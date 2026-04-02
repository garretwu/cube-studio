"""Read-only log tools."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("log")
    if channel is None:
        raise ToolValidationError("required channel is missing: log")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def query(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    query_text = _require_str(params, "query")
    limit = int(params.get("limit", 200))
    if hasattr(channel, "query_logs"):
        return await channel.query_logs(query_text, limit=limit)
    if hasattr(channel, "query_logs_range_only"):
        return await channel.query_logs_range_only(query_text, limit=limit)
    raise ToolValidationError("log channel does not support query_logs")


async def read_pod(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    pod = _require_str(params, "pod")
    namespace = _require_str(params, "namespace")
    tail = int(params.get("tail", 200))
    since_raw = params.get("since")
    since = str(since_raw).strip() if since_raw is not None else None
    if since == "":
        since = None
    if hasattr(channel, "read_pod_logs"):
        return await channel.read_pod_logs(pod, namespace, tail=tail, since=since)
    raise ToolValidationError("log channel does not support read_pod_logs")


async def read_system(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    node = _require_str(params, "node")
    log_path = str(params.get("log_path", "/var/log/syslog")).strip() or "/var/log/syslog"
    tail = int(params.get("tail", 100))
    if hasattr(channel, "read_system_log"):
        return await channel.read_system_log(node, log_path=log_path, tail=tail)
    raise ToolValidationError("log channel does not support read_system_log")

