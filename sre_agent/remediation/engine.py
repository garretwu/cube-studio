"""Deterministic remediation engine."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.models.remediation import RemediationPlan, RemediationResult, VerificationConfig
from sre_agent.remediation.approval import ApprovalGate, ApprovalInput
from sre_agent.remediation.canary import CanaryExecutor, _compare
from sre_agent.remediation.validator import PlanValidationError, PlanValidator
from sre_agent.remediation.wal import RollbackJournal
from sre_agent.tools import ToolExecutionContext, ToolRegistry


class RollbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    success: bool
    recovered_actions: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RemediationEngine:
    def __init__(
        self,
        tool_registry: ToolRegistry,
        approval_gate: ApprovalGate,
        wal: RollbackJournal,
        prometheus: Any | None = None,
        validator: PlanValidator | None = None,
        execution_context: ToolExecutionContext | None = None,
    ) -> None:
        self.tools = tool_registry
        self.approval = approval_gate
        self.wal = wal
        self.prometheus = prometheus
        self.validator = validator or PlanValidator(tool_registry)
        self.execution_context = execution_context or ToolExecutionContext()
        self.canary = CanaryExecutor(wal=wal, prometheus=prometheus)
        self._plans_by_session: dict[str, RemediationPlan] = {}

    def register_plan(self, session_id: str, plan: RemediationPlan) -> None:
        self._plans_by_session[session_id] = plan

    async def approve_and_execute(self, session_id: str, approval: ApprovalInput) -> RemediationResult:
        plan = self._plans_by_session[session_id]
        await self.approval.submit_decision(session_id, approval)
        return await self.execute(plan, session_id=session_id)

    async def rollback(self, session_id: str) -> RollbackResult:
        try:
            recovered = await self.wal.recover_all()
            return RollbackResult(session_id=session_id, success=True, recovered_actions=recovered)
        except Exception as exc:  # noqa: BLE001
            return RollbackResult(session_id=session_id, success=False, error=str(exc))

    async def execute(self, plan: RemediationPlan, session_id: str | None = None) -> RemediationResult:
        errors = self.validator.validate(plan)
        if errors:
            raise PlanValidationError(errors)

        if session_id:
            self.register_plan(session_id, plan)
        approval = await self.approval.request_approval(plan, session_id=session_id)
        if not approval.approved:
            return RemediationResult(
                plan_id=plan.plan_id,
                success=False,
                steps_completed=0,
                steps_total=len(plan.steps),
                error=approval.reason or "approval denied",
            )

        start = time.monotonic()
        try:
            if plan.canary and plan.canary.enabled:
                targets = self._collect_targets(plan)
                return await self.canary.execute_with_canary(
                    plan,
                    targets,
                    lambda _targets: self._execute_steps(plan),
                )
            result = await self._execute_steps(plan)
            duration = int(time.monotonic() - start)
            return result.model_copy(update={"duration_seconds": duration})
        except Exception as exc:  # noqa: BLE001
            await self.wal.recover_all()
            return RemediationResult(
                plan_id=plan.plan_id,
                success=False,
                steps_completed=0,
                steps_total=len(plan.steps),
                rolled_back=True,
                error=str(exc),
                duration_seconds=int(time.monotonic() - start),
            )

    async def _execute_steps(self, plan: RemediationPlan) -> RemediationResult:
        completed = 0
        verification_results: list[dict[str, Any]] = []
        failed_step = None
        for step in plan.steps:
            if step.rollback_tool:
                self.wal.record(
                    fault_id=f"{plan.plan_id}-step-{step.step_id}",
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    recover_action=step.rollback_tool,
                    recover_params=step.rollback_params or {},
                )
            result = await self.tools.execute(
                step.tool,
                step.params,
                replace(self.execution_context, write_approved=True),
            )
            if not result.success:
                failed_step = step
                await self.wal.recover_all()
                return RemediationResult(
                    plan_id=plan.plan_id,
                    success=False,
                    steps_completed=completed,
                    steps_total=len(plan.steps),
                    failed_step=failed_step,
                    rolled_back=True,
                    error=result.error,
                )
            verified = await self._verify(step.verification)
            verification_results.append({"step_id": step.step_id, "verified": verified})
            if not verified:
                failed_step = step
                await self.wal.recover_all()
                return RemediationResult(
                    plan_id=plan.plan_id,
                    success=False,
                    steps_completed=completed,
                    steps_total=len(plan.steps),
                    failed_step=failed_step,
                    rolled_back=True,
                    verification_results=verification_results,
                    error="verification failed",
                )
            completed += 1

        return RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=completed,
            steps_total=len(plan.steps),
            verification_results=verification_results,
        )

    async def _verify(self, config: VerificationConfig) -> bool:
        if config.method == "wait":
            await asyncio.sleep(max(0, config.wait_seconds))
            return True
        if config.method == "promql":
            if config.wait_seconds > 0:
                await asyncio.sleep(config.wait_seconds)
            if self.prometheus is None or not hasattr(self.prometheus, "query_instant"):
                return True
            value = await self.prometheus.query_instant(config.query or "")
            if config.condition is None:
                return bool(value)
            actual = _extract_field({"value": value}, config.condition.field)
            return _compare(actual, config.condition.operator, config.condition.value)
        if config.method == "tool_call":
            if config.wait_seconds > 0:
                await asyncio.sleep(config.wait_seconds)
            result = await self.tools.execute(
                config.tool or "",
                config.tool_params or {},
                self.execution_context,
            )
            if not result.success:
                return False
            if config.condition is None:
                return True
            actual = _extract_field(result.data, config.condition.field)
            return _compare(actual, config.condition.operator, config.condition.value)
        return False

    @staticmethod
    def _collect_targets(plan: RemediationPlan) -> list[str]:
        targets: list[str] = []
        for step in plan.steps:
            for key in ("node", "target", "service_id", "entity_id"):
                value = step.params.get(key)
                if isinstance(value, str) and value.strip():
                    targets.append(value.strip())
        return sorted(set(targets))


def _extract_field(payload: Any, path: str) -> Any:
    if path == "value":
        if isinstance(payload, dict):
            return payload.get("value")
        return payload
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current
