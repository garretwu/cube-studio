"""Four-tier degradation policy for the production phase."""
from __future__ import annotations

from enum import Enum

from sre_agent.slo.metrics import SLOMetricsSnapshot


class DegradationMode(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    PROTECTED = "protected"
    CIRCUIT_BREAK = "circuit_break"

    FULL = NORMAL
    DEGRADED = WARNING
    MINIMAL = PROTECTED
    EMERGENCY = CIRCUIT_BREAK


class SLODegradationPolicy:
    def __init__(self, window_hours: int = 24, auto_recovery_hours: int = 1) -> None:
        self.window_hours = window_hours
        self.auto_recovery_hours = auto_recovery_hours
        self.current_mode = DegradationMode.NORMAL

    def evaluate(self, metrics: SLOMetricsSnapshot | dict[str, float]) -> DegradationMode:
        if isinstance(metrics, dict):
            snapshot = SLOMetricsSnapshot(**metrics)
        else:
            snapshot = metrics

        if snapshot.llm_success_rate < 0.90 or snapshot.false_fix_rate > 0.15:
            self.current_mode = DegradationMode.CIRCUIT_BREAK
        elif snapshot.diagnosis_success_rate < 0.70 or snapshot.false_fix_rate > 0.10:
            self.current_mode = DegradationMode.PROTECTED
        elif snapshot.diagnosis_success_rate < 0.85 or snapshot.false_fix_rate > 0.05:
            self.current_mode = DegradationMode.WARNING
        else:
            self.current_mode = DegradationMode.NORMAL
        return self.current_mode

    def get_effective_approval_policy(self) -> str:
        return {
            DegradationMode.NORMAL: "auto_approve",
            DegradationMode.WARNING: "human_confirm",
            DegradationMode.PROTECTED: "read_only",
            DegradationMode.CIRCUIT_BREAK: "disabled",
        }[self.current_mode]

    def can_auto_recover(self, healthy_hours: int) -> bool:
        if self.current_mode == DegradationMode.CIRCUIT_BREAK:
            return False
        if self.current_mode == DegradationMode.PROTECTED:
            return healthy_hours >= max(2, self.auto_recovery_hours)
        if self.current_mode == DegradationMode.WARNING:
            return healthy_hours >= self.auto_recovery_hours
        return True

    def reset(self) -> None:
        self.current_mode = DegradationMode.NORMAL
