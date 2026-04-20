"""Purpose: execute arbitrary commands on a node via SSH channel."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_timeout(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        timeout = int(text)
    except Exception as exc:  # noqa: BLE001
        raise ToolValidationError("parameter 'timeout' must be an integer") from exc
    if timeout <= 0:
        raise ToolValidationError("parameter 'timeout' must be > 0")
    return timeout


def _extract_output(value: Any) -> dict[str, Any]:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError("channel action failed: run_command")

    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return {"raw": value}
    return {"output": output or "", "error": error or ""}


async def run_command(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    command = _require_str(params, "command")
    use_sudo = _parse_bool(params.get("use_sudo"), default=False)
    timeout = _parse_timeout(params.get("timeout"))

    if timeout is None:
        result = await ssh.run_command(node, command, use_sudo=use_sudo)
    else:
        try:
            result = await ssh.run_command(node, command, timeout=timeout, use_sudo=use_sudo)
        except TypeError:
            # Compatibility with simple stub channels which don't expose timeout.
            result = await ssh.run_command(node, command, use_sudo=use_sudo)

    payload = _extract_output(result)
    if "raw" in payload:
        return {
            "node": node,
            "command": command,
            "use_sudo": use_sudo,
            "timeout": timeout,
            "result": payload["raw"],
        }
    return {
        "node": node,
        "command": command,
        "use_sudo": use_sudo,
        "timeout": timeout,
        "output": payload.get("output", ""),
        "error": payload.get("error", ""),
    }

