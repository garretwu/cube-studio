"""Diagnosis-domain shared model contracts."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import AliasChoices, Field, field_validator, model_validator

from sre_agent.models.alert import Alert
from sre_agent.models.common import StrictFrozenModel
from sre_agent.models.remediation import RemediationPlan

ActionType = Literal["tool_call", "conclude", "remediate"]
RootCauseLayer = Literal["hardware", "network", "os", "platform", "service"]
TriagePriority = Literal["P0", "P1", "P2", "P3"]
DiagnosisCertainty = Literal["confirmed", "probable", "ambiguous"]


class RemediationAlertSnapshot(StrictFrozenModel):
    """Alert snapshot captured before or after remediation."""

    fingerprint: str = Field(min_length=1)
    alert_name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    is_firing: bool
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    available: bool = True
    error: str | None = None


class RemediationMetricSnapshot(StrictFrozenModel):
    """Metric sample captured for remediation verification."""

    metric_key: str = Field(min_length=1)
    query: str = Field(min_length=1)
    value: float | int | str | bool | None = None
    condition: dict[str, Any] | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    available: bool = True
    error: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def _sanitize_non_json_float(cls, v: Any) -> Any:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v


class RemediationCheckSnapshot(StrictFrozenModel):
    """Grouped alert + metric evidence at a specific remediation phase."""

    alert: RemediationAlertSnapshot | None = None
    metrics: list[RemediationMetricSnapshot] = Field(default_factory=list)
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RemediationAlertReview(StrictFrozenModel):
    """Comparison of alert status before and after remediation."""

    fingerprint: str = Field(min_length=1)
    alert_name: str = Field(min_length=1)
    before_status: str = Field(min_length=1)
    after_status: str = Field(min_length=1)
    cleared: bool
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RemediationMetricReview(StrictFrozenModel):
    """Comparison of metric values before and after remediation."""

    metric_key: str = Field(min_length=1)
    query: str = Field(min_length=1)
    before_value: float | int | str | bool | None = None
    after_value: float | int | str | bool | None = None
    condition: dict[str, Any] | None = None
    improved: bool
    available: bool
    error: str | None = None
    reviewed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("before_value", "after_value", mode="before")
    @classmethod
    def _sanitize_non_json_float(cls, v: Any) -> Any:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v


class RemediationEvidence(StrictFrozenModel):
    """Persistent before/after evidence for remediation audit and review."""

    pre_check: RemediationCheckSnapshot | None = None
    post_check: RemediationCheckSnapshot | None = None
    alert_review: RemediationAlertReview | None = None
    metric_reviews: list[RemediationMetricReview] = Field(default_factory=list)
    alert_cleared: bool | None = None
    metrics_improved: bool | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ThinkingStep(StrictFrozenModel):
    """Single thought/action step in the diagnosis trace."""

    step: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    thought: str = Field(min_length=1)
    action_type: ActionType
    thought_key: str | None = None
    tool_name: str | None = None
    tool_params: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    next_action: str | None = None
    thought_duration_sec: int | None = Field(default=None, ge=1)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Observation(StrictFrozenModel):
    """Tool observation in the diagnosis trace."""

    tool: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ThinkingTrace(StrictFrozenModel):
    """Ordered chain of Thought/Observation items."""

    steps: list[ThinkingStep | Observation] = Field(default_factory=list)

    def add_step(self, step: ThinkingStep) -> None:
        """Append a thought step in-place to match design-doc usage."""
        self.steps.append(step)

    def add_observation(self, observation: Observation) -> None:
        """Append an observation in-place to match design-doc usage."""
        self.steps.append(observation)

    @classmethod
    def from_langraph_state(cls, trace_dicts: list[dict[str, Any]]) -> ThinkingTrace:
        trace_items: list[ThinkingStep | Observation] = []
        next_step = 1
        for item in trace_dicts:
            if item.get("type") == "thought":
                action = str(item.get("action", "tool_call")).strip().lower()
                # Normalize action to valid ActionType (same logic as graph.py:398)
                normalized_action = action if action in {"tool_call", "conclude", "remediate"} else "tool_call"
                step = ThinkingStep(
                    step=int(item.get("step", next_step)),
                    thought=str(item.get("content", "")),
                    action_type=normalized_action,  # type: ignore[arg-type]
                    thought_key=item.get("thought_key"),
                    tool_name=item.get("tool_name"),
                    tool_params=item.get("tool_params"),
                    confidence=item.get("confidence"),
                    next_action=item.get("next_action"),
                    thought_duration_sec=item.get("thought_duration_sec"),
                )
                trace_items.append(step)
                next_step = step.step + 1
            elif item.get("type") == "observation":
                trace_items.append(
                    Observation(
                        tool=str(item.get("tool", "unknown")),
                        params=dict(item.get("params", {})),
                        result=dict(item.get("result", {})),
                    )
                )
        return cls(steps=trace_items)

    def to_display(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for item in self.steps:
            if isinstance(item, ThinkingStep):
                output.append(
                    {
                        "type": "thought",
                        "step": item.step,
                        "time": item.timestamp.isoformat(),
                        "content": item.thought,
                        "action": item.action_type,
                    }
                )
            else:
                output.append(
                    {
                        "type": "observation",
                        "tool": item.tool,
                        "time": item.timestamp.isoformat(),
                        "result_summary": item.result,
                    }
                )
        return output


class Hypothesis(StrictFrozenModel):
    """Diagnosis hypothesis with validation outcome."""

    description: str = Field(min_length=1)
    status: Literal["testing", "confirmed", "eliminated"]
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class PropagationStep(StrictFrozenModel):
    """One hop in fault propagation analysis."""

    entity_id: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value_before: float | str
    value_after: float | str
    description: str = Field(min_length=1)


class DiagnosedRootCause(StrictFrozenModel):
    """Formal multi-root-cause diagnosis item used as the primary API structure."""

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    title: str = Field(min_length=1, validation_alias=AliasChoices("title", "root_cause"))
    layer: RootCauseLayer = Field(validation_alias=AliasChoices("layer", "root_cause_layer"))
    entities: list[str] = Field(default_factory=list, validation_alias=AliasChoices("entities", "root_cause_entities"))
    confidence: float = Field(ge=0.0, le=1.0)
    certainty: DiagnosisCertainty = "probable"
    status: Literal["confirmed", "contributing", "suspected", "monitoring"] = "suspected"
    evidence_summary: str = Field(min_length=1)
    impact_summary: str = Field(min_length=1)
    distinguishing_verification: str | None = None
    factor_type: Literal["gpu_contention", "external_load", "cache_pressure", "scheduler", "mixed", "unknown"] | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_interpretation: str | None = None
    recommended_fix: RemediationPlan | None = None


class DiagnosisResult(StrictFrozenModel):
    """Diagnosis result with root_cause[] as the single source of truth."""

    root_cause: list[DiagnosedRootCause] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    next_action: str | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    propagation_chain: list[PropagationStep] = Field(default_factory=list)
    impact_summary: str = Field(min_length=1)
    affected_services: list[str] = Field(default_factory=list)
    # Deprecated compatibility field. External payloads should use root_cause[i].recommended_fix only.
    recommended_fix: RemediationPlan | None = Field(default=None, exclude=True)
    triage_priority: TriagePriority
    diagnosis_certainty: DiagnosisCertainty

    @model_validator(mode="after")
    def _validate_consistency(self) -> DiagnosisResult:
        """Validate root-cause primary structure and normalize legacy top-level plan input.

        Purpose:
        - enforce the contract where `root_cause[]` is mandatory and primary;
        - absorb deprecated top-level `recommended_fix` into `root_cause[0].recommended_fix` when needed.
        Input/Output:
        - input: parsed DiagnosisResult fields from model validation;
        - output: validated DiagnosisResult with plan data normalized onto `root_cause[]`.
        Compatibility rationale:
        - this validator intentionally avoids old `ranked_candidates`/single-string semantics because
          the migration has switched to an array-based primary structure.
        Why:
        - avoids exposing or relying on top-level plan while keeping old payload compatibility.
        """
        if not self.root_cause:
            raise ValueError("root_cause must contain at least one item")
        primary = self.root_cause[0]
        if not primary.title.strip():
            raise ValueError("root_cause[0].title must not be blank")
        if self.diagnosis_certainty == "confirmed" and self.confidence < 0.85:
            raise ValueError("confirmed diagnosis requires confidence >= 0.85")
        if self.recommended_fix is not None:
            synchronized_root_causes = list(self.root_cause)
            if primary.recommended_fix is None:
                synchronized_root_causes[0] = primary.model_copy(update={"recommended_fix": self.recommended_fix})
            return self.model_copy(update={"root_cause": synchronized_root_causes, "recommended_fix": None})
        return self


class DiagnosisSession(StrictFrozenModel):
    """Lifecycle model for diagnosis/remediation execution."""

    session_id: str = Field(min_length=1)
    alert: Alert
    status: Literal[
        "diagnosing",
        "diagnosed",
        "approval_required",
        "remediating",
        "rejected",
        "re_diagnosed",
        "resolved",
        "failed",
        "escalated",
        "timeout",
    ]
    diagnosis_result: DiagnosisResult | None = Field(
        default=None,
        validation_alias=AliasChoices("diagnosis_result", "diagnosis"),
    )
    trace: ThinkingTrace | None = None
    re_diagnosis_round: int = Field(default=0, ge=0)
    duration_seconds: int = Field(default=0, ge=0)
    outcome: str | None = None
    remediation_evidence: RemediationEvidence | None = None

    @field_validator("session_id")
    @classmethod
    def _session_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be blank")
        return value

    @classmethod
    def create(cls, alert: Alert) -> DiagnosisSession:
        return cls(
            session_id=uuid4().hex,
            alert=alert,
            status="diagnosing",
            diagnosis_result=None,
            trace=None,
        )

    @property
    def diagnosis(self) -> DiagnosisResult | None:
        """Compatibility alias for older snippets that access `session.diagnosis`."""
        return self.diagnosis_result

    @property
    def proposed_plan(self) -> RemediationPlan | None:
        """Compatibility alias for older snippets that access `session.proposed_plan`."""
        if self.diagnosis_result is None:
            return None
        for item in self.diagnosis_result.root_cause:
            if item.recommended_fix is not None:
                return item.recommended_fix
        return None


__all__ = [
    "ThinkingStep",
    "Observation",
    "ThinkingTrace",
    "Hypothesis",
    "PropagationStep",
    "DiagnosedRootCause",
    "DiagnosisResult",
    "DiagnosisSession",
    "RemediationAlertSnapshot",
    "RemediationMetricSnapshot",
    "RemediationCheckSnapshot",
    "RemediationAlertReview",
    "RemediationMetricReview",
    "RemediationEvidence",
]
