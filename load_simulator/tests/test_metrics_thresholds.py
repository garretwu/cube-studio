"""Tests for load_simulator.metrics.thresholds."""
from __future__ import annotations

import unittest

from load_simulator.metrics.thresholds import (
    BOTTLENECK_THRESHOLDS,
    check_bottlenecks,
    get_layer_summary,
)


class CheckBottlenecksTests(unittest.TestCase):
    def test_no_metrics_returns_empty(self):
        self.assertEqual(check_bottlenecks({}), [])

    def test_all_healthy_returns_empty(self):
        metrics = {"cpu_util_pct": 30.0, "gpu_util_pct": 50.0, "mem_util_pct": 40.0}
        self.assertEqual(check_bottlenecks(metrics), [])

    def test_warning_threshold(self):
        metrics = {"cpu_util_pct": 80.0}  # warning=75, critical=90
        findings = check_bottlenecks(metrics)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertEqual(findings[0]["layer"], "CPU_SYSTEM")
        self.assertEqual(findings[0]["metric"], "cpu_util_pct")

    def test_critical_threshold(self):
        metrics = {"gpu_util_pct": 96.0}  # warning=85, critical=95
        findings = check_bottlenecks(metrics)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertEqual(findings[0]["layer"], "GPU_COMPUTE")

    def test_multiple_findings_sorted_critical_first(self):
        metrics = {
            "cpu_util_pct": 80.0,   # warning
            "gpu_util_pct": 96.0,   # critical
        }
        findings = check_bottlenecks(metrics)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertEqual(findings[1]["severity"], "warning")

    def test_nvlink_zero_thresholds_skipped(self):
        # NVLink bandwidth has warning=0.0 and critical=0.0 — should be skipped
        metrics = {"nvlink_bw_gbps": 100.0}
        findings = check_bottlenecks(metrics)
        self.assertEqual(len(findings), 0)

    def test_disk_io_thresholds(self):
        metrics = {"disk_util_pct": 92.0, "io_wait_pct": 45.0}
        findings = check_bottlenecks(metrics)
        layers = [f["layer"] for f in findings]
        self.assertTrue(all(l == "STORAGE_IO" for l in layers))
        severities = [f["severity"] for f in findings]
        self.assertIn("critical", severities)


class GetLayerSummaryTests(unittest.TestCase):
    def test_empty_findings(self):
        self.assertEqual(get_layer_summary([]), {})

    def test_worst_severity_wins(self):
        findings = [
            {"layer": "GPU_COMPUTE", "severity": "warning"},
            {"layer": "GPU_COMPUTE", "severity": "critical"},
            {"layer": "CPU_SYSTEM", "severity": "warning"},
        ]
        summary = get_layer_summary(findings)
        self.assertEqual(summary["GPU_COMPUTE"], "critical")
        self.assertEqual(summary["CPU_SYSTEM"], "warning")


class ThresholdRulesIntegrityTests(unittest.TestCase):
    def test_all_rules_have_required_fields(self):
        for rule in BOTTLENECK_THRESHOLDS:
            self.assertIsInstance(rule.layer, str)
            self.assertIsInstance(rule.metric, str)
            self.assertIsInstance(rule.warning, float)
            self.assertIsInstance(rule.critical, float)
            self.assertIsInstance(rule.unit, str)
            self.assertIsInstance(rule.description, str)

    def test_six_layers_present(self):
        layers = {r.layer for r in BOTTLENECK_THRESHOLDS}
        expected = {"GPU_COMPUTE", "GPU_MEMORY", "NVLINK_PCIE", "NETWORK_RDMA", "STORAGE_IO", "CPU_SYSTEM"}
        self.assertEqual(layers, expected)


if __name__ == "__main__":
    unittest.main()
