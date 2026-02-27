from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from load_simulator.reporting.session_output import build_json_payload


@dataclass
class _AgentResult:
    name: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    start_time: float | None = None
    end_time: float | None = None


@dataclass
class _SessionResult:
    session_id: str
    duration_seconds: float
    agent_results: list[_AgentResult]
    bottlenecks: list[dict[str, Any]]
    preflight: dict[str, dict[str, Any]]
    mode: str = "single"
    adaptive_events: list[dict[str, Any]] = field(default_factory=list)
    breaking_point: dict[str, Any] | None = None


class SessionOutputTests(unittest.TestCase):
    def test_build_json_payload_includes_preflight(self) -> None:
        session = _SessionResult(
            session_id="s1",
            duration_seconds=1.2,
            agent_results=[
                _AgentResult(
                    name="inference",
                    status="success",
                    metrics={"req_per_sec": 10.0},
                    errors=[],
                    start_time=1.0,
                    end_time=2.0,
                )
            ],
            bottlenecks=[],
            preflight={"inference_endpoint": {"ok": True, "detail": "http_status=200"}},
            mode="stress",
            adaptive_events=[{"stage": "stress-1", "action": "CONTINUE"}],
            breaking_point={"stage": "stress-2"},
        )
        payload = build_json_payload(session, exit_code=2)
        self.assertEqual(payload["exit_code"], 2)
        self.assertIn("preflight", payload["summary"])
        self.assertEqual(payload["summary"]["mode"], "stress")
        self.assertEqual(payload["summary"]["adaptive_events"][0]["stage"], "stress-1")
        self.assertEqual(payload["summary"]["breaking_point"]["stage"], "stress-2")
        self.assertEqual(payload["summary"]["preflight"]["inference_endpoint"]["ok"], True)
        self.assertEqual(payload["summary"]["scenarios"][0]["duration_seconds"], 1.0)


if __name__ == "__main__":
    unittest.main()
