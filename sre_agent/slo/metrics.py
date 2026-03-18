"""SLO metric snapshot contracts."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SLOMetricsSnapshot:
    diagnosis_success_rate: float = 1.0
    false_fix_rate: float = 0.0
    llm_success_rate: float = 1.0
    mttr_seconds_p95: float = 0.0
    diagnosis_seconds_p95: float = 0.0
    approval_timeout_rate: float = 0.0
