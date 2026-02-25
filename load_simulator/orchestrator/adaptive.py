"""Deterministic adaptive rules for load orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AdaptiveDecision:
    action: str
    reason: str
    record_breaking_point: bool = False


@dataclass
class AdaptiveThresholds:
    pause_error_rate: float = 0.05
    breaking_error_rate: float = 0.10
    p99_breaking_multiplier: float = 10.0
    gpu_mem_reduce_pct: float = 95.0
    cpu_pause_pct: float = 95.0


class AdaptiveRules:
    """Deterministic rule engine used by stress/soak modes."""

    def __init__(self, thresholds: AdaptiveThresholds | None = None) -> None:
        self.thresholds = thresholds or AdaptiveThresholds()

    def evaluate(self, metrics: dict[str, Any], baseline_p99_ms: float | None = None) -> AdaptiveDecision:
        t = self.thresholds
        error_rate = float(metrics.get("error_rate", 0.0) or 0.0)
        p99_ms = float(metrics.get("latency_p99_ms", metrics.get("p99_latency_ms", 0.0)) or 0.0)
        cpu_util = float(metrics.get("cpu_util_pct", 0.0) or 0.0)
        gpu_mem = float(metrics.get("gpu_mem_util_pct", 0.0) or 0.0)

        if error_rate > t.breaking_error_rate:
            return AdaptiveDecision(
                action="RECORD_BREAKING_POINT",
                reason=f"error_rate={error_rate:.3f} > {t.breaking_error_rate:.2f}",
                record_breaking_point=True,
            )
        if baseline_p99_ms and baseline_p99_ms > 0 and p99_ms > baseline_p99_ms * t.p99_breaking_multiplier:
            return AdaptiveDecision(
                action="RECORD_BREAKING_POINT",
                reason=(
                    f"p99={p99_ms:.2f}ms > {t.p99_breaking_multiplier:.1f}x "
                    f"baseline={baseline_p99_ms:.2f}ms"
                ),
                record_breaking_point=True,
            )
        oom_killed_count = int(metrics.get("oom_killed_count", 0) or 0)
        if oom_killed_count > 0:
            return AdaptiveDecision(
                action="RECORD_BREAKING_POINT",
                reason=f"oom_killed_count={oom_killed_count}",
                record_breaking_point=True,
            )
        if gpu_mem > t.gpu_mem_reduce_pct:
            return AdaptiveDecision(
                action="REDUCE_INFERENCE_CONCURRENCY",
                reason=f"gpu_mem_util_pct={gpu_mem:.2f} > {t.gpu_mem_reduce_pct:.1f}%",
            )
        if cpu_util > t.cpu_pause_pct:
            return AdaptiveDecision(
                action="PAUSE_RAMP_UP",
                reason=f"cpu_util_pct={cpu_util:.2f} > {t.cpu_pause_pct:.1f}%",
            )
        if error_rate > t.pause_error_rate:
            return AdaptiveDecision(
                action="PAUSE_RAMP_UP",
                reason=f"error_rate={error_rate:.3f} > {t.pause_error_rate:.2f}",
            )
        return AdaptiveDecision(action="CONTINUE", reason="within thresholds")


def evaluate_adaptive_action(metrics: dict[str, Any], baseline_p99_ms: float | None = None) -> AdaptiveDecision:
    """Compatibility wrapper."""
    return AdaptiveRules().evaluate(metrics, baseline_p99_ms=baseline_p99_ms)
