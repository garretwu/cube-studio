"""Read-only BMC tools."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise ToolValidationError("required channel is missing: redfish")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _unwrap_channel_result(value: Any, *, action: str) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError(f"channel action failed: {action}")
    data = getattr(value, "data", None)
    if data is not None:
        return data
    output = getattr(value, "output", None)
    if output is not None:
        return output
    return value


async def get_info(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_bmc_info"):
        value = await channel.get_bmc_info(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_bmc_info")
    raise ToolValidationError("redfish channel does not support get_bmc_info")


async def get_thermal(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_thermal"):
        value = await channel.get_thermal(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_thermal")
    raise ToolValidationError("redfish channel does not support get_thermal")


async def get_power(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    verify_tls = bool(params.get("verify_tls", True))
    if hasattr(channel, "get_power"):
        value = await channel.get_power(bmc_host, verify_tls=verify_tls)
        return _unwrap_channel_result(value, action="get_power")
    raise ToolValidationError("redfish channel does not support get_power")

