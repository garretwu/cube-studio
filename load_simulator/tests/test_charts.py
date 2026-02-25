"""Tests for load_simulator.reporting.charts."""
from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Any

from load_simulator.reporting.charts import (
    PLOTLY_CDN,
    extract_agent_metric_matrix,
    render_bottleneck_heatmap,
    render_bottleneck_table,
    render_error_rate_timeline,
    render_latency_timeline,
    render_metric_table,
    render_resource_utilization,
    render_stage_comparison,
    render_throughput_timeline,
)


@dataclass
class _FakeAgentResult:
    name: str
    status: str
    metrics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class ExtractAgentMetricMatrixTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(extract_agent_metric_matrix([]), {})

    def test_numeric_only(self):
        results = [
            _FakeAgentResult("inference", "success", {"latency_p50_ms": 10.0, "model": "gpt", "count": 5}),
            _FakeAgentResult("pipeline", "success", {"jobs": 3, "label": "test"}),
        ]
        matrix = extract_agent_metric_matrix(results)
        self.assertIn("inference", matrix)
        self.assertIn("pipeline", matrix)
        # Non-numeric should be excluded
        self.assertNotIn("model", matrix["inference"])
        self.assertNotIn("label", matrix["pipeline"])
        # Numeric should be present
        self.assertEqual(matrix["inference"]["latency_p50_ms"], 10.0)
        self.assertEqual(matrix["inference"]["count"], 5.0)
        self.assertEqual(matrix["pipeline"]["jobs"], 3.0)


class RenderMetricTableTests(unittest.TestCase):
    def test_empty_matrix(self):
        html = render_metric_table("Test", {})
        self.assertIn("No numeric metrics", html)

    def test_with_data(self):
        matrix = {"inference": {"latency": 10.5, "throughput": 100.0}}
        html = render_metric_table("Metrics", matrix)
        self.assertIn("<table>", html)
        self.assertIn("inference", html)
        self.assertIn("latency", html)
        self.assertIn("Metrics", html)


class RenderBottleneckTableTests(unittest.TestCase):
    def test_empty_findings(self):
        html = render_bottleneck_table([])
        self.assertIn("No bottlenecks detected", html)

    def test_with_findings(self):
        findings = [
            {"severity": "critical", "layer": "GPU_COMPUTE", "metric": "gpu_util_pct",
             "value": 96.0, "threshold": 95.0},
        ]
        html = render_bottleneck_table(findings)
        self.assertIn("<table>", html)
        self.assertIn("critical", html)
        self.assertIn("GPU_COMPUTE", html)


class RenderLatencyTimelineTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_latency_timeline([])
        self.assertIn("No data available", html)

    def test_with_request_logs(self):
        ar = _FakeAgentResult("inference", "success", raw={
            "request_logs": [
                {"timestamp": 100.0, "latency_ms": 50.0, "error_message": None},
                {"timestamp": 100.5, "latency_ms": 60.0, "error_message": None},
            ]
        })
        html = render_latency_timeline([ar])
        self.assertIn("plotly", html.lower())
        self.assertIn("Plotly.newPlot", html)
        self.assertIn(PLOTLY_CDN, html)


class RenderThroughputTimelineTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_throughput_timeline([])
        self.assertIn("No data available", html)

    def test_with_data(self):
        ar = _FakeAgentResult("inference", "success", raw={
            "request_logs": [
                {"timestamp": 100.0, "latency_ms": 50.0},
                {"timestamp": 100.1, "latency_ms": 60.0},
                {"timestamp": 105.0, "latency_ms": 55.0},
            ]
        })
        html = render_throughput_timeline([ar])
        self.assertIn("Plotly.newPlot", html)


class RenderErrorRateTimelineTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_error_rate_timeline([])
        self.assertIn("No data available", html)

    def test_with_errors(self):
        ar = _FakeAgentResult("inference", "success", raw={
            "request_logs": [
                {"timestamp": 100.0, "error_message": "fail"},
                {"timestamp": 100.5, "error_message": None},
            ]
        })
        html = render_error_rate_timeline([ar])
        self.assertIn("Plotly.newPlot", html)


class RenderResourceUtilizationTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_resource_utilization([])
        self.assertIn("No data available", html)

    def test_with_snapshots(self):
        snapshots = [
            {"timestamp": 100.0, "cpu_util_pct": 50.0, "mem_util_pct": 60.0},
            {"timestamp": 105.0, "cpu_util_pct": 55.0, "mem_util_pct": 65.0},
        ]
        html = render_resource_utilization(snapshots)
        self.assertIn("Plotly.newPlot", html)
        self.assertIn("CPU", html)


class RenderBottleneckHeatmapTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_bottleneck_heatmap([])
        self.assertIn("No data available", html)

    def test_with_findings(self):
        findings = [
            {"severity": "critical", "layer": "GPU_COMPUTE", "metric": "gpu_util_pct"},
            {"severity": "warning", "layer": "NETWORK", "metric": "latency_ms"},
        ]
        html = render_bottleneck_heatmap(findings)
        self.assertIn("Plotly.newPlot", html)
        self.assertIn("heatmap", html)


class RenderStageComparisonTests(unittest.TestCase):
    def test_empty_data(self):
        html = render_stage_comparison([])
        self.assertIn("No data available", html)

    def test_with_events(self):
        events = [
            {"stage": "baseline", "metrics": {"error_rate": 0.01, "latency_p99_ms": 100}},
            {"stage": "ramp-1", "metrics": {"error_rate": 0.05, "latency_p99_ms": 200}},
        ]
        html = render_stage_comparison(events)
        self.assertIn("Plotly.newPlot", html)
        self.assertIn("baseline", html)


if __name__ == "__main__":
    unittest.main()
