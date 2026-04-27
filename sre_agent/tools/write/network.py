"""Purpose: Switch port enable/disable, route update, and network qdisc cleanup.

Primary tools: switch_port_enable, switch_port_disable, update_route,
set_bmc_vlan, set_bmc_mtu.
Channels used: switch, redfish.
Safety: write operations are expected to run with ToolRegistry approval.
BMC VLAN/MTU writes are hard-blocked by registry policy unless explicit
network-write approval metadata is present.
"""

from __future__ import annotations

import re
from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _is_unknown_action_error(error: str) -> bool:
    return "unknown action" in error.casefold()


def _require_safe_cli_token(params: dict[str, Any], key: str) -> str:
    value = _require_str(params, key)
    if not re.fullmatch(r"[A-Za-z0-9/._:-]+", value):
        raise ToolValidationError(f"parameter {key!r} contains unsafe CLI characters")
    return value


def _optional_bool(params: dict[str, Any], key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n", ""}:
            return False
    return bool(value)


def _qos_directions(raw: Any) -> list[str]:
    direction = str(raw or "both").strip().lower()
    if direction in {"both", "all"}:
        return ["inbound", "outbound"]
    if direction in {"in", "ingress"}:
        return ["inbound"]
    if direction in {"out", "egress"}:
        return ["outbound"]
    if direction in {"inbound", "outbound"}:
        return [direction]
    raise ToolValidationError("parameter 'direction' must be inbound, outbound, or both")


def _extract_result(value: Any, *, action: str, unsupported_message: str | None = None) -> Any:
    success = getattr(value, "success", None)
    if success is not None:
        if not bool(success):
            error_text = str(getattr(value, "error", "") or "").strip()
            if unsupported_message and _is_unknown_action_error(error_text):
                raise ToolValidationError(unsupported_message)
            if error_text:
                raise ToolValidationError(error_text)
            raise ToolValidationError(f"switch/redfish channel action failed: {action}")
        data = getattr(value, "data", None)
        if data is not None:
            return data
        output = getattr(value, "output", None)
        error = getattr(value, "error", None)
        if output is None and error is None:
            return value
        return {"output": output or "", "error": error or ""}

    data = getattr(value, "data", None)
    if data is not None:
        return data
    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


def _extract_ssh_result(value: Any, *, action: str) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError(f"ssh action failed: {action}")

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
        return _extract_result(
            switch.bringup_port(switch_name, interface),
            action="bringup_port",
        )
    if hasattr(switch, "execute"):
        result = await switch.execute("bringup_port", {"switch": switch_name, "interface": interface})
        return _extract_result(result, action="bringup_port")
    raise ToolValidationError("switch backend does not support port enable")


async def switch_port_disable(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_str(params, "switch")
    interface = _require_str(params, "interface")

    if hasattr(switch, "shutdown_port"):
        return _extract_result(
            switch.shutdown_port(switch_name, interface),
            action="shutdown_port",
        )
    if hasattr(switch, "execute"):
        result = await switch.execute("shutdown_port", {"switch": switch_name, "interface": interface})
        return _extract_result(result, action="shutdown_port")
    raise ToolValidationError("switch backend does not support port disable")


async def update_route(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_str(params, "switch")
    config_xml = _require_str(params, "config_xml")

    if hasattr(switch, "apply_raw_config"):
        return _extract_result(
            switch.apply_raw_config(switch_name, config_xml),
            action="apply_raw_config",
        )
    if hasattr(switch, "execute"):
        result = await switch.execute("apply_raw_config", {"switch": switch_name, "config_xml": config_xml})
        return _extract_result(result, action="apply_raw_config")
    raise ToolValidationError("switch backend does not support route/config update")


async def repair_switch_qos_config(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch = context.channels.get("switch")
    if switch is None:
        raise ToolValidationError("required channel is missing: switch")

    switch_name = _require_safe_cli_token(params, "switch")
    interface = _require_safe_cli_token(params, "interface")
    mode = str(params.get("mode") or "remove_car").strip().lower()
    directions = _qos_directions(params.get("direction"))
    save = _optional_bool(params, "save", default=False)

    commands = ["system-view", f"interface {interface}"]
    if mode == "remove_car":
        commands.extend(f"undo qos car {direction}" for direction in directions)
    elif mode == "set_car":
        cir_raw = params.get("cir")
        try:
            cir = int(str(cir_raw).strip())
        except (TypeError, ValueError):
            raise ToolValidationError("parameter 'cir' must be an integer for mode='set_car'") from None
        if cir <= 0:
            raise ToolValidationError("parameter 'cir' must be positive")
        for direction in directions:
            commands.append(f"undo qos car {direction}")
            commands.append(f"qos car {direction} any cir {cir}")
    elif mode == "unapply_policy":
        policy_name = _require_safe_cli_token(params, "policy_name")
        commands.extend(f"undo qos apply policy {policy_name} {direction}" for direction in directions)
    else:
        raise ToolValidationError("parameter 'mode' must be remove_car, set_car, or unapply_policy")

    if save:
        commands.append("save force")

    if hasattr(switch, "apply_cli_commands"):
        return _extract_result(
            switch.apply_cli_commands(switch_name, commands),
            action="apply_cli_commands",
        )
    if hasattr(switch, "execute"):
        result = await switch.execute("apply_cli_commands", {"switch": switch_name, "commands": commands})
        return _extract_result(result, action="apply_cli_commands")
    raise ToolValidationError("switch backend does not support CLI configuration")


async def clear_tc_qdisc(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    iface = _require_str(params, "iface")
    parent = str(params.get("parent") or "").strip()
    handle = str(params.get("handle") or "").strip()
    kind = str(params.get("kind") or "root").strip().lower()
    if kind and kind not in {"root", "ingress", "clsact"}:
        raise ToolValidationError(f"unsupported qdisc scope: {kind!r}")
    if parent and handle:
        raise ToolValidationError("parameters 'parent' and 'handle' are mutually exclusive")

    if parent:
        command = f"tc qdisc del dev {iface} parent {parent}"
    elif handle:
        command = f"tc qdisc del dev {iface} handle {handle}"
    else:
        command = f"tc qdisc del dev {iface} {kind or 'root'}"

    result = await ssh.run_command(node, command, use_sudo=True)
    payload = _extract_ssh_result(result, action="run_command")
    if isinstance(payload, dict):
        payload["node"] = node
        payload["iface"] = iface
        payload["parent"] = parent or None
        payload["handle"] = handle or None
        payload["kind"] = None if parent or handle else (kind or "root")
        payload["source"] = "tc qdisc del"
    return payload


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
        return _extract_result(
            result,
            action="set_vlan",
            unsupported_message="redfish channel does not support set_vlan action",
        )
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
        return _extract_result(
            result,
            action="set_mtu",
            unsupported_message="redfish channel does not support set_mtu action",
        )
    raise ToolValidationError("redfish channel does not support set_mtu action")
