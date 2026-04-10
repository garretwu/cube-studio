from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from sre_agent.skills.registry import SkillDescriptor
from sre_agent.tools import ToolExecutionContext, ToolRegistry


@dataclass(frozen=True)
class ToolRunResult:
    step: int
    tool: str
    params: dict[str, Any]
    success: bool
    data: Any = None
    error: str = ""


@dataclass(frozen=True)
class SkillExecutionResult:
    skill_id: str
    status: str
    summary: str
    tool_runs: list[ToolRunResult] = field(default_factory=list)


class SkillExecutor:
    """Execute skill steps sequentially against ToolRegistry."""

    DEFAULT_ALLOWED_TOOLS = {
        "prometheus.query_instant",
        "k8s.list_pods",
        "gpu.get_metrics",
        "network.get_rdma_stats",
        "network.get_tc_qdisc",
        "network.get_nic_link_state",
        "network.get_nic_counters",
    }

    async def execute(
        self,
        *,
        skill: SkillDescriptor,
        registry: ToolRegistry,
        context: ToolExecutionContext,
        variables: dict[str, Any] | None = None,
        allowed_tools: set[str] | None = None,
        step_timeout_sec: float | None = None,
    ) -> SkillExecutionResult:
        vars_payload = variables or {}
        whitelist = allowed_tools or self.DEFAULT_ALLOWED_TOOLS
        runs: list[ToolRunResult] = []
        failed_steps: list[int] = []

        for idx, step in enumerate(skill.steps, start=1):
            if step.tool not in whitelist:
                reason = f"tool_not_allowed: {step.tool} not in v1 allowed tools"
                runs.append(
                    ToolRunResult(
                        step=idx,
                        tool=step.tool,
                        params=step.params,
                        success=False,
                        error=reason,
                    )
                )
                failed_steps.append(idx)
                continue

            try:
                resolved_params = self._resolve_params(step.params, vars_payload)
            except ValueError as exc:
                reason = str(exc)
                runs.append(
                    ToolRunResult(
                        step=idx,
                        tool=step.tool,
                        params=step.params,
                        success=False,
                        error=reason,
                    )
                )
                failed_steps.append(idx)
                continue

            try:
                if step_timeout_sec is not None:
                    result = await asyncio.wait_for(
                        registry.execute(step.tool, resolved_params, context),
                        timeout=step_timeout_sec,
                    )
                else:
                    result = await registry.execute(step.tool, resolved_params, context)
            except asyncio.TimeoutError:
                runs.append(
                    ToolRunResult(
                        step=idx,
                        tool=step.tool,
                        params=resolved_params,
                        success=False,
                        error=f"tool timed out after {step_timeout_sec}s",
                    )
                )
                failed_steps.append(idx)
                continue

            run = ToolRunResult(
                step=idx,
                tool=step.tool,
                params=resolved_params,
                success=result.success,
                data=result.data,
                error=result.error,
            )
            runs.append(run)
            if not result.success:
                failed_steps.append(idx)

        if failed_steps:
            status = "partial" if len(failed_steps) < len(skill.steps) else "failed"
            succeeded = len(runs) - len(failed_steps)
            first_error = next((r.error for r in runs if not r.success), "")
            return SkillExecutionResult(
                skill_id=skill.id,
                status=status,
                summary=f"completed {succeeded}/{len(runs)} steps; failed at steps {failed_steps}: {first_error}",
                tool_runs=runs,
            )

        return SkillExecutionResult(
            skill_id=skill.id,
            status="success",
            summary=f"success: {len(runs)} step(s) succeeded",
            tool_runs=runs,
        )

    @classmethod
    def _resolve_params(cls, params: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in params.items():
            out[key] = cls._resolve_value(value, variables)
        return out

    @classmethod
    def _resolve_value(cls, value: Any, variables: dict[str, Any]) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("${") and value.endswith("}"):
                var_name = value[2:-1].strip()
                if var_name not in variables:
                    raise ValueError(f"missing required variable: {var_name}")
                return variables[var_name]
            return value
        if isinstance(value, dict):
            return {k: cls._resolve_value(v, variables) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._resolve_value(item, variables) for item in value]
        return value
