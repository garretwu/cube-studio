#!/usr/bin/env bash
# rdma_switch_restore.sh -- 按基线或显式期望恢复交换机接口上的 RDMA QoS 状态

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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def env_text(name: str, default: str = "") -> str:
    return str(os.getenv(name, default)).strip()


@dataclass
class CommandResult:
    success: bool
    output: str
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
        proc = subprocess.run(
            self._ssh_command(),
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
        elif line == "qos trust dscp":
            trust_state = "enabled"
        elif line == "undo qos trust dscp":
            trust_state = "disabled"
        elif line.startswith("qos wred queue "):
            wred_lines.append(line)

    return {
        "interface": interface,
        "policies": sorted(policies, key=lambda item: (item["direction"], item["policy"])),
        "trust_state": trust_state,
        "wred_lines": sorted(wred_lines),
    }


def wred_index(lines: list[str]) -> dict[tuple[str, str], str]:
    indexed: dict[tuple[str, str], str] = {}
    for line in lines:
        match = re.match(r"qos wred queue (\d+) drop-level (\d+) ", line)
        if not match:
            continue
        indexed[(match.group(1), match.group(2))] = line
    return indexed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore switch QoS state for an RDMA interface.")
    parser.add_argument("--switch-host", default=env_text("SRE_RDMA_SWITCH_HOST"), help="Switch management host")
    parser.add_argument("--switch-user", default=env_text("SRE_RDMA_SWITCH_USER"), help="Switch SSH user")
    parser.add_argument("--switch-password", default=env_text("SRE_RDMA_SWITCH_PASSWORD"), help="Switch SSH password")
    parser.add_argument("--switch-port", type=int, default=int(env_text("SRE_RDMA_SWITCH_PORT", "22") or "22"))
    parser.add_argument("--interface", default=env_text("SRE_RDMA_SWITCH_INTERFACE"), help="Switch interface name")
    parser.add_argument("--baseline-report", default="", help="Healthy discovery report JSON")
    parser.add_argument(
        "--expected-trust",
        choices=("enabled", "disabled", "unknown", "unchanged"),
        default="unchanged",
        help="Expected trust state when not using baseline or when overriding baseline",
    )
    parser.add_argument("--expected-policy", action="append", default=[], help="Desired policy binding, format policy:inbound|outbound")
    parser.add_argument("--wred-line", action="append", default=[], help="Desired WRED config line")
    parser.add_argument("--remove-extra-policies", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action="store_true", help="Apply the generated configuration")
    parser.add_argument("--timeout", type=int, default=30)
    return parser


def load_baseline(path: str) -> dict[str, Any] | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    state = data.get("interface_state")
    if not isinstance(state, dict):
        raise RuntimeError("baseline report does not contain interface_state")
    return state


def parse_expected_policies(items: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if ":" not in text:
            raise RuntimeError(f"invalid --expected-policy value: {text}")
        policy, direction = text.rsplit(":", 1)
        direction = direction.strip()
        if direction not in {"inbound", "outbound"}:
            raise RuntimeError(f"invalid policy direction: {text}")
        parsed.append({"policy": policy.strip(), "direction": direction})
    return sorted(parsed, key=lambda item: (item["direction"], item["policy"]))


def build_desired_state(args: argparse.Namespace) -> dict[str, Any]:
    baseline = load_baseline(args.baseline_report) if args.baseline_report else None
    desired = {
        "policies": baseline.get("policies", []) if baseline else [],
        "trust_state": baseline.get("trust_state", "unknown") if baseline else "unknown",
        "wred_lines": baseline.get("wred_lines", []) if baseline else [],
    }
    explicit_policies = parse_expected_policies(args.expected_policy)
    if explicit_policies:
        desired["policies"] = explicit_policies
    if args.expected_trust != "unchanged":
        desired["trust_state"] = args.expected_trust
    if args.wred_line:
        desired["wred_lines"] = sorted(str(item).strip() for item in args.wred_line if str(item).strip())
    return desired


def plan_commands(interface: str, current: dict[str, Any], desired: dict[str, Any], remove_extra_policies: bool) -> list[str]:
    commands = ["system-view", f"interface {interface}"]

    current_policies = {(item["policy"], item["direction"]) for item in current.get("policies", [])}
    desired_policies = {(item["policy"], item["direction"]) for item in desired.get("policies", [])}

    if remove_extra_policies:
        for policy, direction in sorted(current_policies - desired_policies, key=lambda item: (item[1], item[0])):
            commands.append(f"undo qos apply policy {policy} {direction}")
    for policy, direction in sorted(desired_policies - current_policies, key=lambda item: (item[1], item[0])):
        commands.append(f"qos apply policy {policy} {direction}")

    trust_state = desired.get("trust_state", "unknown")
    if trust_state == "enabled" and current.get("trust_state") != "enabled":
        commands.append("qos trust dscp")
    elif trust_state == "disabled" and current.get("trust_state") != "disabled":
        commands.append("undo qos trust dscp")

    current_wred = wred_index(current.get("wred_lines", []))
    desired_wred = wred_index(desired.get("wred_lines", []))
    for key in sorted(current_wred):
        if key not in desired_wred or current_wred[key] != desired_wred[key]:
            queue, level = key
            commands.append(f"undo qos wred queue {queue} drop-level {level}")
    for key in sorted(desired_wred):
        if key not in current_wred or current_wred[key] != desired_wred[key]:
            commands.append(desired_wred[key])

    commands.extend(["quit", "quit"])
    return commands


def comparable_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "policies": sorted(state.get("policies", []), key=lambda item: (item["direction"], item["policy"])),
        "trust_state": state.get("trust_state", "unknown"),
        "wred_lines": sorted(state.get("wred_lines", [])),
    }


def main() -> int:
    args = build_parser().parse_args()
    if not args.switch_host or not args.interface:
        raise SystemExit("--switch-host and --interface are required")
    if not args.baseline_report and args.expected_trust == "unchanged" and not args.expected_policy and not args.wred_line:
        raise SystemExit("provide --baseline-report or explicit expected state")

    executor = SwitchCLIExecutor(
        host=args.switch_host,
        user=args.switch_user,
        password=args.switch_password,
        port=args.switch_port,
        timeout=args.timeout,
    )

    current_result = executor.run_cli([f"display current-configuration interface {args.interface}"])
    if not current_result.success:
        payload = {
            "summary": {
                "status": "failed",
                "switch_host": args.switch_host,
                "interface": args.interface,
            },
            "error": current_result.output or current_result.stderr or "failed to read interface config",
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1

    current = parse_interface_state(args.interface, current_result.output)
    desired = build_desired_state(args)
    commands = plan_commands(args.interface, current, desired, args.remove_extra_policies)

    payload: dict[str, Any] = {
        "summary": {
            "status": "planned",
            "switch_host": args.switch_host,
            "interface": args.interface,
            "mode": "execute" if args.execute else "plan",
        },
        "current_state": current,
        "desired_state": desired,
        "commands": commands,
    }

    if not args.execute:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    apply_result = executor.run_cli(commands)
    verify_result = executor.run_cli([f"display current-configuration interface {args.interface}"])
    verified_state = parse_interface_state(args.interface, verify_result.output) if verify_result.success else {}
    payload["apply_output"] = apply_result.output
    payload["verified_state"] = verified_state
    payload["summary"]["status"] = "success" if comparable_state(verified_state) == comparable_state(desired) else "needs_manual_check"
    if not apply_result.success:
        payload["summary"]["status"] = "failed"
        payload["error"] = apply_result.output or apply_result.stderr or "switch apply failed"
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["summary"]["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
