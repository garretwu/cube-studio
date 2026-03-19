"""Purpose: nvidia-smi, DCGM metrics.

Primary tools: get_metrics, get_processes.
Channels used: ssh.
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _extract_output(value: Any) -> Any:
    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


async def get_metrics(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    command = str(
        params.get(
            "command",
            "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu "
            "--format=csv,noheader,nounits",
        )
    ).strip()
    if not command:
        raise ToolValidationError("parameter 'command' must not be blank")

    result = await ssh.run_command(node, command, use_sudo=False)
    return _extract_output(result)


async def get_processes(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    command = str(
        params.get(
            "command",
            "nvidia-smi --query-compute-apps=pid,process_name,gpu_uuid,used_gpu_memory "
            "--format=csv,noheader,nounits",
        )
    ).strip()
    if not command:
        raise ToolValidationError("parameter 'command' must not be blank")

    result = await ssh.run_command(node, command, use_sudo=False)
    return _extract_output(result)

