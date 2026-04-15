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
                step = ThinkingStep(
                    step=int(item.get("step", next_step)),
                    thought=str(item.get("content", "")),
                    action_type=str(item.get("action", "tool_call")),  # type: ignore[arg-type]
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


class RankedRootCause(StrictFrozenModel):
    """Ranked candidate root cause."""

    rank: int = Field(ge=1)
    root_cause: str = Field(min_length=1)
    root_cause_layer: RootCauseLayer
    root_cause_entities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_summary: str = Field(min_length=1)
    recommended_fix: RemediationPlan | None = None
    distinguishing_verification: str | None = None


class DiagnosisResult(StrictFrozenModel):
    """Diagnosis result supporting both single and ranked-candidate modes."""

    root_cause: str = Field(min_length=1)
    root_cause_layer: RootCauseLayer
    root_cause_entities: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    propagation_chain: list[PropagationStep] = Field(default_factory=list)
    impact_summary: str = Field(min_length=1)
    affected_services: list[str] = Field(default_factory=list)
    recommended_fix: RemediationPlan | None = None
    triage_priority: TriagePriority
    ranked_candidates: list[RankedRootCause] = Field(default_factory=list)
    diagnosis_certainty: DiagnosisCertainty

    @model_validator(mode="after")
    def _validate_consistency(self) -> DiagnosisResult:
        if self.ranked_candidates:
            if self.ranked_candidates[0].root_cause != self.root_cause:
                raise ValueError("ranked_candidates[0] must match root_cause")
            ranks = [candidate.rank for candidate in self.ranked_candidates]
            if ranks != sorted(ranks):
                raise ValueError("ranked_candidates must be ordered by rank")
        if self.diagnosis_certainty == "confirmed" and self.confidence < 0.85:
            raise ValueError("confirmed diagnosis requires confidence >= 0.85")
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
        return self.diagnosis_result.recommended_fix


__all__ = [
    "ThinkingStep",
    "Observation",
    "ThinkingTrace",
    "Hypothesis",
    "PropagationStep",
    "RankedRootCause",
    "DiagnosisResult",
    "DiagnosisSession",
    "RemediationAlertSnapshot",
    "RemediationMetricSnapshot",
    "RemediationCheckSnapshot",
    "RemediationAlertReview",
    "RemediationMetricReview",
    "RemediationEvidence",
]
