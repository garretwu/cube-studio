from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sre_agent.models.remediation import (
    CanaryCondition,
    CanaryConfig,
    RemediationPlan,
    RemediationStep,
    VerificationCondition,
    VerificationConfig,
)
from sre_agent.remediation import ApprovalGate, ApprovalInput, PlanValidationError, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.tools import SafetyLevel, ToolDefinition, ToolExecutionContext, ToolRegistry, build_default_registry


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

    async def _kill_process(params: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        _ = context
        return {"node": params.get("node"), "target": params.get("pid") or params.get("pid_or_name") or params.get("process_name")}

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
    registry.register(
        ToolDefinition(
            name="kill_process",
            description="terminate process by pid or name",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["node"]},
            needs_approval=True,
        ),
        _kill_process,
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
    def test_unit_engine_defaults_to_real_execution_mode(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")

        engine = RemediationEngine(registry, gate, wal, execution_context=ToolExecutionContext())

        assert engine.execution_mode == "real"

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
        assert "missing_required_params" in errors[0]

    def test_unit_validator_accepts_kill_process_with_proc_entity_id(self) -> None:
        registry = _make_registry()
        validator = PlanValidator(registry)
        plan = RemediationPlan(
            plan_id="kill-plan-valid",
            root_cause="synthetic load",
            description="terminate suspicious process",
            estimated_impact="ttft recovers",
            confidence=0.8,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill by process entity",
                    tool="kill_process",
                    params={"node": "10.11.4.13", "entity_id": "proc:473156"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                )
            ],
        )

        errors = validator.validate(plan)

        assert errors == []

    def test_unit_validator_rejects_kill_process_without_target(self) -> None:
        registry = _make_registry()
        validator = PlanValidator(registry)
        plan = RemediationPlan(
            plan_id="kill-plan-missing-target",
            root_cause="synthetic load",
            description="terminate suspicious process",
            estimated_impact="ttft recovers",
            confidence=0.8,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill without target",
                    tool="kill_process",
                    params={"node": "10.11.4.13"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                )
            ],
        )

        errors = validator.validate(plan)

        assert errors
        assert "missing_target for kill_process" in errors[0]

    def test_unit_validator_rejects_kill_process_with_invalid_entity_id(self) -> None:
        registry = _make_registry()
        validator = PlanValidator(registry)
        plan = RemediationPlan(
            plan_id="kill-plan-invalid-entity",
            root_cause="synthetic load",
            description="terminate suspicious process",
            estimated_impact="ttft recovers",
            confidence=0.8,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill with non-proc entity",
                    tool="kill_process",
                    params={"node": "10.11.4.13", "entity_id": "node:10.11.4.13"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                )
            ],
        )

        errors = validator.validate(plan)

        assert errors
        assert "missing_target for kill_process" in errors[0]

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

    def test_unit_collect_targets_prefers_process_entity_ids(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(registry, gate, wal, execution_context=ToolExecutionContext())
        plan = RemediationPlan(
            plan_id="plan-proc-targets",
            root_cause="synthetic load process",
            description="terminate suspicious process in two canary batches",
            estimated_impact="ttft recovers gradually",
            confidence=0.8,
            priority="P1",
            canary=CanaryConfig(
                enabled=True,
                target_percentage=0.5,
                monitor_duration=1,
                success_criteria=[
                    CanaryCondition(metric="vector(1)", operator=">=", value=1),
                ],
                max_batches=2,
                progressive=False,
            ),
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill process A",
                    tool="k8s.delete_pod",
                    params={
                        "namespace": "infer",
                        "pod_name": "vllm-0",
                        "node": "10.11.4.13",
                        "entity_id": "proc:ls_demo_a",
                    },
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
                RemediationStep(
                    step_id=2,
                    description="kill process B",
                    tool="k8s.delete_pod",
                    params={
                        "namespace": "infer",
                        "pod_name": "vllm-1",
                        "node": "10.11.4.13",
                        "entity_id": "proc:ls_demo_b",
                    },
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
            ],
        )

        assert engine._collect_targets(plan) == ["proc:ls_demo_a", "proc:ls_demo_b"]


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

    @pytest.mark.asyncio
    async def test_integration_engine_canary_batches_process_targets_on_same_node(self, tmp_path: Path) -> None:
        registry = _make_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        engine = RemediationEngine(
            registry,
            gate,
            wal,
            prometheus=_FakePrometheus(1.0),
            execution_context=ToolExecutionContext(),
        )
        plan = RemediationPlan(
            plan_id="plan-proc-canary",
            root_cause="synthetic load process",
            description="terminate suspicious load processes in process-level canary",
            estimated_impact="ttft recovers",
            confidence=0.86,
            priority="P1",
            canary=CanaryConfig(
                enabled=True,
                target_percentage=0.5,
                monitor_duration=1,
                success_criteria=[
                    CanaryCondition(metric="vector(1)", operator=">=", value=1),
                ],
                criteria_mode="all",
                max_batches=2,
                progressive=False,
            ),
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill process A",
                    tool="k8s.delete_pod",
                    params={
                        "namespace": "infer",
                        "pod_name": "vllm-0",
                        "node": "10.11.4.13",
                        "entity_id": "proc:ls_demo_a",
                    },
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
                RemediationStep(
                    step_id=2,
                    description="kill process B",
                    tool="k8s.delete_pod",
                    params={
                        "namespace": "infer",
                        "pod_name": "vllm-1",
                        "node": "10.11.4.13",
                        "entity_id": "proc:ls_demo_b",
                    },
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
            ],
        )
        progress_events: list[tuple[str, dict[str, Any]]] = []

        async def _on_progress(*, stage: str, details: dict[str, Any] | None = None) -> None:
            progress_events.append((stage, dict(details or {})))

        result = await engine.execute(plan, session_id="session-proc-canary", progress_callback=_on_progress)

        assert result.success is True
        assert result.steps_completed == 2
        assert result.steps_total == 2
        batch_started = [details for stage, details in progress_events if stage == "canary_batch_started"]
        assert len(batch_started) == 2
        assert batch_started[0]["targets_in_batch"] == ["proc:ls_demo_a"]
        assert batch_started[1]["targets_in_batch"] == ["proc:ls_demo_b"]

    @pytest.mark.asyncio
    async def test_integration_engine_canary_executes_kill_process_with_entity_id_only_steps(self, tmp_path: Path) -> None:
        class _SSHResult:
            success = True
            output = "ok"
            error = ""

        class _FakeSSHChannel:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def run_command(self, node: str, command: str, use_sudo: bool = False) -> _SSHResult:
                self.calls.append({"node": node, "command": command, "use_sudo": use_sudo})
                return _SSHResult()

        registry = build_default_registry()
        gate = ApprovalGate(default_policy="auto_approve")
        wal = RollbackJournal(tmp_path / "wal.jsonl")
        ssh = _FakeSSHChannel()
        engine = RemediationEngine(
            registry,
            gate,
            wal,
            prometheus=_FakePrometheus(1.0),
            execution_context=ToolExecutionContext(channels={"ssh": ssh}),
        )
        plan = RemediationPlan(
            plan_id="plan-kill-entity-canary",
            root_cause="synthetic load process",
            description="kill suspicious processes with entity-id only targets",
            estimated_impact="ttft recovers",
            confidence=0.86,
            priority="P1",
            canary=CanaryConfig(
                enabled=True,
                target_percentage=0.5,
                monitor_duration=1,
                success_criteria=[
                    CanaryCondition(metric="vector(1)", operator=">=", value=1),
                ],
                criteria_mode="all",
                max_batches=2,
                progressive=False,
            ),
            steps=[
                RemediationStep(
                    step_id=1,
                    description="kill process A",
                    tool="kill_process",
                    params={"node": "10.11.4.13", "entity_id": "proc:473156", "signal": "SIGTERM"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
                RemediationStep(
                    step_id=2,
                    description="kill process B",
                    tool="kill_process",
                    params={"node": "10.11.4.13", "entity_id": "proc:473573", "signal": "SIGTERM"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
            ],
        )
        progress_events: list[tuple[str, dict[str, Any]]] = []

        async def _on_progress(*, stage: str, details: dict[str, Any] | None = None) -> None:
            progress_events.append((stage, dict(details or {})))

        result = await engine.execute(plan, session_id="session-kill-entity-canary", progress_callback=_on_progress)

        assert result.success is True
        assert result.steps_completed == 2
        assert len(ssh.calls) == 2
        assert "kill -TERM -- 473156" in ssh.calls[0]["command"]
        assert "kill -TERM -- 473573" in ssh.calls[1]["command"]
        batch_started = [details for stage, details in progress_events if stage == "canary_batch_started"]
        assert len(batch_started) == 2
        assert batch_started[0]["targets_in_batch"] == ["proc:473156"]
        assert batch_started[1]["targets_in_batch"] == ["proc:473573"]


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
