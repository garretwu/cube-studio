"""Canary rollout execution for remediation plans."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Callable, Coroutine

from sre_agent.models.remediation import (
    CanaryCondition,
    RemediationPlan,
    RemediationResult,
)
from sre_agent.remediation.wal import RollbackJournal

LOGGER = logging.getLogger(__name__)

ProgressCallback = Callable[..., Coroutine[Any, Any, None]]


class CanaryExecutor:
    def __init__(self, wal: RollbackJournal, prometheus: Any | None = None) -> None:
        self.wal = wal
        self.prometheus = prometheus

    async def execute_with_canary(
        self,
        plan: RemediationPlan,
        targets: list[str],
        execute_step,
        *,
        progress_callback: ProgressCallback | None = None,
        session_id: str | None = None,
    ) -> RemediationResult:
        if not plan.canary or not plan.canary.enabled or not targets:
            return await execute_step(targets)

        canary = plan.canary

        # Build batches: progressive doubles each round, flat uses fixed size.
        batches = self._build_batches(targets, canary)
        max_batches = min(canary.max_batches, len(batches))
        if not getattr(canary, "progressive", True) and max_batches > 0 and len(batches) > max_batches:
            merged_batches = [list(batch) for batch in batches[:max_batches]]
            overflow_targets: list[str] = []
            for overflow_batch in batches[max_batches:]:
                overflow_targets.extend(list(overflow_batch))
            if overflow_targets:
                merged_batches[-1].extend(overflow_targets)
            batches = merged_batches
            max_batches = len(batches)
        suspect_process_count = len(targets)

        total_completed = 0
        for batch_index, batch in enumerate(batches[:max_batches]):
            current_batch_target = batch[0] if len(batch) == 1 else ""
            # ── Emit canary_batch_started ──────────────────────────────
            if progress_callback is not None:
                await progress_callback(
                    stage="canary_batch_started",
                    details={
                        "batch": f"canary-{batch_index + 1}",
                        "batch_index": batch_index + 1,
                        "batch_total": max_batches,
                        "batch_completed": batch_index,
                        "targets_in_batch": list(batch),
                        "suspect_process_count": suspect_process_count,
                        "planned_batch_total": max_batches,
                        "current_batch_target": current_batch_target,
                        "steps_completed": total_completed,
                        "steps_total": len(plan.steps),
                        "message": (
                            f"灰度批次 {batch_index + 1}/{max_batches} 开始，"
                            f"覆盖 {len(batch)} 个目标: {', '.join(batch)}"
                        ),
                    },
                )

            result = await execute_step(batch)
            total_completed += result.steps_completed

            if not result.success:
                # ── Emit canary_check_failed ───────────────────────────
                if progress_callback is not None:
                    await progress_callback(
                        stage="canary_check_failed",
                        details={
                            "batch": f"canary-{batch_index + 1}",
                            "batch_index": batch_index + 1,
                            "targets_in_batch": list(batch),
                            "suspect_process_count": suspect_process_count,
                            "planned_batch_total": max_batches,
                            "current_batch_target": current_batch_target,
                            "error": result.error or "execution failed",
                            "message": (
                                f"灰度批次 {batch_index + 1} 执行失败: "
                                f"{result.error or 'unknown error'}"
                            ),
                        },
                    )
                if canary.auto_rollback_on_regression:
                    await self.wal.recover_all()
                return result.model_copy(update={"rolled_back": True})

            # ── Wait monitor_duration before checking criteria ─────────
            await asyncio.sleep(canary.monitor_duration)

            if canary.success_criteria:
                if progress_callback is not None:
                    await progress_callback(
                        stage="canary_check_passed",
                        details={
                            "batch": f"canary-{batch_index + 1}",
                            "batch_index": batch_index + 1,
                            "suspect_process_count": suspect_process_count,
                            "planned_batch_total": max_batches,
                            "current_batch_target": current_batch_target,
                            "message": (
                                f"验证灰度批次 {batch_index + 1} 的成功条件 "
                                f"(观察窗口 {canary.monitor_duration}s)"
                            ),
                        },
                    )
                checks = [
                    await self._check_canary_condition(item)
                    for item in canary.success_criteria
                ]
                passed = all(checks) if canary.criteria_mode == "all" else any(checks)
                if not passed:
                    # ── Emit canary_check_failed ───────────────────────
                    if progress_callback is not None:
                        await progress_callback(
                            stage="canary_check_failed",
                            details={
                                "batch": f"canary-{batch_index + 1}",
                                "batch_index": batch_index + 1,
                                "targets_in_batch": list(batch),
                                "suspect_process_count": suspect_process_count,
                                "planned_batch_total": max_batches,
                                "current_batch_target": current_batch_target,
                                "message": (
                                    f"灰度批次 {batch_index + 1} 验证失败，"
                                    f"成功条件未满足"
                                ),
                            },
                        )
                    if canary.auto_rollback_on_regression:
                        await self.wal.recover_all()
                    return RemediationResult(
                        plan_id=plan.plan_id,
                        success=False,
                        steps_completed=total_completed,
                        steps_total=len(plan.steps),
                        rolled_back=True,
                        error="canary verification failed",
                    )

            # ── Emit canary_batch_completed ────────────────────────────
            if progress_callback is not None:
                await progress_callback(
                    stage="canary_batch_completed",
                    details={
                        "batch": f"canary-{batch_index + 1}",
                        "batch_index": batch_index + 1,
                        "batch_total": max_batches,
                        "batch_completed": batch_index + 1,
                        "targets_in_batch": list(batch),
                        "suspect_process_count": suspect_process_count,
                        "planned_batch_total": max_batches,
                        "current_batch_target": current_batch_target,
                        "steps_completed": total_completed,
                        "steps_total": len(plan.steps),
                        "message": (
                            f"灰度批次 {batch_index + 1}/{max_batches} 完成，"
                            f"覆盖 {len(batch)} 个目标"
                        ),
                    },
                )

        return RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=len(plan.steps),
            steps_total=len(plan.steps),
        )

    def _build_batches(
        self, targets: list[str], canary: Any
    ) -> list[list[str]]:
        """Build batch partitions from targets.

        When progressive=True, batch N has size min(len(targets), base * 2^(N-1)).
        When progressive=False, every batch has the same size (flat partition).
        """
        total = len(targets)
        base_size = max(1, int(total * canary.target_percentage))

        if not getattr(canary, "progressive", True):
            # Flat: fixed-size batches
            return [targets[i : i + base_size] for i in range(0, total, base_size)]

        # Progressive: base, base*2, base*4, ...
        batches: list[list[str]] = []
        offset = 0
        while offset < total:
            batch_size = min(total - offset, base_size * (2 ** len(batches)))
            batch_size = max(1, batch_size)
            batches.append(targets[offset : offset + batch_size])
            offset += batch_size
        return batches

    async def _check_canary_condition(self, condition: CanaryCondition) -> bool:
        if self.prometheus is None:
            return True
        if hasattr(self.prometheus, "query_instant"):
            value = await self.prometheus.query_instant(condition.metric)
        else:
            value = 0
        if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
            return True  # 指标缺失或不可用时，不阻断灰度
        return _compare(value, condition.operator, condition.value)


def _compare(left: Any, operator: str, right: Any) -> bool:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    raise ValueError(f"unsupported operator: {operator}")
