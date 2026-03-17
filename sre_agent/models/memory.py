"""Memory-domain models for incidents, patterns, and baselines."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from sre_agent.models.alert import Alert
from sre_agent.models.common import StrictFrozenModel
from sre_agent.models.diagnosis import Hypothesis
from sre_agent.models.remediation import RemediationPlan


class IncidentRecord(StrictFrozenModel):
    """Complete incident record persisted to memory store."""

    incident_id: str = Field(min_length=1)
    aidc_id: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    alert: Alert
    symptoms: list[str] = Field(default_factory=list)
    diagnosis_trace: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: str = Field(min_length=1)
    root_cause_layer: str = Field(min_length=1)
    root_cause_entities: list[str] = Field(default_factory=list)
    hypotheses_tested: list[Hypothesis] = Field(default_factory=list)
    remediation_applied: RemediationPlan | None = None
    outcome: Literal["resolved", "partially_resolved", "failed", "escalated"]
    resolution_time_seconds: int = Field(ge=0)
    engineer_feedback: str | None = None
    tags: list[str] = Field(default_factory=list)


class LearnedPattern(StrictFrozenModel):
    """Pattern extracted from prior incidents."""

    pattern_id: str = Field(min_length=1)
    aidc_id: str = Field(min_length=1)
    symptom_signature: list[str] = Field(default_factory=list)
    root_cause: str = Field(min_length=1)
    effective_fix: str = Field(default="")
    occurrence_count: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    first_seen: datetime
    last_seen: datetime
    example_incidents: list[str] = Field(default_factory=list)
    half_life_days: float = Field(default=90.0, gt=0.0)
    status: Literal["active", "inactive"] = "active"

    @model_validator(mode="after")
    def _time_order_valid(self) -> LearnedPattern:
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen must be greater than or equal to first_seen")
        return self

    def effective_confidence(self, now: datetime | None = None) -> float:
        reference = now if now is not None else datetime.now(UTC)
        days_elapsed = max(0.0, (reference - self.last_seen).total_seconds() / 86400.0)
        decay_factor = 0.5 ** (days_elapsed / self.half_life_days)
        decayed = self.confidence * decay_factor
        return max(0.0, min(1.0, decayed))

    def should_suggest(
        self,
        now: datetime | None = None,
        *,
        min_occurrence: int = 3,
        min_effective_confidence: float = 0.7,
    ) -> bool:
        return (
            self.occurrence_count >= min_occurrence
            and self.effective_confidence(now) >= min_effective_confidence
        )


class ConfigBaseline(StrictFrozenModel):
    """AIDC-specific baseline and threshold memory."""

    aidc_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    metric_baselines: dict[str, float | int | str] = Field(default_factory=dict)
    safety_thresholds: dict[str, float | int | str] = Field(default_factory=dict)
    custom_rules: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_by: str | None = None
    notes: str | None = None


__all__ = [
    "IncidentRecord",
    "LearnedPattern",
    "ConfigBaseline",
]

