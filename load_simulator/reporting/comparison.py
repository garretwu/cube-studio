"""Historical session comparison utilities."""
from __future__ import annotations

from typing import Any


def _to_agent_rows(session: Any) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    for result in getattr(session, "agent_results", []):
        metrics = getattr(result, "metrics", {}) or {}
        rows[result.name] = {k: float(v) for k, v in metrics.items() if isinstance(v, (int, float))}
    return rows


def _to_duration(session: Any) -> float:
    value = getattr(session, "duration_seconds", 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def compare_sessions(current: Any, baseline: Any) -> dict[str, Any]:
    """Compare current session with baseline and return delta summary."""
    curr_rows = _to_agent_rows(current)
    base_rows = _to_agent_rows(baseline)

    comparison: dict[str, Any] = {
        "duration_seconds": {
            "current": _to_duration(current),
            "baseline": _to_duration(baseline),
            "delta": _to_duration(current) - _to_duration(baseline),
        },
        "agents": {},
    }

    all_agents = sorted(set(curr_rows.keys()) | set(base_rows.keys()))
    for agent in all_agents:
        cur = curr_rows.get(agent, {})
        base = base_rows.get(agent, {})
        all_metrics = sorted(set(cur.keys()) | set(base.keys()))
        metric_delta: dict[str, dict[str, float]] = {}
        for name in all_metrics:
            c = float(cur.get(name, 0.0))
            b = float(base.get(name, 0.0))
            metric_delta[name] = {"current": c, "baseline": b, "delta": c - b}
        comparison["agents"][agent] = metric_delta

    return comparison
