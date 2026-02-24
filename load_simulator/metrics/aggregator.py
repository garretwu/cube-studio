"""Percentile aggregation utilities (p50 / p95 / p99)."""
from __future__ import annotations

import math
from typing import Sequence


def percentile(data: Sequence[float], pct: float) -> float:
    """Return the *pct*-th percentile of *data* (0–100).

    Uses the nearest-rank method so no external dependencies are needed.

    Args:
        data: Sequence of numeric values.
        pct:  Percentile to compute, e.g. 95 for P95.

    Returns:
        The computed percentile value, or 0.0 if *data* is empty.
    """
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    # Nearest-rank: ceil(pct/100 * n) - 1, clamped to [0, n-1]
    idx = max(0, min(n - 1, math.ceil(pct / 100.0 * n) - 1))
    return sorted_data[idx]


def compute_percentiles(data: Sequence[float]) -> dict[str, float]:
    """Compute p50, p95, and p99 for *data*.

    Args:
        data: Raw latency / duration samples (seconds or milliseconds).

    Returns:
        Dict with keys ``p50``, ``p95``, ``p99``, ``min``, ``max``, ``mean``.
    """
    if not data:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}

    return {
        "p50": percentile(data, 50),
        "p95": percentile(data, 95),
        "p99": percentile(data, 99),
        "min": min(data),
        "max": max(data),
        "mean": sum(data) / len(data),
    }


def compute_rate(count: int, elapsed_seconds: float) -> float:
    """Compute events-per-second rate.

    Args:
        count:           Number of events.
        elapsed_seconds: Elapsed wall-clock time in seconds.

    Returns:
        Rate in events/s, or 0.0 if elapsed is zero.
    """
    if elapsed_seconds <= 0:
        return 0.0
    return count / elapsed_seconds
