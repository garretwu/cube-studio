"""Purpose: Execute remediation actions.

Primary tools: execute_plan.
Channels used: remediation.
Safety: write operations are expected to run with ToolRegistry approval.
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


async def execute_plan(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    remediation = context.channels.get("remediation")
    if remediation is None:
        raise ToolValidationError("required channel is missing: remediation")

    plan = params.get("plan")
    if plan is None:
        raise ToolValidationError("parameter 'plan' is required")

    if hasattr(remediation, "execute_plan"):
        return await remediation.execute_plan(plan)
    if hasattr(remediation, "execute"):
        return await remediation.execute(plan)
    raise ToolValidationError("remediation backend does not support execute_plan/execute")

