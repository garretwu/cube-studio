"""Deterministic remediation engine."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from sre_agent.models.remediation import RemediationPlan, RemediationResult, VerificationConfig
from sre_agent.remediation.approval import ApprovalGate, ApprovalInput
from sre_agent.remediation.canary import CanaryExecutor, _compare
from sre_agent.remediation.validator import PlanValidationError, PlanValidator
from sre_agent.ttft_process_policy import is_ttft_suspect_process
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
        self._plans_by_session_key: dict[str, dict[str, RemediationPlan]] = {}
        self._plan_versions_by_session_key: dict[str, dict[str, list[RemediationPlan]]] = {}
        self._default_plan_key_by_session: dict[str, str] = {}
        mode = str(execution_mode or "real").strip().lower()
        self.execution_mode = mode if mode in {"mock", "real"} else "real"

    def register_plan(
        self,
        session_id: str,
        plan: RemediationPlan,
        *,
        plan_key: str | None = None,
        set_default: bool = True,
    ) -> None:
        resolved_key = self._normalize_plan_key(plan_key)
        session_plans = self._plans_by_session_key.setdefault(session_id, {})
        session_versions = self._plan_versions_by_session_key.setdefault(session_id, {})
        session_plans[resolved_key] = plan
        versions = session_versions.setdefault(resolved_key, [])
        if not versions:
            versions.append(plan)
        else:
            latest = versions[-1]
            if latest.model_dump(mode="json") != plan.model_dump(mode="json"):
                versions.append(plan)
        if set_default or session_id not in self._default_plan_key_by_session:
            self._default_plan_key_by_session[session_id] = resolved_key
        self._sync_legacy_session_views(session_id)

    def register_plans(
        self,
        session_id: str,
        plans: Iterable[tuple[str, RemediationPlan]],
        *,
        clear_existing: bool = True,
    ) -> list[str]:
        if clear_existing:
            self.clear_session_plans(session_id)
        registered_keys: list[str] = []
        for index, (plan_key, plan) in enumerate(plans):
            self.register_plan(
                session_id,
                plan,
                plan_key=plan_key,
                set_default=index == 0,
            )
            registered_keys.append(self._normalize_plan_key(plan_key))
        self._sync_legacy_session_views(session_id)
        return registered_keys

    async def approve_and_execute(
        self,
        session_id: str,
        approval: ApprovalInput,
        *,
        plan_key: str | None = None,
        progress_callback: Any | None = None,
    ) -> RemediationResult:
        plan = self.get_plan(session_id, plan_key=plan_key)
        if plan is None:
            raise KeyError(session_id if plan_key is None else f"{session_id}:{plan_key}")
        await self.approval.submit_decision(session_id, approval)
        return await self.execute(
            plan,
            session_id=session_id,
            plan_key=plan_key,
            progress_callback=progress_callback,
        )

    def get_plan(self, session_id: str, *, plan_key: str | None = None) -> RemediationPlan | None:
        if plan_key is None:
            return self._plans_by_session.get(session_id)
        resolved_key = self._normalize_plan_key(plan_key)
        return self._plans_by_session_key.get(session_id, {}).get(resolved_key)

    def get_plan_history(self, session_id: str, *, plan_key: str | None = None) -> list[RemediationPlan]:
        if plan_key is None:
            return list(self._plan_versions_by_session.get(session_id, []))
        resolved_key = self._normalize_plan_key(plan_key)
        return list(self._plan_versions_by_session_key.get(session_id, {}).get(resolved_key, []))

    def get_latest_plan_version(self, session_id: str, *, plan_key: str | None = None) -> int:
        if plan_key is None:
            versions = self._plan_versions_by_session.get(session_id) or []
        else:
            resolved_key = self._normalize_plan_key(plan_key)
            versions = self._plan_versions_by_session_key.get(session_id, {}).get(resolved_key) or []
        if not versions:
            return 0
        return len(versions)

    def list_plan_keys(self, session_id: str) -> list[str]:
        session_plans = self._plans_by_session_key.get(session_id, {})
        default_key = self._default_plan_key_by_session.get(session_id)
        ordered = sorted(session_plans.keys())
        if default_key is not None and default_key in ordered:
            ordered.remove(default_key)
            ordered.insert(0, default_key)
        return ordered

    def get_all_plans(self, session_id: str) -> list[tuple[str, RemediationPlan]]:
        session_plans = self._plans_by_session_key.get(session_id, {})
        keys = self.list_plan_keys(session_id)
        return [(key, session_plans[key]) for key in keys if key in session_plans]

    def revise_plan(
        self,
        *,
        session_id: str,
        instruction: str,
        base_plan_version: int | None = None,
        plan_key: str | None = None,
    ) -> tuple[int, RemediationPlan]:
        resolved_key = self._resolve_plan_key_or_default(session_id, plan_key)
        if resolved_key is None:
            raise KeyError(session_id)
        versions = self._plan_versions_by_session_key.get(session_id, {}).get(resolved_key) or []
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
        session_plans = self._plans_by_session_key.setdefault(session_id, {})
        session_versions = self._plan_versions_by_session_key.setdefault(session_id, {})
        session_plans[resolved_key] = revised_plan
        session_versions[resolved_key] = versions
        self._sync_legacy_session_views(session_id)
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
        plan_key: str | None = None,
        progress_callback: Any | None = None,
    ) -> RemediationResult:
        errors = self.validator.validate(plan)
        if errors:
            raise PlanValidationError(errors)

        if session_id:
            self.register_plan(session_id, plan, plan_key=plan_key)
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
                if self._has_process_target_mismatch(plan, targets):
                    LOGGER.warning(
                        "blocking canary auto execution due to process target mismatch: plan_id=%s batch_total=%s",
                        plan.plan_id,
                        len(targets),
                    )
                    if progress_callback is not None:
                        await progress_callback(
                            stage="canary_check_failed",
                            details={
                                "message": "进程级灰度批次目标异常，已阻断自动执行，请人工复核后重试。",
                                "error": "process_target_mismatch",
                                "suspect_process_count": len(targets),
                                "planned_batch_total": len(targets),
                            },
                        )
                    return RemediationResult(
                        plan_id=plan.plan_id,
                        success=False,
                        steps_completed=0,
                        steps_total=len(plan.steps),
                        error="process canary target mismatch; manual approval required",
                    )
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
            precheck_error = await self._precheck_kill_process_target(plan=plan, step=step)
            if precheck_error is not None:
                if str(precheck_error.get("action") or "").strip() == "pid_already_absent":
                    LOGGER.info(
                        "ttft kill_process precheck: pid_already_absent=true plan_id=%s step_id=%s node=%s pid=%s",
                        plan.plan_id,
                        step.step_id,
                        precheck_error.get("node"),
                        precheck_error.get("pid"),
                    )
                    if progress_callback is not None:
                        await progress_callback(
                            stage="validating",
                            details={
                                "step_id": step.step_id,
                                "steps_completed": completed,
                                "steps_total": total_steps,
                                "message": (
                                    f"目标进程 PID {precheck_error.get('pid')} 已不存在，"
                                    "跳过执行并视为完成"
                                ),
                                "pid_already_absent": True,
                                "node": precheck_error.get("node"),
                                "pid": precheck_error.get("pid"),
                            },
                        )
                    verification_results.append(
                        {
                            "step_id": step.step_id,
                            "verified": True,
                            "skipped": True,
                            "reason": "pid_already_absent",
                            "pid": precheck_error.get("pid"),
                            "node": precheck_error.get("node"),
                        }
                    )
                    completed += 1
                    continue
                failed_step = step
                await self.wal.recover_all()
                return RemediationResult(
                    plan_id=plan.plan_id,
                    success=False,
                    steps_completed=completed,
                    steps_total=total_steps,
                    failed_step=failed_step,
                    rolled_back=True,
                    error=str(precheck_error.get("reason") or "stale or mismatched pid"),
                    error_code="stale_or_mismatched_pid",
                    error_details=precheck_error,
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
            completed += 1

        final_verifications = self._final_verification_configs(steps_to_run)
        if final_verifications:
            if progress_callback is not None:
                await progress_callback(
                    stage="validating",
                    details={
                        "step_id": steps_to_run[-1].step_id if steps_to_run else None,
                        "steps_completed": completed,
                        "steps_total": total_steps,
                        "verification_scope": "plan_final",
                        "verification_count": len(final_verifications),
                        "message": "验证修复计划最终结果",
                    },
                )
            for verification_index, verification in enumerate(final_verifications, start=1):
                verified = await self._verify(verification)
                verification_results.append(
                    {
                        "step_id": steps_to_run[-1].step_id if steps_to_run else None,
                        "verification_index": verification_index,
                        "verification_scope": "plan_final",
                        "verified": verified,
                    }
                )
                if not verified:
                    failed_step = steps_to_run[-1] if steps_to_run else None
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

        return RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=completed,
            steps_total=total_steps,
            verification_results=verification_results,
        )

    @staticmethod
    def _final_verification_configs(steps: Iterable[Any]) -> list[VerificationConfig]:
        """Return deduplicated plan-final verification configs.

        Step-level verification is intentionally deferred until all actions in the
        current plan/root-cause have run. Repeated generated plans often attach the
        same global verification to every step; keeping one final check avoids
        false failures after the first partial action.
        """
        step_list = list(steps)
        if not step_list:
            return []
        candidates = [step.verification for step in step_list if getattr(step, "verification", None) is not None]
        non_wait_candidates = [item for item in candidates if item.method != "wait"]
        selected = non_wait_candidates or candidates[-1:]
        deduped_reversed: list[VerificationConfig] = []
        seen: set[str] = set()
        for config in reversed(selected):
            key = json.dumps(config.model_dump(mode="json"), sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            deduped_reversed.append(config)
        return list(reversed(deduped_reversed))

    async def _precheck_kill_process_target(
        self,
        *,
        plan: RemediationPlan,
        step: Any,
    ) -> dict[str, Any] | None:
        if str(step.tool or "").strip() != "kill_process":
            return None
        if "-ttft-kill" not in str(plan.plan_id or ""):
            return None
        node = str(step.params.get("node", "") or "").strip()
        if not node:
            return {
                "action": "re_diagnose_required",
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "node": "",
                "pid": None,
                "reason": "missing node for kill_process precheck",
            }
        target_pid = self._extract_target_pid(step.params)
        if target_pid is None:
            return {
                "action": "re_diagnose_required",
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "node": node,
                "pid": None,
                "reason": "missing numeric pid for kill_process precheck",
            }

        precheck = await self.tools.execute(
            "process.find",
            {"node": node, "pattern": str(target_pid)},
            self.execution_context,
        )
        if not precheck.success:
            return {
                "action": "re_diagnose_required",
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "node": node,
                "pid": target_pid,
                "reason": f"precheck failed: {precheck.error or 'unknown error'}",
            }
        payload = precheck.data if isinstance(precheck.data, dict) else {}
        raw_matches = payload.get("matches")
        matches = raw_matches if isinstance(raw_matches, list) else []
        pid_match: dict[str, Any] | None = None
        for item in matches:
            if not isinstance(item, dict):
                continue
            try:
                pid = int(item.get("pid"))
            except Exception:  # noqa: BLE001
                continue
            if pid == target_pid:
                pid_match = item
                break
        commandline_sample = ""
        if isinstance(pid_match, dict):
            commandline_sample = str(pid_match.get("command") or pid_match.get("process") or "").strip()
        LOGGER.info(
            "ttft kill_process precheck: stale_pid_precheck=true plan_id=%s step_id=%s node=%s pid=%s pid_commandline_sample=%s",
            plan.plan_id,
            step.step_id,
            node,
            target_pid,
            commandline_sample or "<empty>",
        )
        if pid_match is None:
            return {
                "action": "pid_already_absent",
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "node": node,
                "pid": target_pid,
                "reason": "target pid not found before execution",
                "pid_commandline_sample": "",
            }
        if not is_ttft_suspect_process(commandline_sample):
            return {
                "action": "re_diagnose_required",
                "plan_id": plan.plan_id,
                "step_id": step.step_id,
                "node": node,
                "pid": target_pid,
                "reason": "target pid commandline is not TTFT-suspect process",
                "pid_commandline_sample": commandline_sample,
            }
        return None

    @staticmethod
    def _extract_target_pid(params: dict[str, Any]) -> int | None:
        pid_raw = params.get("pid")
        if pid_raw is not None:
            try:
                pid = int(pid_raw)
            except Exception:  # noqa: BLE001
                pid = -1
            if pid > 0:
                return pid
        entity_id = str(params.get("entity_id", "") or "").strip()
        if entity_id.lower().startswith("proc:"):
            raw = entity_id.split(":", 1)[1].strip()
            try:
                pid = int(raw)
            except Exception:  # noqa: BLE001
                return None
            if pid > 0:
                return pid
        return None

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
            try:
                result = await asyncio.wait_for(
                    self.tools.execute(
                        config.tool or "",
                        config.tool_params or {},
                        self.execution_context,
                    ),
                    timeout=60,
                )
            except asyncio.TimeoutError:
                return False
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
    def _has_process_target_mismatch(plan: RemediationPlan, targets: list[str]) -> bool:
        process_step_targets: list[str] = []
        for step in plan.steps:
            entity_id = step.params.get("entity_id")
            if isinstance(entity_id, str) and entity_id.strip().lower().startswith("proc:"):
                process_step_targets.append(entity_id.strip())
        if not process_step_targets:
            return False

        unique_steps = list(dict.fromkeys(process_step_targets))
        unique_targets = list(dict.fromkeys(targets))
        if len(unique_steps) != len(process_step_targets):
            return True
        return unique_steps != unique_targets

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

    def clear_session_plans(self, session_id: str) -> None:
        self._plans_by_session_key.pop(session_id, None)
        self._plan_versions_by_session_key.pop(session_id, None)
        self._default_plan_key_by_session.pop(session_id, None)
        self._plans_by_session.pop(session_id, None)
        self._plan_versions_by_session.pop(session_id, None)

    @staticmethod
    def _normalize_plan_key(plan_key: str | None) -> str:
        value = str(plan_key or "").strip()
        return value or "default"

    def _resolve_plan_key_or_default(self, session_id: str, plan_key: str | None) -> str | None:
        if plan_key is not None and str(plan_key).strip():
            resolved = self._normalize_plan_key(plan_key)
            if resolved in self._plans_by_session_key.get(session_id, {}):
                return resolved
            return None
        default_key = self._default_plan_key_by_session.get(session_id)
        if default_key is not None:
            return default_key
        keys = sorted(self._plans_by_session_key.get(session_id, {}).keys())
        if keys:
            return keys[0]
        if session_id in self._plans_by_session:
            return "default"
        return None

    def _sync_legacy_session_views(self, session_id: str) -> None:
        default_key = self._resolve_plan_key_or_default(session_id, None)
        if default_key is None:
            self._plans_by_session.pop(session_id, None)
            self._plan_versions_by_session.pop(session_id, None)
            return
        session_plans = self._plans_by_session_key.get(session_id, {})
        session_versions = self._plan_versions_by_session_key.get(session_id, {})
        plan = session_plans.get(default_key)
        versions = session_versions.get(default_key, [])
        if plan is not None:
            self._plans_by_session[session_id] = plan
            self._plan_versions_by_session[session_id] = list(versions)

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
