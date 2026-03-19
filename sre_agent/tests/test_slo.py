from __future__ import annotations

from sre_agent.slo.degradation import DegradationMode, SLODegradationPolicy
from sre_agent.slo.metrics import SLOMetricsSnapshot


class TestSLOUnit:
    def test_unit_maps_thresholds_to_document_modes_when_metrics_cross_boundaries(self) -> None:
        policy = SLODegradationPolicy(auto_recovery_hours=1)

        assert policy.evaluate(SLOMetricsSnapshot()) == DegradationMode.NORMAL
        assert policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99}) == DegradationMode.WARNING
        assert policy.evaluate({"diagnosis_success_rate": 0.6, "false_fix_rate": 0.11, "llm_success_rate": 0.99}) == DegradationMode.PROTECTED
        assert policy.evaluate({"diagnosis_success_rate": 0.95, "false_fix_rate": 0.2, "llm_success_rate": 0.99}) == DegradationMode.CIRCUIT_BREAK

    def test_unit_maps_current_mode_to_approval_policy_when_state_changes(self) -> None:
        policy = SLODegradationPolicy()

        policy.current_mode = DegradationMode.NORMAL
        assert policy.get_effective_approval_policy() == "auto_approve"
        policy.current_mode = DegradationMode.WARNING
        assert policy.get_effective_approval_policy() == "human_confirm"
        policy.current_mode = DegradationMode.PROTECTED
        assert policy.get_effective_approval_policy() == "read_only"
        policy.current_mode = DegradationMode.CIRCUIT_BREAK
        assert policy.get_effective_approval_policy() == "disabled"


class TestSLOIntegration:
    def test_integration_accepts_dataclass_and_dict_inputs_when_evaluating_metrics(self) -> None:
        policy = SLODegradationPolicy()

        normal_mode = policy.evaluate(SLOMetricsSnapshot())
        warning_mode = policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99})

        assert normal_mode == DegradationMode.NORMAL
        assert warning_mode == DegradationMode.WARNING

    def test_integration_supports_auto_recovery_and_reset_when_modes_change(self) -> None:
        policy = SLODegradationPolicy(auto_recovery_hours=1)
        policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99})
        assert policy.can_auto_recover(healthy_hours=1) is True

        policy.evaluate({"diagnosis_success_rate": 0.6, "false_fix_rate": 0.11, "llm_success_rate": 0.99})
        assert policy.can_auto_recover(healthy_hours=1) is False
        assert policy.can_auto_recover(healthy_hours=2) is True

        policy.reset()
        assert policy.current_mode == DegradationMode.NORMAL


class TestSLOE2E:
    def test_e2e_transitions_from_warning_to_normal_when_metrics_recover(self) -> None:
        policy = SLODegradationPolicy(auto_recovery_hours=1)

        degraded = policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99})
        recovered = policy.evaluate(SLOMetricsSnapshot())

        assert degraded == DegradationMode.WARNING
        assert recovered == DegradationMode.NORMAL
        assert policy.get_effective_approval_policy() == "auto_approve"

    def test_e2e_holds_circuit_break_until_manual_reset_when_false_fix_rate_high(self) -> None:
        policy = SLODegradationPolicy(auto_recovery_hours=1)

        broken = policy.evaluate({"diagnosis_success_rate": 0.95, "false_fix_rate": 0.2, "llm_success_rate": 0.99})

        assert broken == DegradationMode.CIRCUIT_BREAK
        assert policy.can_auto_recover(healthy_hours=12) is False
        policy.reset()
        assert policy.current_mode == DegradationMode.NORMAL
