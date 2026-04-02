"""Read-only knowledge tools."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("knowledge")
    if channel is None:
        raise ToolValidationError("required channel is missing: knowledge")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def search(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    query = _require_str(params, "query")
    category_raw = params.get("category")
    category = str(category_raw).strip() if category_raw is not None else None
    if category == "":
        category = None
    top_k = int(params.get("top_k", 5))
    if hasattr(channel, "search"):
        return await channel.search(query, category=category, top_k=top_k)
    raise ToolValidationError("knowledge channel does not support search")


async def search_runbook(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    symptom = _require_str(params, "symptom")
    if hasattr(channel, "search_runbook"):
        return await channel.search_runbook(symptom)
    if hasattr(channel, "search_runbooks"):
        return await channel.search_runbooks(symptom)
    raise ToolValidationError("knowledge channel does not support search_runbook")

