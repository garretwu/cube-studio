#!/usr/bin/env python3
"""Probe correlation between a specific BMC fan control target and GPU telemetry."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fault_injector.config.schema import SSHConfig, TargetNodeConfig
from lib.channels.redfish import RedfishChannel
from lib.channels.ssh import SSHChannel

DEFAULT_OUTPUT = Path("data/demo/gpu-fan-correlation-probe.json")
DEFAULT_NVIDIA_SMI_QUERY = (
    "nvidia-smi --query-gpu="
    "index,temperature.gpu,utilization.gpu,clocks.current.sm,clocks.max.sm,fan.speed,pstate "
    "--format=csv,noheader"
)


@dataclass
class ProbeSample:
    timestamp: str
    phase: str
    gpu_metrics: list[dict[str, Any]]
    web_fan_status: dict[str, Any]
    thermal_fans: dict[str, Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Short fan-to-GPU correlation probe with automatic restore.",
    )
    parser.add_argument("--node-name", default="worker-04", help="Inventory node name used for SSH.")
    parser.add_argument("--ssh-host", default="10.11.4.13", help="SSH host for GPU telemetry.")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--ssh-user", default=os.getenv("GPU_THERMAL_DEMO_SSH_USER", "yuyonghao"))
    parser.add_argument("--ssh-password", default=os.getenv("GPU_THERMAL_DEMO_SSH_PASSWORD", "Yuyonghao@123"))
    parser.add_argument("--ssh-timeout", type=int, default=30)
    parser.add_argument("--bmc-host", default="10.11.8.13")
    parser.add_argument("--bmc-username", default=os.getenv("SRE_REDFISH_USERNAME", "admin"))
    parser.add_argument("--bmc-password", default=os.getenv("SRE_REDFISH_PASSWORD", "Admin@9000"))
    parser.add_argument(
        "--bmc-verify-tls",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Toggle BMC TLS verification.",
    )
    parser.add_argument("--fan-bp-index", type=int, default=0)
    parser.add_argument("--fan-index", type=int, default=0)
    parser.add_argument("--manual-pwm", type=int, default=61)
    parser.add_argument("--hold-seconds", type=float, default=20.0)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    parser.add_argument("--recovery-seconds", type=float, default=10.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--skip-restore", action="store_true")
    return parser


def _build_ssh_inventory(args: argparse.Namespace) -> dict[str, TargetNodeConfig]:
    return {
        args.node_name: TargetNodeConfig(
            name=args.node_name,
            ssh=SSHConfig(
                host=args.ssh_host,
                port=args.ssh_port,
                user=args.ssh_user,
                password=args.ssh_password,
                timeout=args.ssh_timeout,
                use_sudo=True,
            ),
        )
    }


def _parse_gpu_metrics(raw: str) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text:
            continue
        parts = [segment.strip() for segment in text.split(",")]
        if len(parts) < 6:
            continue
        fan_speed = parts[5].replace("%", "").strip()
        metrics.append(
            {
                "gpu_index": int(parts[0]),
                "temperature_c": int(parts[1]),
                "utilization_pct": int(parts[2].replace("%", "").strip()),
                "sm_clock_mhz": int(parts[3].replace("MHz", "").strip()),
                "sm_clock_max_mhz": int(parts[4].replace("MHz", "").strip()),
                "fan_speed_pct": int(fan_speed) if fan_speed and fan_speed.lower() != "[not supported]" else None,
                "pstate": parts[6] if len(parts) > 6 else "",
            }
        )
    return metrics


def _extract_web_fan_status(raw: str) -> dict[str, Any]:
    payload = json.loads(raw) if raw else {}
    status: dict[str, Any] = {
        "fan_mode": payload.get("fanMode"),
        "backplanes": {},
    }
    for bp in payload.get("allFanInfo", []) or []:
        bp_index = bp.get("fanBpIndex")
        entries: dict[str, Any] = {}
        for fan in bp.get("fanInfo", []) or []:
            entries[f"fan_{fan.get('fanIndex')}"] = {
                "fanPWM": fan.get("fanPWM"),
                "fanPresentFlag": fan.get("fanPresentFlag"),
            }
        status["backplanes"][f"bp_{bp_index}"] = entries
    return status


def _extract_thermal_fans(raw: str) -> dict[str, Any]:
    payload = json.loads(raw) if raw else {}
    fans: dict[str, Any] = {}
    for fan in payload.get("Fans", []) or []:
        name = str(fan.get("Name") or "").strip()
        if not name:
            continue
        fans[name] = {
            "rpm": fan.get("Reading"),
            "state": ((fan.get("Status") or {}).get("State")),
            "health": ((fan.get("Status") or {}).get("Health")),
        }
    return fans


async def _collect_sample(
    ssh_channel: SSHChannel,
    redfish_channel: RedfishChannel,
    args: argparse.Namespace,
    phase: str,
) -> ProbeSample:
    gpu_result = await ssh_channel.run_command(
        args.node_name,
        DEFAULT_NVIDIA_SMI_QUERY,
        use_sudo=False,
    )
    web_result = await redfish_channel.get_web_fan_status(args.bmc_host, verify_tls=args.bmc_verify_tls)
    thermal_result = await redfish_channel.get_thermal(args.bmc_host, verify_tls=args.bmc_verify_tls)

    return ProbeSample(
        timestamp=datetime.now(UTC).isoformat(),
        phase=phase,
        gpu_metrics=_parse_gpu_metrics(gpu_result.output if gpu_result.success else ""),
        web_fan_status=_extract_web_fan_status(web_result.output if web_result.success else ""),
        thermal_fans=_extract_thermal_fans(thermal_result.output if thermal_result.success else ""),
    )


def _summarize_correlation(samples: list[ProbeSample], baseline: ProbeSample) -> dict[str, Any]:
    if not samples:
        return {}
    base_gpu = {item["gpu_index"]: item for item in baseline.gpu_metrics}
    base_fans = baseline.thermal_fans

    peak_gpu: dict[int, dict[str, Any]] = {}
    for sample in samples:
        for gpu in sample.gpu_metrics:
            idx = gpu["gpu_index"]
            current = peak_gpu.get(idx)
            if current is None or gpu["temperature_c"] > current["temperature_c"]:
                peak_gpu[idx] = gpu

    max_fan_delta_name = None
    max_fan_delta = None
    last_sample = samples[-1]
    for name, current in last_sample.thermal_fans.items():
        before = ((base_fans.get(name) or {}).get("rpm"))
        after = current.get("rpm")
        if before is None or after is None:
            continue
        delta = after - before
        if max_fan_delta is None or abs(delta) > abs(max_fan_delta):
            max_fan_delta = delta
            max_fan_delta_name = name

    hottest_gpu = None
    hottest_gpu_delta = None
    for idx, peak in peak_gpu.items():
        before = (base_gpu.get(idx) or {}).get("temperature_c")
        after = peak.get("temperature_c")
        if before is None or after is None:
            continue
        delta = after - before
        if hottest_gpu_delta is None or delta > hottest_gpu_delta:
            hottest_gpu_delta = delta
            hottest_gpu = idx

    return {
        "max_changed_thermal_fan": {
            "name": max_fan_delta_name,
            "rpm_delta": max_fan_delta,
        },
        "hottest_gpu_during_probe": {
            "gpu_index": hottest_gpu,
            "temperature_delta_c": hottest_gpu_delta,
        },
    }


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    inventory = _build_ssh_inventory(args)
    ssh_channel = SSHChannel(inventory=inventory, command_timeout=args.ssh_timeout, connect_timeout=10)
    redfish_channel = RedfishChannel(timeout=30)
    redfish_channel.set_web_credentials(args.bmc_host, args.bmc_username, args.bmc_password)

    samples: list[ProbeSample] = []
    baseline_sample: ProbeSample | None = None
    restore_result: dict[str, Any] | None = None
    manual_result: dict[str, Any] | None = None
    auth_result: dict[str, Any] | None = None

    try:
        auth = await redfish_channel.authenticate(
            args.bmc_host,
            args.bmc_username,
            args.bmc_password,
            verify_tls=args.bmc_verify_tls,
        )
        auth_result = {"success": auth.success, "error": auth.error, "output": auth.output}
        if not auth.success:
            raise SystemExit(f"Redfish auth failed: {auth.error}")

        baseline_sample = await _collect_sample(ssh_channel, redfish_channel, args, "baseline")

        manual = await redfish_channel.set_web_fan_control(
            args.bmc_host,
            fan_index=args.fan_index,
            mode="Manual",
            pwm=args.manual_pwm,
            fan_bp_index=args.fan_bp_index,
            verify_tls=args.bmc_verify_tls,
        )
        manual_result = {"success": manual.success, "error": manual.error, "output": manual.output}
        if not manual.success:
            raise SystemExit(f"Failed to set manual fan control: {manual.error}")

        elapsed = 0.0
        while elapsed < args.hold_seconds:
            sample = await _collect_sample(ssh_channel, redfish_channel, args, "manual")
            samples.append(sample)
            await asyncio.sleep(args.interval_seconds)
            elapsed += args.interval_seconds

    finally:
        if not args.skip_restore:
            restore = await redfish_channel.set_web_fan_control(
                args.bmc_host,
                fan_index=args.fan_index,
                mode="Auto",
                fan_bp_index=args.fan_bp_index,
                verify_tls=args.bmc_verify_tls,
            )
            restore_result = {"success": restore.success, "error": restore.error, "output": restore.output}
            recovery_elapsed = 0.0
            while recovery_elapsed < args.recovery_seconds:
                sample = await _collect_sample(ssh_channel, redfish_channel, args, "recovery")
                samples.append(sample)
                await asyncio.sleep(args.interval_seconds)
                recovery_elapsed += args.interval_seconds
        await ssh_channel.close()
        await redfish_channel.close()

    if baseline_sample is None:
        raise SystemExit("Probe did not capture a baseline sample")

    result = {
        "captured_at": datetime.now(UTC).isoformat(),
        "target": {
            "node_name": args.node_name,
            "ssh_host": args.ssh_host,
            "bmc_host": args.bmc_host,
            "fan_bp_index": args.fan_bp_index,
            "fan_index": args.fan_index,
            "manual_pwm": args.manual_pwm,
        },
        "auth": auth_result,
        "manual_set": manual_result,
        "restore": restore_result,
        "baseline": asdict(baseline_sample),
        "samples": [asdict(sample) for sample in samples],
        "summary": _summarize_correlation(
            [sample for sample in samples if sample.phase == "manual"],
            baseline_sample,
        ),
    }
    return result


def main() -> None:
    args = build_parser().parse_args()
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(main_async(args))
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
