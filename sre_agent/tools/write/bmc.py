"""Write BMC tools for fan control."""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise ToolValidationError("required channel is missing: redfish")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "") or "").strip()
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


async def set_fan_control(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    """Set BMC fan control mode and PWM via Redfish web API.

    Args:
        params: Tool parameters including:
            - bmc_host: BMC IP address (required)
            - mode: Fan control mode, "Auto" or "Manual" (required)
            - pwm: PWM percentage 0-100 for Manual mode (optional)
            - fan_index: Fan index (optional, default 0)
            - fan_bp_index: Fan backplane index (optional, default 255)
            - verify_tls: TLS verification (optional, default True)
        context: Tool execution context with redfish channel

    Returns:
        Result from the BMC web API call
    """
    channel = _require_channel(context)
    bmc_host = _require_str(params, "bmc_host")
    mode = _require_str(params, "mode")

    fan_index = int(params.get("fan_index", 0))
    pwm = params.get("pwm")
    if pwm is not None:
        pwm = int(pwm)
    fan_bp_index = int(params.get("fan_bp_index", 0xFF))
    verify_tls = bool(params.get("verify_tls", True))

    if hasattr(channel, "set_web_fan_control"):
        value = await channel.set_web_fan_control(
            bmc_host=bmc_host,
            fan_index=fan_index,
            mode=mode,
            pwm=pwm,
            fan_bp_index=fan_bp_index,
            verify_tls=verify_tls,
        )
        return _unwrap_channel_result(value, action="set_web_fan_control")

    raise ToolValidationError("redfish channel does not support set_web_fan_control")