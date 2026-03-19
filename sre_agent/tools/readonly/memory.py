"""Purpose: Pattern/incident/config lookup.

Primary tools: search_incidents, search_patterns, get_config_baseline.
Channels used: memory.
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _require_memory(context: ToolExecutionContext) -> Any:
    memory = context.channels.get("memory")
    if memory is None:
        raise ToolValidationError("required channel is missing: memory")
    return memory


async def search_incidents(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    memory = _require_memory(context)
    query = _require_str(params, "query")
    limit = int(params.get("limit", 20))

    if hasattr(memory, "search_incidents"):
        return await _maybe_await(memory.search_incidents(query, limit=limit))
    if hasattr(memory, "find_incidents"):
        return await _maybe_await(memory.find_incidents(query, limit=limit))
    raise ToolValidationError("memory backend does not support incident search")


async def search_patterns(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    memory = _require_memory(context)
    query = _require_str(params, "query")
    limit = int(params.get("limit", 20))

    if hasattr(memory, "search_patterns"):
        return await _maybe_await(memory.search_patterns(query, limit=limit))
    if hasattr(memory, "find_patterns"):
        return await _maybe_await(memory.find_patterns(query, limit=limit))
    raise ToolValidationError("memory backend does not support pattern search")


async def get_config_baseline(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    memory = _require_memory(context)
    aidc_id = _require_str(params, "aidc_id")

    if hasattr(memory, "get_config_baseline"):
        return await _maybe_await(memory.get_config_baseline(aidc_id))
    if hasattr(memory, "get_baseline"):
        return await _maybe_await(memory.get_baseline(aidc_id))
    raise ToolValidationError("memory backend does not support config baseline lookup")


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value

