from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_agent.models.remediation import RemediationPlan, RemediationStep, VerificationConfig
from sre_agent.safety import BlastRadiusPolicy, contains_forbidden_operation, ensure_within_blast_radius
from sre_agent.tools import ToolExecutionContext, build_default_registry
from sre_agent.remediation.validator import PlanValidator


class _FakeOntology:
    def __init__(self, affected_count: int) -> None:
        self.affected_count = affected_count

    def get_blast_radius(self, entity_id: str) -> dict[str, object]:
        return {"root_entity_id": entity_id, "affected_entities": [], "affected_count": self.affected_count}


def _plan(description: str = "update route", params: dict[str, object] | None = None) -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-1",
        root_cause="rdma issue",
        description=description,
        estimated_impact="switch update",
        confidence=0.8,
        priority="P1",
        steps=[
            RemediationStep(
                step_id=1,
                description=description,
                tool="network.update_route",
                params=params or {"switch": "sw-1", "config_xml": "<route/>"},
                verification=VerificationConfig(method="wait", wait_seconds=1),
            )
        ],
    )


class TestSafetyUnit:
    def test_unit_detects_forbidden_operation_when_shell_destroy_command_present(self) -> None:
        assert contains_forbidden_operation("rm -rf /") is True

    def test_unit_allows_small_blast_radius_when_under_threshold(self) -> None:
        result = ensure_within_blast_radius(_FakeOntology(2), ["node-a"], BlastRadiusPolicy(max_affected_entities=3))

        assert result["affected_count"] == 2


class TestSafetyIntegration:
    def test_integration_validator_rejects_plan_when_blast_radius_exceeds_policy(self) -> None:
        registry = build_default_registry()
        validator = PlanValidator(registry, ontology=_FakeOntology(10), blast_radius_policy=BlastRadiusPolicy(max_affected_entities=3))
        errors = validator.validate(_plan(params={"switch": "sw-1", "config_xml": "<route/>", "entity_id": "node-a"}))

        assert errors
        assert "blast radius too large" in errors[-1]

    @pytest.mark.asyncio
    async def test_integration_registry_keeps_bmc_vlan_write_hard_blocked_without_explicit_flag(self) -> None:
        registry = build_default_registry()
        result = await registry.execute(
            "network.set_bmc_vlan",
            {"bmc_host": "10.0.0.11", "vlan_id": "100"},
            ToolExecutionContext(channels={"redfish": object()}, write_approved=True),
        )

        assert result.success is False
        assert "hard-blocked" in result.error


class TestSafetyE2E:
    def test_e2e_validator_blocks_high_risk_plan_when_description_contains_forbidden_action(self) -> None:
        registry = build_default_registry()
        validator = PlanValidator(registry)
        errors = validator.validate(_plan(description="rm -rf /"))

        assert errors
        assert "forbidden operation" in errors[0]

    def test_e2e_blast_radius_policy_raises_when_happy_path_turns_high_risk(self) -> None:
        with pytest.raises(Exception):
            ensure_within_blast_radius(_FakeOntology(8), ["node-a"], BlastRadiusPolicy(max_affected_entities=2))
