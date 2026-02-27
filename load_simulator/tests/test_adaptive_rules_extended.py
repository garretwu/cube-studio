"""Extended tests for load_simulator.orchestrator.adaptive — edge cases not covered by test_adaptive_rules.py."""
from __future__ import annotations

import unittest

from load_simulator.orchestrator.adaptive import (
    AdaptiveDecision,
    AdaptiveRules,
    AdaptiveThresholds,
    evaluate_adaptive_action,
)


class P99BreakingPointTests(unittest.TestCase):
    def test_p99_over_multiplier_records_breaking_point(self):
        rules = AdaptiveRules()
        metrics = {"latency_p99_ms": 600.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics, baseline_p99_ms=50.0)
        # 600 > 50 * 10 = 500 → RECORD_BREAKING_POINT
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")
        self.assertTrue(decision.record_breaking_point)

    def test_p99_under_multiplier_continues(self):
        rules = AdaptiveRules()
        metrics = {"latency_p99_ms": 400.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics, baseline_p99_ms=50.0)
        # 400 < 50 * 10 = 500 → not a breaking point
        self.assertNotEqual(decision.action, "RECORD_BREAKING_POINT")

    def test_p99_with_zero_baseline_ignored(self):
        rules = AdaptiveRules()
        metrics = {"latency_p99_ms": 1000.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics, baseline_p99_ms=0.0)
        # baseline=0 → p99 check skipped
        self.assertEqual(decision.action, "CONTINUE")

    def test_p99_with_none_baseline_ignored(self):
        rules = AdaptiveRules()
        metrics = {"latency_p99_ms": 1000.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics, baseline_p99_ms=None)
        self.assertEqual(decision.action, "CONTINUE")


class GpuMemReduceTests(unittest.TestCase):
    def test_gpu_mem_over_threshold_reduces(self):
        rules = AdaptiveRules()
        metrics = {"gpu_mem_util_pct": 96.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics)
        self.assertEqual(decision.action, "REDUCE_INFERENCE_CONCURRENCY")
        self.assertFalse(decision.record_breaking_point)

    def test_gpu_mem_under_threshold_continues(self):
        rules = AdaptiveRules()
        metrics = {"gpu_mem_util_pct": 90.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics)
        self.assertEqual(decision.action, "CONTINUE")


class PriorityOrderTests(unittest.TestCase):
    """Verify that higher-priority rules win over lower-priority ones."""

    def test_error_rate_breaking_takes_priority_over_gpu_mem(self):
        rules = AdaptiveRules()
        metrics = {"error_rate": 0.15, "gpu_mem_util_pct": 96.0}
        decision = rules.evaluate(metrics)
        # error_rate > 10% is checked first
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")

    def test_error_rate_pause_vs_cpu_pause(self):
        rules = AdaptiveRules()
        # Both conditions met: cpu > 95 AND error_rate > 5%
        # GPU mem check comes first in evaluate(), then CPU, then error_rate pause
        metrics = {"error_rate": 0.07, "cpu_util_pct": 96.0}
        decision = rules.evaluate(metrics)
        # CPU check (95%) is evaluated before error_rate pause (5%)
        self.assertEqual(decision.action, "PAUSE_RAMP_UP")


class CompatibilityWrapperTests(unittest.TestCase):
    def test_evaluate_adaptive_action_uses_defaults(self):
        metrics = {"error_rate": 0.15}
        decision = evaluate_adaptive_action(metrics)
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")

    def test_evaluate_adaptive_action_healthy(self):
        decision = evaluate_adaptive_action({"error_rate": 0.0})
        self.assertEqual(decision.action, "CONTINUE")


class CustomThresholdsEdgeCasesTests(unittest.TestCase):
    def test_very_high_break_threshold(self):
        thresholds = AdaptiveThresholds(breaking_error_rate=0.99)
        rules = AdaptiveRules(thresholds)
        metrics = {"error_rate": 0.50}
        decision = rules.evaluate(metrics)
        # 50% < 99% so not breaking, but 50% > 5% pause
        self.assertEqual(decision.action, "PAUSE_RAMP_UP")

    def test_metric_key_alias_p99_latency_ms(self):
        """The adaptive rules check both latency_p99_ms and p99_latency_ms."""
        rules = AdaptiveRules()
        metrics = {"p99_latency_ms": 600.0, "error_rate": 0.0}
        decision = rules.evaluate(metrics, baseline_p99_ms=50.0)
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")


class OOMKilledRuleTests(unittest.TestCase):
    """Tests for the OOMKilled adaptive rule."""

    def test_oomkilled_triggers_breaking_point(self):
        rules = AdaptiveRules()
        metrics = {"error_rate": 0.0, "oom_killed_count": 1}
        decision = rules.evaluate(metrics)
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")
        self.assertTrue(decision.record_breaking_point)
        self.assertIn("oom_killed_count", decision.reason)

    def test_zero_oomkilled_is_safe(self):
        rules = AdaptiveRules()
        metrics = {"error_rate": 0.0, "oom_killed_count": 0}
        decision = rules.evaluate(metrics)
        self.assertEqual(decision.action, "CONTINUE")

    def test_oomkilled_priority_vs_error_rate(self):
        """Error rate breaking check happens before OOMKilled."""
        rules = AdaptiveRules()
        metrics = {"error_rate": 0.15, "oom_killed_count": 2}
        decision = rules.evaluate(metrics)
        # error_rate > 10% is checked first
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")
        self.assertIn("error_rate", decision.reason)

    def test_oomkilled_priority_vs_gpu_mem(self):
        """OOMKilled is checked before gpu_mem."""
        rules = AdaptiveRules()
        metrics = {"error_rate": 0.0, "oom_killed_count": 1, "gpu_mem_util_pct": 96.0}
        decision = rules.evaluate(metrics)
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")
        self.assertIn("oom_killed_count", decision.reason)


if __name__ == "__main__":
    unittest.main()
