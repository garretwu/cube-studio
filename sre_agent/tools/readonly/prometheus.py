"""Purpose: PromQL instant/range queries.

Primary tools: query_instant, query_range.
Channels used: prometheus (fallback: metrics).
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _get_prometheus_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("prometheus")
    if channel is None:
        channel = context.channels.get("metrics")
    if channel is None:
        raise ToolValidationError("required channel is missing: prometheus/metrics")
    return channel


async def query_instant(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _get_prometheus_channel(context)
    promql = _require_str(params, "promql")
    return await channel.query_instant(promql)


async def query_range(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _get_prometheus_channel(context)
    promql = _require_str(params, "promql")
    if "start" not in params:
        raise ToolValidationError("parameter 'start' is required")
    if "end" not in params:
        raise ToolValidationError("parameter 'end' is required")
    start = params["start"]
    end = params["end"]
    step = str(params.get("step", "15s")).strip() or "15s"
    return await channel.query_range(promql, start, end, step=step)

