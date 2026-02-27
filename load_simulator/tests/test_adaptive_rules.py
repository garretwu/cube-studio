from __future__ import annotations

import unittest

from load_simulator.orchestrator.adaptive import AdaptiveRules, AdaptiveThresholds, evaluate_adaptive_action


class AdaptiveRulesTests(unittest.TestCase):
    def test_record_breaking_on_high_error(self) -> None:
        decision = evaluate_adaptive_action({"error_rate": 0.2}, baseline_p99_ms=100.0)
        self.assertEqual(decision.action, "RECORD_BREAKING_POINT")
        self.assertTrue(decision.record_breaking_point)

    def test_pause_on_high_cpu(self) -> None:
        decision = evaluate_adaptive_action({"cpu_util_pct": 99.0}, baseline_p99_ms=100.0)
        self.assertEqual(decision.action, "PAUSE_RAMP_UP")

    def test_continue_when_healthy(self) -> None:
        decision = evaluate_adaptive_action({"error_rate": 0.0, "latency_p99_ms": 80.0}, baseline_p99_ms=100.0)
        self.assertEqual(decision.action, "CONTINUE")

    def test_custom_thresholds(self) -> None:
        rules = AdaptiveRules(AdaptiveThresholds(breaking_error_rate=0.2))
        decision = rules.evaluate({"error_rate": 0.15}, baseline_p99_ms=100.0)
        self.assertEqual(decision.action, "PAUSE_RAMP_UP")


if __name__ == "__main__":
    unittest.main()
