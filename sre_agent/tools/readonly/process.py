"""Purpose: generic process discovery via SSH ps output.

Primary tools: find.
Channels used: ssh.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _extract_output(value: Any) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError("channel action failed: run_command")

    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


def _resolve_node(params: dict[str, Any], context: ToolExecutionContext) -> str:
    explicit = str(params.get("node", "") or "").strip()
    if explicit:
        return explicit
    fallback = str(context.metadata.get("ttft_external_process_default_node", "") or "").strip()
    if fallback:
        return fallback
    raise ToolValidationError(
        "parameter 'node' is required (or set context.metadata.ttft_external_process_default_node)"
    )


async def find(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _resolve_node(params, context)
    pattern = _require_str(params, "pattern")
    grep_pattern = shlex.quote(pattern)
    command = (
        "ps -eo pid=,comm=,args= "
        f"| grep -E -- {grep_pattern} "
        "| grep -v -E 'grep -E --' || true"
    )
    result = await ssh.run_command(node, command, use_sudo=False)
    payload = _extract_output(result)

    output_text = ""
    if isinstance(payload, dict):
        output_text = str(payload.get("output") or "")
    else:
        output_text = str(payload or "")

    matches: list[dict[str, Any]] = []
    for raw_line in output_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\s+(\S+)\s*(.*)$", line)
        if not match:
            continue
        matches.append(
            {
                "pid": int(match.group(1)),
                "process": match.group(2),
                "command": match.group(3).strip(),
            }
        )

    structured = {
        "node": node,
        "pattern": pattern,
        "count": len(matches),
        "matches": matches,
        "source": "ps -eo",
        "output": output_text,
    }
    if isinstance(payload, dict) and payload.get("error"):
        structured["error"] = payload.get("error")
    return structured

