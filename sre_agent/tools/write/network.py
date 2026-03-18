from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _extract_result(value: Any) -> Any:
    if hasattr(value, "data"):
        return value.data
    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


async def switch_port_enable(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_str(params, "switch")
    interface = _require_str(params, "interface")

    if hasattr(switch, "bringup_port"):
        return _extract_result(switch.bringup_port(switch_name, interface))
    if hasattr(switch, "execute"):
        result = await switch.execute("bringup_port", {"switch": switch_name, "interface": interface})
        return _extract_result(result)
    raise ToolValidationError("switch backend does not support port enable")


async def switch_port_disable(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_str(params, "switch")
    interface = _require_str(params, "interface")

    if hasattr(switch, "shutdown_port"):
        return _extract_result(switch.shutdown_port(switch_name, interface))
    if hasattr(switch, "execute"):
        result = await switch.execute("shutdown_port", {"switch": switch_name, "interface": interface})
        return _extract_result(result)
    raise ToolValidationError("switch backend does not support port disable")


async def update_route(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_str(params, "switch")
    config_xml = _require_str(params, "config_xml")

    if hasattr(switch, "apply_raw_config"):
        return _extract_result(switch.apply_raw_config(switch_name, config_xml))
    if hasattr(switch, "execute"):
        result = await switch.execute("apply_raw_config", {"switch": switch_name, "config_xml": config_xml})
        return _extract_result(result)
    raise ToolValidationError("switch backend does not support route/config update")


async def set_bmc_vlan(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise ToolValidationError("required channel is missing: redfish")
    bmc_host = _require_str(params, "bmc_host")
    vlan_id = _require_str(params, "vlan_id")

    if hasattr(channel, "execute"):
        result = await channel.execute(
            "set_vlan",
            {"bmc_host": bmc_host, "vlan_id": vlan_id},
        )
        return _extract_result(result)
    raise ToolValidationError("redfish channel does not support set_vlan action")


async def set_bmc_mtu(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise ToolValidationError("required channel is missing: redfish")
    bmc_host = _require_str(params, "bmc_host")
    mtu = _require_str(params, "mtu")

    if hasattr(channel, "execute"):
        result = await channel.execute(
            "set_mtu",
            {"bmc_host": bmc_host, "mtu": mtu},
        )
        return _extract_result(result)
    raise ToolValidationError("redfish channel does not support set_mtu action")
