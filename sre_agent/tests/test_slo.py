from __future__ import annotations

from sre_agent.slo.degradation import DegradationMode, SLODegradationPolicy
from sre_agent.slo.metrics import SLOMetricsSnapshot


def test_slo_policy_uses_document_modes_and_approval_mapping() -> None:
    policy = SLODegradationPolicy(auto_recovery_hours=1)

    assert policy.evaluate(SLOMetricsSnapshot()) == DegradationMode.NORMAL
    assert policy.get_effective_approval_policy() == "auto_approve"

    assert policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99}) == DegradationMode.WARNING
    assert policy.get_effective_approval_policy() == "human_confirm"

    assert policy.evaluate({"diagnosis_success_rate": 0.6, "false_fix_rate": 0.11, "llm_success_rate": 0.99}) == DegradationMode.PROTECTED
    assert policy.get_effective_approval_policy() == "read_only"

    assert policy.evaluate({"diagnosis_success_rate": 0.95, "false_fix_rate": 0.2, "llm_success_rate": 0.99}) == DegradationMode.CIRCUIT_BREAK
    assert policy.get_effective_approval_policy() == "disabled"


def test_slo_policy_auto_recovery_and_reset_contracts() -> None:
    policy = SLODegradationPolicy(auto_recovery_hours=1)
    policy.evaluate({"diagnosis_success_rate": 0.8, "false_fix_rate": 0.01, "llm_success_rate": 0.99})
    assert policy.can_auto_recover(healthy_hours=1) is True

    policy.evaluate({"diagnosis_success_rate": 0.6, "false_fix_rate": 0.11, "llm_success_rate": 0.99})
    assert policy.can_auto_recover(healthy_hours=1) is False
    assert policy.can_auto_recover(healthy_hours=2) is True

    policy.evaluate({"diagnosis_success_rate": 0.95, "false_fix_rate": 0.2, "llm_success_rate": 0.99})
    assert policy.can_auto_recover(healthy_hours=12) is False
    policy.reset()
    assert policy.current_mode == DegradationMode.NORMAL
