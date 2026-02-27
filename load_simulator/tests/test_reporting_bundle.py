from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from load_simulator.reporting.bundle import write_report_bundle


@dataclass
class _AgentResult:
    name: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    start_time: float | None = 1.0
    end_time: float | None = 2.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        return float((self.end_time or 0) - (self.start_time or 0))


@dataclass
class _SessionResult:
    session_id: str = "s-bundle"
    duration_seconds: float = 1.2
    mode: str = "stress"
    agent_results: list[_AgentResult] = field(default_factory=lambda: [_AgentResult("inference", "success", {"req_per_sec": 10})])
    bottlenecks: list[dict[str, Any]] = field(default_factory=list)
    summary: str = "ok"
    preflight: dict[str, Any] = field(default_factory=dict)
    adaptive_events: list[dict[str, Any]] = field(default_factory=list)
    breaking_point: dict[str, Any] | None = None
    system_metrics_series: list[dict[str, Any]] = field(default_factory=list)


class ReportingBundleTests(unittest.TestCase):
    def test_write_report_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            report = write_report_bundle(_SessionResult(), output_dir=output_dir)
            self.assertTrue(report.exists())
            base = output_dir / "sessions" / "s-bundle"
            self.assertTrue((base / "report" / "report.json").exists())
            self.assertTrue((base / "report" / "metrics-raw.csv").exists())
            self.assertTrue((base / "report" / "charts" / "stage-comparison.html").exists())
            self.assertTrue((base / "session.json").exists())
            self.assertTrue((base / "events.jsonl").exists())

    def test_request_logs_written(self) -> None:
        logs = [
            {"timestamp": 1.0, "method": "POST", "url": "http://x", "status_code": 200,
             "latency_ms": 10.5, "error_message": None},
        ]
        ar = _AgentResult("inference", "success", {"req_per_sec": 10}, raw={"request_logs": logs})
        sr = _SessionResult(agent_results=[ar])
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_report_bundle(sr, output_dir=output_dir)
            base = output_dir / "sessions" / "s-bundle"
            logs_dir = base / "request_logs"
            self.assertTrue(logs_dir.exists())
            inference_log = logs_dir / "inference.jsonl"
            self.assertTrue(inference_log.exists())
            lines = inference_log.read_text().strip().split("\n")
            self.assertEqual(len(lines), 1)

    def test_no_request_logs_dir_when_empty(self) -> None:
        """No request_logs dir should be created when agents have no logs."""
        ar = _AgentResult("inference", "success", {"req_per_sec": 10})
        sr = _SessionResult(agent_results=[ar])
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            write_report_bundle(sr, output_dir=output_dir)
            base = output_dir / "sessions" / "s-bundle"
            self.assertFalse((base / "request_logs").exists())


if __name__ == "__main__":
    unittest.main()
