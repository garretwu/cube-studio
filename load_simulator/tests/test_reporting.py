from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from load_simulator.reporting.comparison import compare_sessions
from load_simulator.reporting.html_report import generate_html_report


@dataclass
class _AgentResult:
    name: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    start_time: float | None = None
    end_time: float | None = None

    @property
    def duration_seconds(self) -> float:
        if self.start_time is None or self.end_time is None:
            return 0.0
        return self.end_time - self.start_time


@dataclass
class _SessionResult:
    session_id: str
    duration_seconds: float
    agent_results: list[_AgentResult]
    bottlenecks: list[dict[str, Any]]
    summary: str = ""
    preflight: dict[str, dict[str, Any]] = field(default_factory=dict)
    adaptive_events: list[dict[str, Any]] = field(default_factory=list)
    breaking_point: dict[str, Any] | None = None


class ReportingTests(unittest.TestCase):
    def _session(self, duration: float, req_rate: float) -> _SessionResult:
        agent = _AgentResult(
            name="inference",
            status="success",
            metrics={"req_per_sec": req_rate, "error_rate": 0.01},
            start_time=1.0,
            end_time=2.0,
        )
        return _SessionResult(
            session_id="s1",
            duration_seconds=duration,
            agent_results=[agent],
            bottlenecks=[{"severity": "warning", "layer": "CPU_SYSTEM", "metric": "cpu_util_pct", "value": 85}],
            summary="ok",
            preflight={
                "inference_endpoint": {"ok": True, "detail": "http_status=200"},
                "cube_studio_pipeline": {"ok": False, "detail": "connection refused"},
            },
            adaptive_events=[{"stage": "stress-1", "action": "CONTINUE", "reason": "ok"}],
            breaking_point={"stage": "stress-2", "concurrency_scale": 1.5, "reason": "error_rate high"},
        )

    def test_generate_html_report(self) -> None:
        html = generate_html_report(self._session(duration=10.0, req_rate=100.0))
        self.assertIn("Load Simulator Report", html)
        self.assertIn("Agent Summary", html)
        self.assertIn("Bottleneck Analysis", html)
        self.assertIn("Preflight", html)
        self.assertIn("inference_endpoint", html)
        self.assertIn("preflight-fail", html)
        self.assertIn("Adaptive Rules", html)
        self.assertIn("Breaking point", html)

    def test_compare_sessions(self) -> None:
        current = self._session(duration=12.0, req_rate=120.0)
        baseline = self._session(duration=10.0, req_rate=100.0)
        result = compare_sessions(current, baseline)
        self.assertAlmostEqual(result["duration_seconds"]["delta"], 2.0)
        self.assertAlmostEqual(
            result["agents"]["inference"]["req_per_sec"]["delta"],
            20.0,
        )


if __name__ == "__main__":
    unittest.main()
