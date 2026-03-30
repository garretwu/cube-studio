from __future__ import annotations

from typing import Any, Iterable

from sre_agent.tools.registry import SafetyLevel, ToolRegistry


def list_tool_definitions(
    registry: ToolRegistry,
    *,
    safety_levels: Iterable[SafetyLevel | str] | None = None,
    include_schema: bool = True,
) -> list[dict[str, Any]]:
    items = registry.list_tools(safety_levels=safety_levels)
    payload: list[dict[str, Any]] = []
    for item in items:
        entry: dict[str, Any] = {
            "name": item.name,
            "description": item.description,
            "safety_level": item.safety_level.value,
            "tags": list(item.tags),
            "needs_approval": item.needs_approval,
        }
        if include_schema:
            entry["params_schema"] = item.params_schema
        payload.append(entry)
    return payload


def list_tool_names(
    registry: ToolRegistry,
    *,
    safety_levels: Iterable[SafetyLevel | str] | None = None,
) -> list[str]:
    return [item["name"] for item in list_tool_definitions(registry, safety_levels=safety_levels, include_schema=False)]

