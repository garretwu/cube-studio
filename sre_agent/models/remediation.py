"""Remediation-domain models for shared SRE contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import AliasChoices, Field, model_validator

from sre_agent.models.common import SafetyLevel, StrictFrozenModel

if TYPE_CHECKING:
    from sre_agent.models.diagnosis import RankedRootCause

Operator = Literal["<", "<=", ">", ">=", "==", "!="]
VerificationMethod = Literal["promql", "tool_call", "wait"]
PlanPriority = Literal["P0", "P1", "P2"]
LoopOutcome = Literal[
    "resolved",
    "partially_resolved",
    "exhausted",
    "escalated",
    "re_diagnosed",
]


class VerificationCondition(StrictFrozenModel):
    """Structured condition DSL used by remediation verification."""

    field: str = Field(min_length=1)
    operator: Operator
    value: float | int | str


class VerificationConfig(StrictFrozenModel):
    """Verification config for a remediation action."""

    method: VerificationMethod
    query: str | None = None
    tool: str | None = None
    tool_params: dict[str, Any] | None = None
    condition: VerificationCondition | None = None
    wait_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def _validate_method_shape(self) -> VerificationConfig:
        if self.method == "promql" and not self.query:
            raise ValueError("method=promql requires query")
        if self.method == "tool_call" and not self.tool:
            raise ValueError("method=tool_call requires tool")
        return self


class CanaryCondition(StrictFrozenModel):
    """Structured canary check condition."""

    metric: str = Field(min_length=1)
    field: str = "value"
    operator: Operator
    value: float | int | str


class CanaryConfig(StrictFrozenModel):
    """Canary rollout verification configuration."""

    enabled: bool = True
    target_percentage: float = Field(default=0.1, gt=0.0, le=1.0)
    monitor_duration: int = Field(default=120, ge=1)
    success_criteria: list[CanaryCondition] = Field(default_factory=list)
    criteria_mode: Literal["all", "any"] = "all"
    max_batches: int = Field(default=3, ge=1)
    auto_rollback_on_regression: bool = True
    progressive: bool = True

    @model_validator(mode="after")
    def _enabled_requires_criteria(self) -> CanaryConfig:
        if self.enabled and not self.success_criteria:
            raise ValueError("enabled canary requires at least one success criterion")
        return self


class RemediationAction(StrictFrozenModel):
    """Single executable remediation action step."""

    step_id: int = Field(ge=1)
    description: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    command: str | None = None
    rollback_tool: str | None = None
    rollback_params: dict[str, Any] | None = None
    verification: VerificationConfig
    timeout: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def _rollback_fields_consistent(self) -> RemediationAction:
        if self.rollback_tool is None and self.rollback_params is not None:
            raise ValueError("rollback_params provided without rollback_tool")
        return self


# Compatibility alias: legacy name in design docs.
RemediationStep = RemediationAction


class RemediationPlan(StrictFrozenModel):
    """Structured remediation plan returned by the diagnosis subsystem."""

    plan_id: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    description: str = Field(min_length=1)
    steps: list[RemediationAction] = Field(
        default_factory=list,
        validation_alias=AliasChoices("steps", "actions"),
    )
    canary: CanaryConfig | None = None
    estimated_impact: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    priority: PlanPriority
    safety_level: SafetyLevel = SafetyLevel.HIGH

    @model_validator(mode="after")
    def _must_have_steps(self) -> RemediationPlan:
        if not self.steps:
            raise ValueError("remediation plan must have at least one step")
        return self


class RemediationResult(StrictFrozenModel):
    """Execution result of a remediation plan."""

    plan_id: str = Field(min_length=1)
    success: bool
    steps_completed: int = Field(ge=0)
    steps_total: int = Field(ge=0)
    failed_step: RemediationAction | None = None
    rolled_back: bool = False
    verification_results: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: int = Field(default=0, ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def _validate_step_progress(self) -> RemediationResult:
        if self.steps_completed > self.steps_total:
            raise ValueError("steps_completed cannot exceed steps_total")
        if self.success and self.error is not None:
            raise ValueError("successful remediation result cannot contain error")
        if self.success and self.steps_total > 0 and self.steps_completed != self.steps_total:
            raise ValueError("successful remediation result must complete all steps")
        return self


class CandidateAttempt(StrictFrozenModel):
    """Single candidate root-cause remediation attempt in loop orchestration."""

    candidate: RankedRootCause
    remediation_result: RemediationResult
    verification_passed: bool
    rolled_back: bool
    observations: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: int = Field(ge=0)


class LoopResult(StrictFrozenModel):
    """LoopOrchestrator summary result."""

    session_id: str = Field(min_length=1)
    outcome: LoopOutcome
    winning_candidate: RankedRootCause | None = None
    attempts: list[CandidateAttempt] = Field(default_factory=list)
    total_duration_seconds: int = Field(default=0, ge=0)
    re_diagnosis_context: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("re_diagnosis_context", "re_diag_context"),
    )

    @model_validator(mode="after")
    def _validate_outcome_candidate(self) -> LoopResult:
        if self.outcome == "resolved" and self.winning_candidate is None:
            raise ValueError("resolved outcome requires winning_candidate")
        if self.outcome in {"exhausted", "escalated"} and self.winning_candidate is not None:
            raise ValueError(f"{self.outcome} outcome must not include winning_candidate")
        return self


def rebuild_remediation_models(ranked_root_cause_type: type[Any]) -> None:
    """Resolve forward refs for RankedRootCause without introducing import cycles."""
    namespace = {"RankedRootCause": ranked_root_cause_type}
    CandidateAttempt.model_rebuild(_types_namespace=namespace, force=True)
    LoopResult.model_rebuild(_types_namespace=namespace, force=True)


__all__ = [
    "VerificationCondition",
    "VerificationConfig",
    "CanaryCondition",
    "CanaryConfig",
    "RemediationAction",
    "RemediationStep",
    "RemediationPlan",
    "RemediationResult",
    "CandidateAttempt",
    "LoopResult",
    "rebuild_remediation_models",
]
