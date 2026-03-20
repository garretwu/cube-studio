"""Purpose: Graph traversal, neighbor, path queries.

Primary tools: query_entities, get_path, get_blast_radius.
Channels used: ontology.
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext, name: str) -> Any:
    channel = context.channels.get(name)
    if channel is None:
        raise ToolValidationError(f"required channel is missing: {name}")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def query_entities(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "ontology")
    entity_type = _require_str(params, "entity_type")
    filters = params.get("filters")
    if not isinstance(filters, dict):
        filters = {}
    return await channel.query(entity_type, filters=filters)


async def get_path(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "ontology")
    from_id = _require_str(params, "from_id")
    to_id = _require_str(params, "to_id")
    return await channel.get_path(from_id, to_id)


async def get_blast_radius(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "ontology")
    entity_id = _require_str(params, "entity_id")
    return await channel.get_blast_radius(entity_id)
