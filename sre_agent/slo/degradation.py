"""Four-tier degradation policy for the production phase."""
from __future__ import annotations

from enum import Enum

from sre_agent.slo.metrics import SLOMetricsSnapshot


class DegradationMode(str, Enum):
    FULL = "full"
    DEGRADED = "degraded"
    MINIMAL = "minimal"
    EMERGENCY = "emergency"


class SLODegradationPolicy:
    def __init__(self, window_hours: int = 24) -> None:
        self.window_hours = window_hours
        self.current_mode = DegradationMode.FULL

    def evaluate(self, metrics: SLOMetricsSnapshot | dict[str, float]) -> DegradationMode:
        if isinstance(metrics, dict):
            snapshot = SLOMetricsSnapshot(**metrics)
        else:
            snapshot = metrics

        if snapshot.llm_success_rate < 0.90 or snapshot.false_fix_rate > 0.15:
            self.current_mode = DegradationMode.EMERGENCY
        elif snapshot.diagnosis_success_rate < 0.70 or snapshot.false_fix_rate > 0.10:
            self.current_mode = DegradationMode.MINIMAL
        elif snapshot.diagnosis_success_rate < 0.85 or snapshot.false_fix_rate > 0.05:
            self.current_mode = DegradationMode.DEGRADED
        else:
            self.current_mode = DegradationMode.FULL
        return self.current_mode

    def get_effective_approval_policy(self) -> str:
        return {
            DegradationMode.FULL: "auto_approve",
            DegradationMode.DEGRADED: "human_confirm",
            DegradationMode.MINIMAL: "read_only",
            DegradationMode.EMERGENCY: "disabled",
        }[self.current_mode]
