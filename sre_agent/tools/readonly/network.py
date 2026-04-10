"""Purpose: RDMA/network diagnostics and switch port counters.

Primary tools: get_rdma_stats, get_tc_qdisc, get_nic_link_state,
get_nic_counters, get_switch_port_counters.
Channels used: ssh, switch.
"""

from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _extract_output(value: Any, *, action: str) -> Any:
    success = getattr(value, "success", None)
    if success is not None and not bool(success):
        error_text = str(getattr(value, "error", "") or "").strip()
        if error_text:
            raise ToolValidationError(error_text)
        raise ToolValidationError(f"channel action failed: {action}")

    output = getattr(value, "output", None)
    error = getattr(value, "error", None)
    if output is None and error is None:
        return value
    return {"output": output or "", "error": error or ""}


def _optional_iface(params: dict[str, Any]) -> str:
    return str(params.get("iface", "") or "").strip()


async def get_rdma_stats(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    command = str(
        params.get(
            "command",
            "rdma link show; cat /proc/net/softnet_stat | head -n 4",
        )
    ).strip()
    if not command:
        raise ToolValidationError("parameter 'command' must not be blank")
    result = await ssh.run_command(node, command, use_sudo=False)
    return _extract_output(result, action="run_command")


async def get_tc_qdisc(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    iface = _optional_iface(params)
    command = f"tc qdisc show dev {iface}" if iface else "tc qdisc show"
    result = await ssh.run_command(node, command, use_sudo=False)
    payload = _extract_output(result, action="run_command")
    if isinstance(payload, dict):
        payload["node"] = node
        payload["iface"] = iface or None
        payload["source"] = "tc qdisc show"
    return payload


async def get_nic_link_state(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    iface = _optional_iface(params)
    if iface:
        command = (
            f"ip -s link show dev {iface}; "
            f"(command -v ethtool >/dev/null 2>&1 && ethtool {iface}) || true"
        )
    else:
        command = (
            "ip -s link show; "
            "(command -v ethtool >/dev/null 2>&1 && "
            "for dev in $(ls /sys/class/net); do "
            "echo \"### ethtool $dev\"; ethtool \"$dev\" 2>/dev/null || true; "
            "done) || true"
        )
    result = await ssh.run_command(node, command, use_sudo=False)
    payload = _extract_output(result, action="run_command")
    if isinstance(payload, dict):
        payload["node"] = node
        payload["iface"] = iface or None
        payload["source"] = "ip/ethtool link state"
    return payload


async def get_nic_counters(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    iface = _optional_iface(params)
    if iface:
        command = (
            "(command -v ethtool >/dev/null 2>&1 && ethtool -S {iface}) || "
            "(for f in rx_errors tx_errors rx_dropped tx_dropped rx_crc_errors; "
            "do p=\"/sys/class/net/{iface}/statistics/$f\"; [ -f \"$p\" ] && echo \"$f=$(cat \"$p\")\"; "
            "done)"
        ).format(iface=iface)
    else:
        command = (
            "for dev in $(ls /sys/class/net); do "
            "echo \"### iface=$dev\"; "
            "(command -v ethtool >/dev/null 2>&1 && ethtool -S \"$dev\" 2>/dev/null) || "
            "(for f in rx_errors tx_errors rx_dropped tx_dropped rx_crc_errors; "
            "do p=\"/sys/class/net/$dev/statistics/$f\"; [ -f \"$p\" ] && echo \"$f=$(cat \"$p\")\"; done); "
            "done"
        )
    result = await ssh.run_command(node, command, use_sudo=False)
    payload = _extract_output(result, action="run_command")
    if isinstance(payload, dict):
        payload["node"] = node
        payload["iface"] = iface or None
        payload["source"] = "ethtool/sysfs counters"
    return payload


async def get_switch_port_counters(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch_channel = context.channels.get("switch")
    if switch_channel is None:
        raise ToolValidationError("required channel is missing: switch")

    switch = _require_str(params, "switch")
    interface = _require_str(params, "interface")

    if hasattr(switch_channel, "execute"):
        result = await switch_channel.execute(
            "get_interface_status",
            {"switch": switch, "interface": interface},
        )
        return _extract_output(result, action="get_interface_status")
    if hasattr(switch_channel, "get_interface_status"):
        value = switch_channel.get_interface_status(switch, interface)
        if value is None:
            raise ToolValidationError("switch channel returned no interface status")
        return value
    raise ToolValidationError("switch channel does not support interface status read")

