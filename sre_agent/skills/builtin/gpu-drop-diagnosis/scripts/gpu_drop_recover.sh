#!/usr/bin/env bash
# gpu_drop_recover.sh — GPU 掉卡诊断 / 注入 / 恢复脚本
#
# 这个脚本默认执行 doctor 流程：
# - 判断当前是否少卡
# - 判断是 PCIe 设备消失，还是 PCIe 还在但 nvidia-smi inventory 少卡
# - 在允许时尝试 rescan / reset 恢复
#
# 也保留 drop / recover / list / status 等显式动作。

set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/root/workspace/.cube/bin/python}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SKILL_DIR}/../../../.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec "$PYTHON_BIN" - "$@" <<'PY'
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _env_str(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def preprocess_env_args(argv: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in argv:
        text = str(item).strip()
        if (
            text.startswith("SRE_GPU_DROP_")
            and "=" in text
            and not text.startswith("--")
        ):
            key, value = text.split("=", 1)
            os.environ[key] = value
            continue
        cleaned.append(item)
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Diagnose, inject, and recover GPU missing-card incidents.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="doctor",
        help="Action to execute. Defaults to doctor. If a host/IP is passed here, doctor mode is assumed.",
    )
    parser.add_argument("--gpu-index", default="", help="Target GPU index from nvidia-smi.")
    parser.add_argument("--bdf", default="", help="Target PCIe BDF, e.g. 0000:41:00.0.")
    parser.add_argument("--wait-seconds", type=int, default=15, help="Verification wait window.")
    parser.add_argument("--skip-verify", action="store_true", help="Skip verification after action.")
    parser.add_argument(
        "--expected-gpu-count",
        type=int,
        default=_env_int("SRE_GPU_DROP_EXPECTED_GPU_COUNT", 0),
        help="Expected healthy GPU count. Defaults to SRE_GPU_DROP_EXPECTED_GPU_COUNT or inferred count.",
    )
    parser.add_argument(
        "--auto-recover",
        action=argparse.BooleanOptionalAction,
        default=_env_str("SRE_GPU_DROP_AUTO_RECOVER", "1") not in {"0", "false", "False"},
        help="During doctor, automatically try rescan/reset recovery when missing GPUs are detected.",
    )
    parser.add_argument("--node", default=_env_str("SRE_GPU_DROP_NODE"), help="Logical node name.")
    parser.add_argument("--host", default=_env_str("SRE_GPU_DROP_HOST"), help="SSH target host. Empty means local execution.")
    parser.add_argument("--ssh-user", default=_env_str("SRE_GPU_DROP_SSH_USER"), help="SSH username.")
    parser.add_argument("--ssh-password", default=_env_str("SRE_GPU_DROP_SSH_PASSWORD"), help="SSH password.")
    parser.add_argument("--ssh-port", type=int, default=_env_int("SRE_GPU_DROP_SSH_PORT", 22), help="SSH port.")
    parser.add_argument("--ssh-timeout", type=int, default=_env_int("SRE_GPU_DROP_SSH_TIMEOUT", 30), help="SSH command timeout.")
    parser.add_argument(
        "--pause-gpu-clients",
        action=argparse.BooleanOptionalAction,
        default=_env_str("SRE_GPU_DROP_PAUSE_CLIENTS", "1") not in {"0", "false", "False"},
        help="Temporarily pause monitoring/clients before gpu-reset.",
    )
    return parser


def normalize_action_and_target(args: argparse.Namespace) -> argparse.Namespace:
    if not getattr(args, "node", ""):
        args.node = _env_str("SRE_GPU_DROP_NODE")
    if not getattr(args, "host", ""):
        args.host = _env_str("SRE_GPU_DROP_HOST")
    if not getattr(args, "ssh_user", ""):
        args.ssh_user = _env_str("SRE_GPU_DROP_SSH_USER")
    if not getattr(args, "ssh_password", ""):
        args.ssh_password = _env_str("SRE_GPU_DROP_SSH_PASSWORD")
    if not getattr(args, "expected_gpu_count", 0):
        args.expected_gpu_count = _env_int("SRE_GPU_DROP_EXPECTED_GPU_COUNT", 0)

    allowed_actions = {"doctor", "list", "status", "drop", "recover"}
    action = str(args.action or "").strip()
    if not action:
        args.action = "doctor"
        return args
    if action in allowed_actions:
        return args
    if not args.node:
        args.node = action
    if not args.host:
        args.host = action
    args.action = "doctor"
    return args


def normalize_bdf(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if re.fullmatch(r"[0-9A-Fa-f]{8}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]", text):
        return f"{text[4:8]}:{text[9:]}".lower()
    if re.fullmatch(r"[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}\.[0-7]", text):
        return text.lower()
    raise SystemExit(f"invalid BDF: {raw}")


@dataclass
class CommandResult:
    success: bool
    stdout: str
    stderr: str
    command: str


class TargetExecutor:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self._ssh_channel = None

    def is_remote(self) -> bool:
        return bool(self.args.host)

    async def run(self, command: str, *, use_sudo: bool = False) -> CommandResult:
        if self.is_remote():
            return await self._run_remote(command, use_sudo=use_sudo)
        return self._run_local(command, use_sudo=use_sudo)

    def _run_local(self, command: str, *, use_sudo: bool) -> CommandResult:
        actual = f"sudo {command}" if use_sudo else command
        proc = subprocess.run(
            actual,
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        return CommandResult(
            success=proc.returncode == 0,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=actual,
        )

    async def _run_remote(self, command: str, *, use_sudo: bool) -> CommandResult:
        from lib.channels.ssh import SSHChannel

        if self._ssh_channel is None:
            inventory = {
                self.args.node or self.args.host: SimpleNamespace(
                    name=self.args.node or self.args.host,
                    ssh=SimpleNamespace(
                        host=self.args.host,
                        port=int(self.args.ssh_port),
                        user=self.args.ssh_user or "root",
                        password=self.args.ssh_password or None,
                        key_file=None,
                        timeout=int(self.args.ssh_timeout),
                        use_sudo=True,
                    ),
                )
            }
            self._ssh_channel = SSHChannel(
                inventory=inventory,
                dry_run=False,
                command_timeout=int(self.args.ssh_timeout),
            )
        result = await self._ssh_channel.run_command(
            self.args.node or self.args.host,
            command,
            use_sudo=use_sudo,
        )
        return CommandResult(
            success=bool(result.success),
            stdout=str(result.output or ""),
            stderr=str(result.error or ""),
            command=command,
        )

    async def close(self) -> None:
        if self._ssh_channel is not None:
            await self._ssh_channel.close()


def parse_gpu_table(stdout: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        rows.append(
            {
                "index": parts[0],
                "bdf": normalize_bdf(parts[1]),
                "name": parts[2],
                "uuid": parts[3],
            }
        )
    return rows


def parse_bdf_lines(stdout: str) -> list[str]:
    items: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        token = line.split()[0]
        try:
            items.append(normalize_bdf(token))
        except SystemExit:
            continue
    return sorted(set(items))


async def collect_inventory(executor: TargetExecutor) -> dict[str, Any]:
    smi = await executor.run(
        "nvidia-smi --query-gpu=index,pci.bus_id,name,uuid --format=csv,noheader",
        use_sudo=False,
    )
    pcie = await executor.run(
        "bash -lc \"lspci -D | grep -Ei 'VGA compatible controller: NVIDIA' | awk '{print $1}'\"",
        use_sudo=False,
    )
    sysfs = await executor.run(
        "bash -lc \"ls /sys/bus/pci/devices 2>/dev/null | grep -Ei '^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\\.[0-7]$' | sort\"",
        use_sudo=False,
    )
    dmesg = await executor.run(
        r"bash -lc ""dmesg | grep -Ei 'Xid|fallen off the bus|NVRM|pci.*error' | tail -n 80 || true""",
        use_sudo=True,
    )
    gpu_rows = parse_gpu_table(smi.stdout)
    pcie_bdfs = parse_bdf_lines(pcie.stdout)
    sysfs_bdfs = [item for item in parse_bdf_lines(sysfs.stdout) if item in pcie_bdfs]
    return {
        "nvidia_smi": {
            "success": smi.success,
            "rows": gpu_rows,
            "stderr": smi.stderr,
        },
        "pcie": {
            "success": pcie.success,
            "bdfs": pcie_bdfs,
            "stderr": pcie.stderr,
        },
        "sysfs": {
            "success": sysfs.success,
            "bdfs": sysfs_bdfs,
            "stderr": sysfs.stderr,
        },
        "dmesg_tail": dmesg.stdout,
    }


def determine_expected_count(args: argparse.Namespace, inventory: dict[str, Any]) -> int:
    if int(args.expected_gpu_count or 0) > 0:
        return int(args.expected_gpu_count)
    return max(
        len(inventory["nvidia_smi"]["rows"]),
        len(inventory["pcie"]["bdfs"]),
        0,
    )


def classify_state(inventory: dict[str, Any], expected_count: int) -> dict[str, Any]:
    smi_rows = inventory["nvidia_smi"]["rows"]
    smi_bdfs = {row["bdf"] for row in smi_rows}
    pcie_bdfs = set(inventory["pcie"]["bdfs"])
    sysfs_bdfs = set(inventory["sysfs"]["bdfs"])
    combined_pcie = sorted(pcie_bdfs | sysfs_bdfs)
    missing_from_nvidia = sorted((pcie_bdfs | sysfs_bdfs) - smi_bdfs)
    missing_from_pcie = max(expected_count - len(combined_pcie), 0)
    missing_from_nvidia_count = max(expected_count - len(smi_rows), 0)

    if expected_count == 0 and not smi_rows and not combined_pcie:
        state = "unknown"
        summary = "No GPU inventory could be collected."
    elif missing_from_nvidia:
        state = "pcie_present_nvidia_missing"
        summary = (
            "PCIe still reports GPU device(s), but nvidia-smi inventory is missing "
            f"{', '.join(missing_from_nvidia)}."
        )
    elif len(smi_rows) < expected_count and len(combined_pcie) < expected_count:
        state = "pcie_missing"
        summary = (
            f"Expected {expected_count} GPU(s) but only {len(combined_pcie)} PCIe device(s) "
            f"and {len(smi_rows)} nvidia-smi GPU(s) are visible."
        )
    elif len(smi_rows) < expected_count:
        state = "nvidia_missing_unknown"
        summary = (
            f"Expected {expected_count} GPU(s) but only {len(smi_rows)} are visible in nvidia-smi."
        )
    else:
        state = "healthy"
        summary = f"Expected {expected_count} GPU(s) are visible and inventory is consistent."

    return {
        "state": state,
        "summary": summary,
        "expected_gpu_count": expected_count,
        "nvidia_gpu_count": len(smi_rows),
        "pcie_gpu_count": len(combined_pcie),
        "missing_from_nvidia_bdfs": missing_from_nvidia,
        "missing_from_nvidia_count": missing_from_nvidia_count,
        "missing_from_pcie_count": missing_from_pcie,
    }


async def pause_gpu_clients(executor: TargetExecutor) -> list[dict[str, Any]]:
    commands = [
        "pkill -f 'watch -n 1 -t nvidia-smi' 2>/dev/null || true",
        "pkill -f dcgm-exporter 2>/dev/null || true",
        "pkill -f nvidia-device-plugin 2>/dev/null || true",
        "bash -lc 'for id in $(crictl ps 2>/dev/null | awk \"/dcgm-exporter|nvidia-device-plugin/ {print \\$1}\"); do crictl stop \"$id\" 2>/dev/null || true; done'",
    ]
    steps: list[dict[str, Any]] = []
    for command in commands:
        result = await executor.run(command, use_sudo=True)
        steps.append(
            {
                "command": command,
                "success": result.success,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
            }
        )
    return steps


async def issue_rescan(executor: TargetExecutor) -> dict[str, Any]:
    result = await executor.run("bash -lc \"printf '1\\n' > /sys/bus/pci/rescan\"", use_sudo=True)
    return {
        "command": result.command,
        "success": result.success,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


async def issue_gpu_reset(executor: TargetExecutor) -> dict[str, Any]:
    result = await executor.run("nvidia-smi --gpu-reset", use_sudo=True)
    return {
        "command": result.command,
        "success": result.success,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def resolve_bdf_from_index(gpu_index: str, inventory: dict[str, Any]) -> str:
    index = gpu_index.strip()
    for row in inventory["nvidia_smi"]["rows"]:
        if row["index"] == index:
            return row["bdf"]
    raise SystemExit(f"could not resolve BDF for GPU index {gpu_index}")


async def do_drop(executor: TargetExecutor, args: argparse.Namespace) -> dict[str, Any]:
    inventory = await collect_inventory(executor)
    bdf = normalize_bdf(args.bdf) if args.bdf else resolve_bdf_from_index(args.gpu_index, inventory)
    result = await executor.run(
        f"bash -lc \"printf '1\\n' > /sys/bus/pci/devices/{bdf}/remove\"",
        use_sudo=True,
    )
    post = None if args.skip_verify else await collect_inventory(executor)
    return {
        "action": "drop",
        "target_bdf": bdf,
        "command_success": result.success,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "post_inventory": post,
    }


async def do_recover(executor: TargetExecutor, args: argparse.Namespace) -> dict[str, Any]:
    before = await collect_inventory(executor)
    rescan = await issue_rescan(executor)
    if not args.skip_verify:
        await asyncio.sleep(max(int(args.wait_seconds), 1))
    after = None if args.skip_verify else await collect_inventory(executor)
    return {
        "action": "recover",
        "before": before,
        "rescan": rescan,
        "after": after,
    }


async def do_doctor(executor: TargetExecutor, args: argparse.Namespace) -> dict[str, Any]:
    before = await collect_inventory(executor)
    expected_count = determine_expected_count(args, before)
    diagnosis = classify_state(before, expected_count)
    actions: list[dict[str, Any]] = []
    after = before

    if args.auto_recover and diagnosis["state"] != "healthy":
        actions.append({"step": "rescan", **(await issue_rescan(executor))})
        await asyncio.sleep(max(int(args.wait_seconds), 1))
        after = await collect_inventory(executor)
        diagnosis_after_rescan = classify_state(after, expected_count)
        if diagnosis_after_rescan["state"] != "healthy":
            if args.pause_gpu_clients:
                actions.append({"step": "pause_gpu_clients", "details": await pause_gpu_clients(executor)})
                await asyncio.sleep(2)
            actions.append({"step": "gpu_reset", **(await issue_gpu_reset(executor))})
            await asyncio.sleep(max(int(args.wait_seconds), 1))
            after = await collect_inventory(executor)

    final = classify_state(after, expected_count)
    recovered = final["state"] == "healthy" and diagnosis["state"] != "healthy"
    remediation = {
        "needed": final["state"] != "healthy",
        "recovered": recovered,
        "next_step": (
            "none"
            if final["state"] == "healthy"
            else "driver_reload_or_reboot"
        ),
    }
    report = {
        "action": "doctor",
        "node": args.node or args.host or "local",
        "target_host": args.host or "local",
        "expected_gpu_count": expected_count,
        "initial_diagnosis": diagnosis,
        "final_diagnosis": final,
        "before": before,
        "after": after,
        "actions": actions,
        "remediation": remediation,
        "summary": final["summary"] if final["state"] != "healthy" else (
            "GPU inventory is now healthy after diagnosis/recovery."
            if recovered
            else "GPU inventory is healthy."
        ),
    }
    return report


def render_list(inventory: dict[str, Any]) -> str:
    rows = inventory["nvidia_smi"]["rows"]
    if not rows:
        return "No GPUs visible in nvidia-smi.\n"
    lines = ["GPU inventory:"]
    for row in rows:
        lines.append(f"{row['index']}, {row['bdf']}, {row['name']}, {row['uuid']}")
    return "\n".join(lines) + "\n"


async def main() -> int:
    args = normalize_action_and_target(build_parser().parse_args(preprocess_env_args(sys.argv[1:])))
    executor = TargetExecutor(args)
    try:
        if args.action in {"list", "status"}:
            inventory = await collect_inventory(executor)
            sys.stdout.write(render_list(inventory))
            return 0
        if args.action == "drop":
            if not args.gpu_index and not args.bdf:
                raise SystemExit("drop requires --gpu-index or --bdf")
            report = await do_drop(executor, args)
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return 0 if report["command_success"] else 1
        if args.action == "recover":
            report = await do_recover(executor, args)
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return 0
        report = await do_doctor(executor, args)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 0
    finally:
        await executor.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
PY
