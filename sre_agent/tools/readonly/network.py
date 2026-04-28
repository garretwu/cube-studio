"""Purpose: RDMA/network diagnostics and switch port counters.

Primary tools: get_rdma_stats, get_tc_qdisc, find_process,
get_nic_link_state, get_nic_counters, get_switch_port_counters.
Channels used: ssh, switch.
"""

from __future__ import annotations

import re
import shlex
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


def _require_safe_cli_token(params: dict[str, Any], key: str) -> str:
    value = _require_str(params, key)
    if not re.fullmatch(r"[A-Za-z0-9/._:-]+", value):
        raise ToolValidationError(f"parameter {key!r} contains unsafe CLI characters")
    return value


def _parse_car_cir_lines(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "car" not in line.casefold() or "cir" not in line.casefold():
            continue
        match = re.search(r"\bcar\b.*?\bcir\s+(\d+)", line, flags=re.IGNORECASE)
        if not match:
            continue
        direction_match = re.search(r"\b(inbound|outbound)\b", line, flags=re.IGNORECASE)
        entries.append(
            {
                "line": line,
                "cir": int(match.group(1)),
                "direction": direction_match.group(1).lower() if direction_match else None,
            }
        )
    return entries


def _extract_policy_bindings(text: str) -> list[dict[str, str]]:
    bindings: list[dict[str, str]] = []
    for match in re.finditer(
        r"\bqos apply policy\s+([A-Za-z0-9._:-]+)\s+(inbound|outbound)\b",
        str(text or ""),
        flags=re.IGNORECASE,
    ):
        policy_name = str(match.group(1) or "").strip()
        direction = str(match.group(2) or "").strip().lower()
        if policy_name and direction:
            bindings.append({"name": policy_name, "direction": direction})
    return bindings


def _extract_qos_gts_lines(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.search(
            r"\bqos gts queue\s+(\d+)\s+cir\s+(\d+)(?:\s+cbs\s+(\d+))?\b",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        entries.append(
            {
                "queue": str(match.group(1) or "").strip(),
                "cir": int(match.group(2)),
                "cbs": int(match.group(3)) if match.group(3) else None,
                "line": line,
            }
        )
    return entries


def _extract_abnormal_qos_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.casefold()
        if (
            lowered.startswith("qos apply policy ")
            or lowered.startswith("qos gts ")
            or lowered.startswith("qos lr ")
            or lowered.startswith("qos car ")
        ):
            lines.append(line)
    if not re.search(r"\bqos trust dscp\b", text, flags=re.IGNORECASE):
        lines.append("MISSING: qos trust dscp")
    return lines


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


async def find_process(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    ssh = context.channels.get("ssh")
    if ssh is None:
        raise ToolValidationError("required channel is missing: ssh")

    node = _require_str(params, "node")
    pattern = _require_str(params, "pattern")
    grep_pattern = shlex.quote(pattern)
    command = (
        "ps -eo pid=,comm=,args= "
        f"| grep -E -- {grep_pattern} "
        "| grep -v -E 'grep -E --' || true"
    )
    result = await ssh.run_command(node, command, use_sudo=False)
    payload = _extract_output(result, action="run_command")

    output_text = ""
    if isinstance(payload, dict):
        output_text = str(payload.get("output") or "")
    else:
        output_text = str(payload or "")

    matches: list[dict[str, Any]] = []
    for raw_line in output_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^(\d+)\s+(\S+)\s*(.*)$", line)
        if not match:
            continue
        matches.append(
            {
                "pid": int(match.group(1)),
                "process": match.group(2),
                "command": match.group(3).strip(),
            }
        )

    structured = {
        "node": node,
        "pattern": pattern,
        "count": len(matches),
        "matches": matches,
        "source": "ps -eo",
        "output": output_text,
    }
    if isinstance(payload, dict) and payload.get("error"):
        structured["error"] = payload.get("error")
    return structured


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

    switch = _require_safe_cli_token(params, "switch")
    interface = _require_safe_cli_token(params, "interface")

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


async def get_switch_qos_config(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    switch_channel = context.channels.get("switch")
    if switch_channel is None:
        raise ToolValidationError("required channel is missing: switch")

    switch = _require_safe_cli_token(params, "switch")
    interface = _require_safe_cli_token(params, "interface")
    commands = [f"interface {interface}", "display this"]

    outputs: list[dict[str, Any]] = []
    combined_output: list[str] = []
    if hasattr(switch_channel, "apply_cli_commands"):
        result = switch_channel.apply_cli_commands(switch, commands)
        payload = _extract_output(result, action="apply_cli_commands")
        output_text = str(payload.get("output") if isinstance(payload, dict) else payload or "")
        outputs.append({"command": "\n".join(commands), "output": output_text})
        combined_output.append(output_text)
    else:
        for command in commands:
            if hasattr(switch_channel, "run_cli_execution"):
                result = switch_channel.run_cli_execution(switch, command)
            elif hasattr(switch_channel, "execute"):
                result = await switch_channel.execute(
                    "run_cli_execution",
                    {"switch": switch, "command": command},
                )
            else:
                raise ToolValidationError("switch channel does not support CLI execution")

            payload = _extract_output(result, action="run_cli_execution")
            output_text = str(payload.get("output") if isinstance(payload, dict) else payload or "")
            outputs.append({"command": command, "output": output_text})
            combined_output.append(output_text)

    text = "\n".join(combined_output)
    car_cir = _parse_car_cir_lines(text)
    result: dict[str, Any] = {
        "switch": switch,
        "interface": interface,
        "commands": outputs,
        "car_cir": car_cir,
        "applied_policies": _extract_policy_bindings(text),
        "qos_gts": _extract_qos_gts_lines(text),
        "trust_dscp": bool(re.search(r"\bqos trust dscp\b", text, flags=re.IGNORECASE)),
        "abnormal_config_lines": _extract_abnormal_qos_lines(text),
        "source": "H3C CLI display qos/current-configuration",
    }
    return result
