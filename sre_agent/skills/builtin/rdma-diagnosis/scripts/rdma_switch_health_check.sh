#!/usr/bin/env bash
# rdma_switch_health_check.sh -- 发现交换机接口上的 RDMA 相关 QoS 漂移

set -euo pipefail

resolve_python_bin() {
  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  printf 'No usable Python interpreter found. Set PYTHON_BIN explicitly.\n' >&2
  return 1
}

PYTHON_BIN="$(resolve_python_bin)"

exec "$PYTHON_BIN" - "$@" <<'PY'
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def env_text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


@dataclass
class CommandResult:
    success: bool
    output: str
    command: list[str]
    returncode: int
    stderr: str


class SwitchCLIExecutor:
    def __init__(self, host: str, user: str, password: str, port: int, timeout: int) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.timeout = timeout

    def _ssh_command(self) -> list[str]:
        target = f"{self.user}@{self.host}" if self.user else self.host
        ssh = [
            "ssh",
            "-tt",
            "-p",
            str(self.port),
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            target,
        ]
        if self.password:
            if shutil.which("sshpass") is None:
                raise RuntimeError("sshpass is required when switch password is provided")
            return ["sshpass", "-p", self.password, *ssh]
        return ssh

    @staticmethod
    def _sanitize(output: str) -> str:
        lines: list[str] = []
        for raw in output.splitlines():
            line = raw.rstrip("\r")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("Warning:", "Pseudo-terminal", "Connection to ")):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def run_cli(self, commands: list[str]) -> CommandResult:
        payload = "screen-length disable\n" + "\n".join(commands) + "\nquit\n"
        cmd = self._ssh_command()
        proc = subprocess.run(
            cmd,
            input=payload,
            text=True,
            capture_output=True,
            timeout=self.timeout,
            check=False,
        )
        combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
        return CommandResult(
            success=proc.returncode == 0,
            output=self._sanitize(combined),
            command=cmd,
            returncode=proc.returncode,
            stderr=proc.stderr or "",
        )


def parse_interface_state(interface: str, output: str) -> dict[str, Any]:
    lines: list[str] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line or line.startswith("<") or line == "return" or line == "#":
            continue
        if line.startswith("interface ") and line != f"interface {interface}":
            continue
        lines.append(line)

    policies: list[dict[str, str]] = []
    trust_state = "unknown"
    wred_lines: list[str] = []
    for line in lines:
        policy_match = re.match(r"qos apply policy (\S+) (inbound|outbound)$", line)
        if policy_match:
            policies.append({"policy": policy_match.group(1), "direction": policy_match.group(2)})
            continue
        if line == "qos trust dscp":
            trust_state = "enabled"
            continue
        if line == "undo qos trust dscp":
            trust_state = "disabled"
            continue
        if line.startswith("qos wred queue "):
            wred_lines.append(line)

    wred_by_queue: dict[str, list[dict[str, Any]]] = {}
    for line in wred_lines:
        match = re.match(
            r"qos wred queue (\d+) drop-level (\d+) low-limit (\d+) high-limit (\d+) discard-probability (\d+)",
            line,
        )
        if not match:
            continue
        queue = match.group(1)
        wred_by_queue.setdefault(queue, []).append(
            {
                "line": line,
                "drop_level": int(match.group(2)),
                "low_limit": int(match.group(3)),
                "high_limit": int(match.group(4)),
                "discard_probability": int(match.group(5)),
            }
        )

    return {
        "interface": interface,
        "policies": sorted(policies, key=lambda item: (item["direction"], item["policy"])),
        "trust_state": trust_state,
        "wred_lines": sorted(wred_lines),
        "wred_by_queue": wred_by_queue,
        "config_lines": lines,
    }


def compare_to_baseline(current: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    current_policies = {(item["policy"], item["direction"]) for item in current.get("policies", [])}
    baseline_policies = {(item["policy"], item["direction"]) for item in baseline.get("policies", [])}
    if current_policies != baseline_policies:
        findings.append(
            {
                "code": "switch_policy_drift",
                "severity": "warn",
                "message": f"policy bindings drifted: current={sorted(current_policies)} baseline={sorted(baseline_policies)}",
            }
        )
    if current.get("trust_state") != baseline.get("trust_state"):
        findings.append(
            {
                "code": "switch_trust_drift",
                "severity": "warn",
                "message": f"trust state drifted: current={current.get('trust_state')} baseline={baseline.get('trust_state')}",
            }
        )
    if sorted(current.get("wred_lines", [])) != sorted(baseline.get("wred_lines", [])):
        findings.append(
            {
                "code": "switch_wred_drift",
                "severity": "warn",
                "message": "WRED queue lines drifted from baseline",
            }
        )
    return findings


def heuristic_findings(
    current: dict[str, Any],
    *,
    expected_trust: str,
    max_discard_probability: int | None,
    min_high_limit: int | None,
    car_output: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if expected_trust != "unchanged" and current.get("trust_state") != expected_trust:
        findings.append(
            {
                "code": "switch_trust_unexpected",
                "severity": "warn",
                "message": f"trust state is {current.get('trust_state')}, expected {expected_trust}",
            }
        )

    if current.get("policies"):
        findings.append(
            {
                "code": "switch_policy_bound",
                "severity": "info",
                "message": f"interface has QoS policy bindings: {current.get('policies')}",
            }
        )

    for queue, entries in current.get("wred_by_queue", {}).items():
        for entry in entries:
            if max_discard_probability is not None and entry["discard_probability"] > max_discard_probability:
                findings.append(
                    {
                        "code": "switch_wred_discard_high",
                        "severity": "warn",
                        "message": (
                            f"queue {queue} drop-level {entry['drop_level']} discard-probability="
                            f"{entry['discard_probability']} > {max_discard_probability}"
                        ),
                    }
                )
            if min_high_limit is not None and entry["high_limit"] < min_high_limit:
                findings.append(
                    {
                        "code": "switch_wred_high_limit_low",
                        "severity": "warn",
                        "message": (
                            f"queue {queue} drop-level {entry['drop_level']} high-limit="
                            f"{entry['high_limit']} < {min_high_limit}"
                        ),
                    }
                )

    if re.search(r"\bcar cir\b", car_output, re.IGNORECASE):
        findings.append(
            {
                "code": "switch_car_hint",
                "severity": "info",
                "message": "global configuration contains CAR rate-limit lines; verify whether attached policies reference them",
            }
        )
    return findings


def load_baseline(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    baseline = data.get("interface_state")
    if not isinstance(baseline, dict):
        raise RuntimeError("baseline report does not contain interface_state")
    return baseline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover switch-side QoS drift for RDMA interfaces.")
    parser.add_argument("--switch-host", default=env_text("SRE_RDMA_SWITCH_HOST"), help="Switch management host")
    parser.add_argument("--switch-user", default=env_text("SRE_RDMA_SWITCH_USER"), help="Switch SSH user")
    parser.add_argument("--switch-password", default=env_text("SRE_RDMA_SWITCH_PASSWORD"), help="Switch SSH password")
    parser.add_argument("--switch-port", type=int, default=int(env_text("SRE_RDMA_SWITCH_PORT", "22") or "22"))
    parser.add_argument("--interface", default=env_text("SRE_RDMA_SWITCH_INTERFACE"), help="Switch interface name")
    parser.add_argument("--baseline-report", default="", help="Optional healthy baseline report JSON")
    parser.add_argument(
        "--expected-trust",
        choices=("enabled", "disabled", "unknown", "unchanged"),
        default="enabled",
        help="Expected qos trust state when no baseline is provided",
    )
    parser.add_argument("--max-discard-probability", type=int, default=None, help="Warn when discard-probability is above this threshold")
    parser.add_argument("--min-high-limit", type=int, default=None, help="Warn when WRED high-limit is below this threshold")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--output-dir", default="", help="Optional output directory")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.switch_host or not args.interface:
        raise SystemExit("--switch-host and --interface are required")

    output_dir = Path(args.output_dir) if args.output_dir else Path(
        f"/tmp/rdma_switch_health_{args.interface.replace('/', '_')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "rdma_switch_health_report.json"
    raw_path = output_dir / "raw_switch_outputs.json"

    executor = SwitchCLIExecutor(
        host=args.switch_host,
        user=args.switch_user,
        password=args.switch_password,
        port=args.switch_port,
        timeout=args.timeout,
    )

    interface_cmd = f"display current-configuration interface {args.interface}"
    interface_result = executor.run_cli([interface_cmd])
    global_car_result = executor.run_cli(["display current-configuration | include car cir"])
    global_policy_result = executor.run_cli(["display current-configuration | include qos policy"])
    global_behavior_result = executor.run_cli(["display current-configuration | include traffic behavior"])

    if not interface_result.success:
        payload = {
            "summary": {
                "status": "failed",
                "interface": args.interface,
                "switch_host": args.switch_host,
            },
            "error": interface_result.output or interface_result.stderr or "switch command failed",
        }
        report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1

    current = parse_interface_state(args.interface, interface_result.output)
    baseline = load_baseline(args.baseline_report) if args.baseline_report else None

    findings: list[dict[str, str]] = []
    if baseline is not None:
        findings.extend(compare_to_baseline(current, baseline))
    findings.extend(
        heuristic_findings(
            current,
            expected_trust=args.expected_trust,
            max_discard_probability=args.max_discard_probability,
            min_high_limit=args.min_high_limit,
            car_output=global_car_result.output,
        )
    )

    recommended_actions: list[str] = []
    if any(item["code"] == "switch_policy_drift" for item in findings):
        recommended_actions.append("使用 rdma_switch_restore.sh 按健康基线恢复接口 policy 绑定")
    if any(item["code"] in {"switch_trust_drift", "switch_trust_unexpected"} for item in findings):
        recommended_actions.append("确认该接口是否应该启用 qos trust dscp，再执行恢复")
    if any(item["code"].startswith("switch_wred") for item in findings):
        recommended_actions.append("按健康基线恢复 WRED drop-level / discard-probability 配置")
    if any(item["code"] == "switch_car_hint" for item in findings):
        recommended_actions.append("检查当前接口绑定的 policy 是否引用了带 CAR 的 traffic behavior")
    if not recommended_actions:
        recommended_actions.append("当前接口未发现明确的 QoS 漂移，可结合主机侧与交换机计数继续排查")

    payload = {
        "summary": {
            "status": "needs_attention" if findings else "healthy",
            "switch_host": args.switch_host,
            "interface": args.interface,
        },
        "interface_state": current,
        "findings": findings,
        "recommended_actions": recommended_actions,
        "artifacts": {
            "report_path": str(report_path),
            "raw_outputs_path": str(raw_path),
        },
        "raw_outputs": {
            "interface_config": interface_result.output,
            "global_car": global_car_result.output,
            "global_policy": global_policy_result.output,
            "global_behavior": global_behavior_result.output,
        },
    }
    raw_path.write_text(json.dumps(payload["raw_outputs"], indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
