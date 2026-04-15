"""Purpose: Execute remediation actions.

Primary tools: execute_plan, kill_process.
Channels used: remediation, ssh.
Safety: write operations are expected to run with ToolRegistry approval.
"""

from __future__ import annotations

import shlex
from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _coerce_signal(params: dict[str, Any]) -> str:
    signal = str(params.get("signal") or "TERM").strip().upper()
    allowed = {"TERM", "KILL", "INT", "HUP"}
    if signal not in allowed:
        raise ToolValidationError(f"unsupported signal: {signal!r}")
    return signal


def _extract_output(value: Any) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError("ssh action failed")

    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


def _resolve_kill_target_from_entity_id(params: dict[str, Any]) -> tuple[str, str]:
    raw_entity_id = str(params.get("entity_id", "") or "").strip()
    if not raw_entity_id:
        return "", ""

    if not raw_entity_id.lower().startswith("proc:"):
        raise ToolValidationError(
            "kill_process entity_id must start with 'proc:' when pid/pid_or_name/process_name is missing"
        )

    target = raw_entity_id.split(":", 1)[1].strip()
    if not target:
        raise ToolValidationError("kill_process entity_id target is empty")

    if target.isdigit():
        return target, ""
    return "", target


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


async def kill_process(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    signal = _coerce_signal(params)

    pid_raw = params.get("pid")
    pid_or_name_raw = params.get("pid_or_name")
    process_name_raw = params.get("process_name")

    pid_text = str(pid_raw).strip() if pid_raw is not None else ""
    pid_or_name = str(pid_or_name_raw).strip() if pid_or_name_raw is not None else ""
    process_name = str(process_name_raw).strip() if process_name_raw is not None else ""

    target = pid_text or pid_or_name or process_name
    if not target:
        pid_text, pid_or_name = _resolve_kill_target_from_entity_id(params)
        target = pid_text or pid_or_name
    if not target:
        raise ToolValidationError("kill_process requires pid, pid_or_name, process_name, or entity_id='proc:<target>'")

    if pid_text:
        command = f"kill -{signal} -- {shlex.quote(pid_text)}"
    elif pid_or_name:
        if pid_or_name.isdigit():
            command = f"kill -{signal} -- {shlex.quote(pid_or_name)}"
        else:
            command = f"pkill -{signal} -f -- {shlex.quote(pid_or_name)}"
    else:
        command = f"pkill -{signal} -f -- {shlex.quote(process_name)}"

    result = await ssh.run_command(node, command, use_sudo=True)
    return _extract_output(result)
