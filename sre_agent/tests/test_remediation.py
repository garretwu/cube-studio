from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sre_agent.models.remediation import (
    RemediationPlan,
    RemediationStep,
    VerificationCondition,
    VerificationConfig,
)
from sre_agent.remediation import ApprovalGate, ApprovalInput, PlanValidationError, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.tools import SafetyLevel, ToolDefinition, ToolExecutionContext, ToolRegistry


def _make_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def _delete_pod(params: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        _ = context
        return {"deleted": params["pod_name"]}

    async def _scale(params: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        _ = context
        return {"scaled": params["name"], "replicas": params["replicas"]}

    async def _query(params: dict[str, Any], context: ToolExecutionContext) -> float:
        _ = context
        return float(params.get("value", 1.0))

    registry.register(
        ToolDefinition(
            name="k8s.delete_pod",
            description="delete pod",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["namespace", "pod_name"]},
            needs_approval=True,
        ),
        _delete_pod,
    )
    registry.register(
        ToolDefinition(
            name="k8s.scale_deployment",
            description="scale deployment",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["namespace", "name", "replicas"]},
            needs_approval=True,
        ),
        _scale,
    )
    registry.register(
        ToolDefinition(
            name="prometheus.query_instant",
            description="query instant",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["value"]},
        ),
        _query,
    )
    return registry


def _make_plan(tool: str = "k8s.delete_pod", *, verification: VerificationConfig | None = None) -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-1",
        root_cause="gpu contention",
        description="delete one pod to drain load",
        estimated_impact="one pod restart",
        confidence=0.9,
        priority="P1",
        steps=[
            RemediationStep(
                step_id=1,
                description="delete pod",
                tool=tool,
                params={"namespace": "infer", "pod_name": "vllm-0"} if tool == "k8s.delete_pod" else {"namespace": "infer", "name": "vllm", "replicas": 2},
                rollback_tool="k8s.delete_pod",
                rollback_params={"namespace": "infer", "pod_name": "vllm-rollback"},
                verification=verification
                or VerificationConfig(
                    method="wait",
                    wait_seconds=1,
                ),
            )
        ],
    )


class _FakePrometheus:
    def __init__(self, value: float) -> None:
        self.value = value

    async def query_instant(self, promql: str) -> float:
        _ = promql
        return self.value


class TestRemediationUnit:
    def test_unit_validator_reports_missing_required_params_when_plan_incomplete(self) -> None:
        registry = _make_registry()
        validator = PlanValidator(registry)
        bad_plan = RemediationPlan(
            plan_id="bad-plan",
            root_cause="oom",
            description="scale deployment",
            estimated_impact="minor",
            confidence=0.8,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="scale",
                    tool="k8s.scale_deployment",
                    params={"namespace": "infer"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                )
            ],
        )

        errors = validator.validate(bad_plan)

        assert errors
        assert "missing params" in errors[0]

    @pytest.mark.asyncio
    async def test_unit_approval_gate_auto_approves_low_risk_plan_when_no_human_required(self) -> None:
        gate = ApprovalGate(default_policy="human_confirm")
        result = await gate.request_approval(_make_plan())

        assert result.approved is True
        assert result.method == "auto"

    @pytest.mark.asyncio
    async def test_unit_approval_gate_routes_decisions_by_session(self) -> None:
        gate = ApprovalGate(default_policy="human_confirm")
        plan = _make_plan(tool="k8s.scale_deployment")

        task_s1 = asyncio.create_task(gate.request_approval(plan, session_id="session-1"))
        task_s2 = asyncio.create_task(gate.request_approval(plan, session_id="session-2"))
        await asyncio.sleep(0)
        await gate.submit_decision("session-2", ApprovalInput(approved=False, user="bob", reason="reject"))
        await gate.submit_decision("session-1", ApprovalInput(approved=True, user="alice"))

        result_s1 = await task_s1
        result_s2 = await task_s2

        assert result_s1.approved is True
        assert result_s1.approver == "alice"
        assert result_s2.approved is False
        assert result_s2.reason == "reject"

    def test_unit_wal_records_entry_when_step_has_rollback(self, tmp_path: Path) -> None:
        wal = RollbackJournal(tmp_path / "journal.jsonl")
        wal.record("fault-1", "plan-1", 1, "k8s.delete_pod", {"pod_name": "rollback"})

        assert len(wal.entries) == 1
        assert wal.entries[0].recover_action == "k8s.delete_pod"


class TestRemediationIntegration:
    @pytest.mark.asyncio
    async def test_integration_engine_executes_and_verifies_plan_when_handlers_succeed(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(registry, gate, wal, execution_context=ToolExecutionContext())

        result = await engine.execute(_make_plan())

        assert result.success is True
        assert result.steps_completed == 1
        assert result.steps_total == 1

    @pytest.mark.asyncio
    async def test_integration_engine_rolls_back_when_promql_verification_fails(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(
            registry,
            gate,
            wal,
            prometheus=_FakePrometheus(900.0),
            execution_context=ToolExecutionContext(),
        )
        plan = _make_plan(
            verification=VerificationConfig(
                method="promql",
                query="latency",
                condition=VerificationCondition(field="value", operator="<", value=500),
                wait_seconds=1,
            )
        )

        result = await engine.execute(plan)

        assert result.success is False
        assert result.rolled_back is True
        assert result.error == "verification failed"


class TestRemediationE2E:
    @pytest.mark.asyncio
    async def test_e2e_approve_and_execute_runs_registered_plan_when_happy_path(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="human_confirm")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(registry, gate, wal, execution_context=ToolExecutionContext())
        plan = _make_plan(tool="k8s.scale_deployment")
        engine.register_plan("session-1", plan)

        task = asyncio.create_task(
            engine.approve_and_execute(
                "session-1",
                ApprovalInput(approved=True, user="operator"),
            )
        )
        result = await task

        assert result.success is True
        assert result.steps_total == 1

    @pytest.mark.asyncio
    async def test_e2e_engine_raises_validation_error_when_plan_schema_invalid(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(registry, gate, wal, execution_context=ToolExecutionContext())
        bad_plan = _make_plan(tool="missing.tool")

        with pytest.raises(PlanValidationError):
            await engine.execute(bad_plan)
