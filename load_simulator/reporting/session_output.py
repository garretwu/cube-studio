"""Output payload helpers shared by CLI renderers."""
from __future__ import annotations

from typing import Any


def build_json_payload(session_result: Any, exit_code: int = 0) -> dict[str, Any]:
    """Build machine-readable JSON payload from session result."""
    scenarios = []
    for ar in getattr(session_result, "agent_results", []):
        scenarios.append(
            {
                "name": ar.name,
                "status": ar.status,
                "metrics": ar.metrics,
                "errors": ar.errors,
                "duration_seconds": (
                    (ar.end_time - ar.start_time) if ar.end_time and ar.start_time else None
                ),
            }
        )

    return {
        "exit_code": exit_code,
        "summary": {
            "session_id": getattr(session_result, "session_id", ""),
            "duration_seconds": getattr(session_result, "duration_seconds", 0.0),
            "mode": getattr(session_result, "mode", "single"),
            "scenarios": scenarios,
            "bottlenecks": getattr(session_result, "bottlenecks", []),
            "preflight": getattr(session_result, "preflight", {}),
            "adaptive_events": getattr(session_result, "adaptive_events", []),
            "breaking_point": getattr(session_result, "breaking_point", None),
        },
    }
