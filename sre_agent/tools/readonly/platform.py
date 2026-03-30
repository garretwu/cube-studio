"""Read-only platform tools."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("cube_studio")
    if channel is None:
        channel = context.channels.get("platform")
    if channel is None:
        raise ToolValidationError("required channel is missing: cube_studio/platform")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def list_inference_services(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    _ = params
    channel = _require_channel(context)
    if hasattr(channel, "list_inference_services"):
        return await channel.list_inference_services()
    raise ToolValidationError("platform channel does not support list_inference_services")


async def get_service_status(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    service_name = _require_str(params, "service_name")
    if hasattr(channel, "get_service_status"):
        return await channel.get_service_status(service_name)
    raise ToolValidationError("platform channel does not support get_service_status")

