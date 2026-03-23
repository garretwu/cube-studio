"""Canary rollout execution for remediation plans."""

from __future__ import annotations

import asyncio
from typing import Any

from sre_agent.models.remediation import CanaryCondition, RemediationPlan, RemediationResult
from sre_agent.remediation.wal import RollbackJournal


class CanaryExecutor:
    def __init__(self, wal: RollbackJournal, prometheus: Any | None = None) -> None:
        self.wal = wal
        self.prometheus = prometheus

    async def execute_with_canary(
        self,
        plan: RemediationPlan,
        targets: list[str],
        execute_step,
    ) -> RemediationResult:
        if not plan.canary or not plan.canary.enabled or not targets:
            return await execute_step(targets)

        canary = plan.canary
        batch_size = max(1, int(len(targets) * canary.target_percentage))
        batches = [targets[i : i + batch_size] for i in range(0, len(targets), batch_size)]

        total_completed = 0
        for batch in batches[: canary.max_batches]:
            result = await execute_step(batch)
            total_completed += result.steps_completed
            if not result.success:
                if canary.auto_rollback_on_regression:
                    await self.wal.recover_all()
                return result.model_copy(update={"rolled_back": True})

            await asyncio.sleep(0)
            if canary.success_criteria:
                checks = [await self._check_canary_condition(item) for item in canary.success_criteria]
                passed = all(checks) if canary.criteria_mode == "all" else any(checks)
                if not passed:
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

        return RemediationResult(
            plan_id=plan.plan_id,
            success=True,
            steps_completed=len(plan.steps),
            steps_total=len(plan.steps),
        )

    async def _check_canary_condition(self, condition: CanaryCondition) -> bool:
        if self.prometheus is None:
            return True
        if hasattr(self.prometheus, "query_instant"):
            value = await self.prometheus.query_instant(condition.metric)
        else:
            value = 0
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
