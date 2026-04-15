"""Deterministic remediation engine."""

from __future__ import annotations

import asyncio
import json
import logging
import re
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

LOGGER = logging.getLogger(__name__)


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
        execution_mode: str = "real",
    ) -> None:
        self.tools = tool_registry
        self.approval = approval_gate
        self.wal = wal
        self.prometheus = prometheus
        self.validator = validator or PlanValidator(tool_registry)
        self.execution_context = execution_context or ToolExecutionContext()
        self.canary = CanaryExecutor(wal=wal, prometheus=prometheus)
        self._plans_by_session: dict[str, RemediationPlan] = {}
        self._plan_versions_by_session: dict[str, list[RemediationPlan]] = {}
        mode = str(execution_mode or "real").strip().lower()
        self.execution_mode = mode if mode in {"mock", "real"} else "real"

    def register_plan(self, session_id: str, plan: RemediationPlan) -> None:
        self._plans_by_session[session_id] = plan
        versions = self._plan_versions_by_session.setdefault(session_id, [])
        if not versions:
            versions.append(plan)
            return
        latest = versions[-1]
        if latest.model_dump(mode="json") != plan.model_dump(mode="json"):
            versions.append(plan)

    async def approve_and_execute(
        self,
        session_id: str,
        approval: ApprovalInput,
        *,
        progress_callback: Any | None = None,
    ) -> RemediationResult:
        plan = self._plans_by_session[session_id]
        await self.approval.submit_decision(session_id, approval)
        return await self.execute(plan, session_id=session_id, progress_callback=progress_callback)

    def get_plan(self, session_id: str) -> RemediationPlan | None:
        return self._plans_by_session.get(session_id)

    def get_plan_history(self, session_id: str) -> list[RemediationPlan]:
        return list(self._plan_versions_by_session.get(session_id, []))

    def get_latest_plan_version(self, session_id: str) -> int:
        versions = self._plan_versions_by_session.get(session_id) or []
        if not versions:
            return 0
        return len(versions)

    def revise_plan(
        self,
        *,
        session_id: str,
        instruction: str,
        base_plan_version: int | None = None,
    ) -> tuple[int, RemediationPlan]:
        versions = self._plan_versions_by_session.get(session_id) or []
        if not versions:
            raise KeyError(session_id)

        if base_plan_version is None:
            base_plan = versions[-1]
        else:
            index = max(1, int(base_plan_version)) - 1
            if index >= len(versions):
                raise ValueError("base_plan_version out of range")
            base_plan = versions[index]

        revised_note = str(instruction or "").strip() or "operator requested plan refinement"
        plan_data = base_plan.model_dump(mode="json")
        next_version = len(versions) + 1
        plan_data["plan_id"] = f"{self._base_plan_id(base_plan.plan_id)}-v{next_version}"
        plan_data["steps"] = self._revise_steps(plan_data.get("steps", []), revised_note)
        plan_data["description"] = f"{base_plan.description} | Revised: {revised_note}"
        plan_data["estimated_impact"] = f"{base_plan.estimated_impact} | Revision note: {revised_note}"
        revised_plan = RemediationPlan.model_validate(plan_data)
        versions.append(revised_plan)
        self._plans_by_session[session_id] = revised_plan
        self._plan_versions_by_session[session_id] = versions
        return next_version, revised_plan

    async def rollback(self, session_id: str) -> RollbackResult:
        try:
            recovered = await self.wal.recover_all()
            return RollbackResult(session_id=session_id, success=True, recovered_actions=recovered)
        except Exception as exc:  # noqa: BLE001
            return RollbackResult(session_id=session_id, success=False, error=str(exc))

    async def execute(
        self,
        plan: RemediationPlan,
        session_id: str | None = None,
        *,
        progress_callback: Any | None = None,
    ) -> RemediationResult:
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
            if self.execution_mode == "mock":
                print("mock 已执行修复计划")
                LOGGER.info("mock 已执行修复计划: plan_id=%s", plan.plan_id)
                result = await self._execute_steps_mock(plan)
                duration = int(time.monotonic() - start)
                return result.model_copy(update={"duration_seconds": duration})
            if plan.canary and plan.canary.enabled:
                targets = self._collect_targets(plan)
                return await self.canary.execute_with_canary(
                    plan,
                    targets,
                    lambda batch_targets: self._execute_steps(
                        plan,
                        progress_callback=progress_callback,
                        target_filter=set(batch_targets) if batch_targets else None,
                    ),
                    progress_callback=progress_callback,
                    session_id=session_id,
                )
            result = await self._execute_steps(plan, progress_callback=progress_callback)
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

    async def _execute_steps_mock(self, plan: RemediationPlan) -> RemediationResult:
        verification_results: list[dict[str, Any]] = []
        for step in plan.steps:
            await asyncio.sleep(0)
            command = step.command or self._render_step_command(step.tool, step.params)
            result_message = f"mock 已执行修复计划，步骤 {step.step_id} 已完成"
            verification_results.append(
                {
                    "step_id": step.step_id,
                    "tool": step.tool,
                    "command": command,
                    "result": result_message,
                    "success": True,
                    "verified": True,
                    "mocked": True,
                    "message": result_message,
                }
            )
        return RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=len(plan.steps),
            steps_total=len(plan.steps),
            verification_results=verification_results,
        )

    async def _execute_steps(
        self,
        plan: RemediationPlan,
        *,
        progress_callback: Any | None = None,
        target_filter: set[str] | None = None,
    ) -> RemediationResult:
        completed = 0
        verification_results: list[dict[str, Any]] = []
        failed_step = None

        steps_to_run = plan.steps
        if target_filter is not None:
            steps_to_run = self._filter_steps_by_targets(plan.steps, target_filter)

        total_steps = len(steps_to_run)
        for step in steps_to_run:
            if step.rollback_tool:
                self.wal.record(
                    fault_id=f"{plan.plan_id}-step-{step.step_id}",
                    plan_id=plan.plan_id,
                    step_id=step.step_id,
                    recover_action=step.rollback_tool,
                    recover_params=step.rollback_params or {},
                )
            if progress_callback is not None:
                await progress_callback(
                    stage="remediating",
                    details={
                        "step_id": step.step_id,
                        "tool": step.tool,
                        "steps_completed": completed,
                        "steps_total": total_steps,
                        "message": f"正在执行步骤 {step.step_id}/{total_steps}: {step.description}",
                    },
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
                    steps_total=total_steps,
                    failed_step=failed_step,
                    rolled_back=True,
                    error=result.error,
                )
            if progress_callback is not None:
                await progress_callback(
                    stage="validating",
                    details={
                        "step_id": step.step_id,
                        "steps_completed": completed,
                        "steps_total": total_steps,
                        "message": f"验证步骤 {step.step_id} 执行结果",
                    },
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
                    steps_total=total_steps,
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
            steps_total=total_steps,
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
        process_targets: list[str] = []
        generic_targets: list[str] = []
        seen_process: set[str] = set()
        seen_generic: set[str] = set()

        def _append_unique(bucket: list[str], seen: set[str], value: str) -> None:
            normalized = str(value or "").strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            bucket.append(normalized)

        for step in plan.steps:
            entity_id = step.params.get("entity_id")
            if isinstance(entity_id, str) and entity_id.strip().lower().startswith("proc:"):
                _append_unique(process_targets, seen_process, entity_id)
                continue
            for key in ("node", "target", "service_id", "entity_id"):
                value = step.params.get(key)
                if isinstance(value, str) and value.strip():
                    _append_unique(generic_targets, seen_generic, value)

        # Prefer process-level targets for canary batching when available.
        if process_targets:
            return process_targets
        return generic_targets

    @staticmethod
    def _filter_steps_by_targets(
        steps: list[RemediationAction],
        target_filter: set[str],
    ) -> list[RemediationAction]:
        """Return only steps whose params reference at least one target in the filter set.

        If a step has no target-related param at all, it is included (global step).
        """
        target_keys = ("node", "target", "service_id", "entity_id")
        filtered: list[RemediationAction] = []
        for step in steps:
            step_targets = {
                step.params[key]
                for key in target_keys
                if isinstance(step.params.get(key), str) and step.params[key].strip()
            }
            if not step_targets or step_targets & target_filter:
                filtered.append(step)
        return filtered

    @staticmethod
    def _base_plan_id(plan_id: str) -> str:
        normalized = re.sub(r"-v\d+$", "", str(plan_id or "").strip())
        return normalized or "plan"

    def _revise_steps(self, raw_steps: list[dict[str, Any]], instruction: str) -> list[dict[str, Any]]:
        steps = [dict(item) for item in raw_steps if isinstance(item, dict)]
        removed_step_ids = self._parse_removed_step_ids(instruction)
        if not removed_step_ids:
            return steps

        steps = [item for item in steps if int(item.get("step_id", 0) or 0) not in removed_step_ids]
        if not steps:
            removed_text = ", ".join(str(step_id) for step_id in sorted(removed_step_ids))
            raise ValueError(f"cannot remove steps ({removed_text}): plan must keep at least one step")
        for index, item in enumerate(steps, start=1):
            item["step_id"] = index
        return steps

    @staticmethod
    def _parse_removed_step_ids(instruction: str) -> set[int]:
        text = str(instruction or "")
        if not text.strip():
            return set()
        lowered = text.lower()
        remove_keywords = ("移除", "删除", "删掉", "去掉", "remove", "drop")
        if not any(keyword in text or keyword in lowered for keyword in remove_keywords):
            return set()

        found: set[int] = set()
        for match in re.findall(r"(?:步骤|step)\s*([0-9]+)", text, flags=re.IGNORECASE):
            found.add(int(match))
        for match in re.findall(r"第\s*([0-9]+)\s*步", text):
            found.add(int(match))
        if found:
            return found

        remove_block = re.search(
            r"(?:移除|删除|删掉|去掉|remove|drop)\s*([0-9,\s，、和and]+)",
            text,
            flags=re.IGNORECASE,
        )
        if remove_block:
            for match in re.findall(r"[0-9]+", remove_block.group(1)):
                found.add(int(match))
        return found

    def _render_step_command(self, tool: str, params: dict[str, Any]) -> str:
        """Resolve command from tool registry; fallback to generic format."""
        try:
            tool_def = self.tools.get_tool(tool)
            return tool_def.build_command(params)
        except Exception:  # noqa: BLE001
            compact = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
            return f"{tool} {compact}"


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
