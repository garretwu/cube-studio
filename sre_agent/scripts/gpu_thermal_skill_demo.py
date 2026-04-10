#!/usr/bin/env python3
"""Run a live GPU thermal alert demo focused on Claude-style skill usage."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.alert import AlertChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.ssh import SSHChannel
from lib.tests._real_backends import PrometheusHttpBackend
from sre_agent.agent import run_diagnosis
from sre_agent.models.alert import Alert
from sre_agent.models.remediation import RemediationPlan
from sre_agent.remediation import ApprovalGate, PlanValidationError, RemediationEngine, RollbackJournal
from sre_agent.tools import SafetyLevel, ToolDefinition, ToolExecutionContext, ToolRegistry, build_default_registry

DEFAULT_OUTPUT = Path("data/demo/gpu-temp-skill-alert-demo.json")
DEFAULT_AGENT_CONFIG = Path("sre_agent/conf/config.yaml")
DEFAULT_WAL_PATH = Path("data/wal/gpu-thermal-skill-demo.jsonl")
DEFAULT_ALLOWED_TOOLS = [
    "skills.list_skills",
    "skills.load_skill",
    "skills.read_skill_ref",
    "skills.run_skill",
    "bmc.get_fan_status",
    "gpu.get_metrics",
    "gpu.get_processes",
]
DEFAULT_ALERT_NAME = "GPUTemperatureHighWjLabCpt04"
DEFAULT_GPU_BURN_COMMAND = "./gpu_burn -d -tc -m 100% 1800"
DEFAULT_GPU_BURN_WORKDIR = "/tmp/gpu-burn"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live GPU thermal alert demo that exercises Claude-style skills.",
    )
    parser.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG), help="SRE agent config path.")
    parser.add_argument("--alert-name", default=DEFAULT_ALERT_NAME, help="Live alert name to fetch.")
    parser.add_argument("--lookback", default="24h", help="Alert history lookback window.")
    parser.add_argument(
        "--node",
        default="",
        help="Optional node hint override. If omitted, derive from alert labels when possible.",
    )
    parser.add_argument(
        "--gpu",
        default="",
        help="Optional GPU index hint override. If omitted, derive from alert labels when possible.",
    )
    parser.add_argument("--model", default="", help="Override SRE_LLM_MODEL for this run.")
    parser.add_argument("--base-url", default="", help="Override SRE_OPENAI_BASE_URL for this run.")
    parser.add_argument("--ssh-user", default=os.getenv("GPU_THERMAL_DEMO_SSH_USER", ""), help="Optional SSH username override.")
    parser.add_argument("--ssh-password", default=os.getenv("GPU_THERMAL_DEMO_SSH_PASSWORD", ""), help="Optional SSH password override.")
    parser.add_argument("--ssh-port", type=int, default=int(os.getenv("GPU_THERMAL_DEMO_SSH_PORT", "22")), help="Optional SSH port override.")
    parser.add_argument("--ssh-timeout", type=int, default=int(os.getenv("GPU_THERMAL_DEMO_SSH_TIMEOUT", "30")), help="SSH command timeout in seconds.")
    parser.add_argument(
        "--live-inventory",
        default="",
        help="Optional live inventory YAML path. Defaults to ontology.discovery.live_inventory_path from config.",
    )
    parser.add_argument("--bmc-host", default="", help="Optional BMC host override.")
    parser.add_argument("--bmc-username", default=os.getenv("SRE_REDFISH_USERNAME", ""), help="Optional BMC username override.")
    parser.add_argument("--bmc-password", default=os.getenv("SRE_REDFISH_PASSWORD", ""), help="Optional BMC password override.")
    parser.add_argument(
        "--bmc-verify-tls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override BMC TLS verification.",
    )
    parser.add_argument(
        "--allowed-tools",
        nargs="*",
        default=DEFAULT_ALLOWED_TOOLS,
        help="Tools exposed to the demo.",
    )
    parser.add_argument(
        "--inject-faults",
        action="store_true",
        help="Inject low fan PWM plus 4-GPU gpu_burn before waiting for the alert.",
    )
    parser.add_argument("--inject-fan-index", type=int, default=0, help="Fan index to set during injection.")
    parser.add_argument("--inject-fan-pwm", type=int, default=80, help="Injected manual PWM percentage.")
    parser.add_argument("--inject-fan-bp-index", type=int, default=0xFF, help="Backplane fan index passed to the BMC API.")
    parser.add_argument("--gpu-burn-command", default=DEFAULT_GPU_BURN_COMMAND, help="gpu_burn command to start on the target node.")
    parser.add_argument("--gpu-burn-workdir", default=DEFAULT_GPU_BURN_WORKDIR, help="Working directory that contains gpu_burn.")
    parser.add_argument("--alert-wait-timeout", type=float, default=300.0, help="How long to wait for the target alert to enter firing state.")
    parser.add_argument("--alert-poll-interval", type=float, default=10.0, help="Polling interval while waiting for the alert.")
    parser.add_argument("--skip-cleanup", action="store_true", help="Keep injected state after the run for manual inspection.")
    parser.add_argument("--step-timeout", type=float, default=60.0)
    parser.add_argument("--total-timeout", type=float, default=300.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument(
        "--execute-remediation",
        action="store_true",
        help="Execute the generated remediation plan through RemediationEngine.",
    )
    parser.add_argument(
        "--approval-user",
        default="demo-admin",
        help="Approver identity recorded when remediation execution is enabled.",
    )
    parser.add_argument(
        "--wal-path",
        default=str(DEFAULT_WAL_PATH),
        help="WAL path used when executing remediation.",
    )
    parser.add_argument(
        "--remediation-mode",
        choices=["mock", "real"],
        default="real",
        help="Execution mode used when --execute-remediation is enabled.",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default="./data/checkpoints/gpu_thermal_skill_demo",
        help="Checkpoint directory for this run.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output file path.",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "txt"],
        default="json",
        help="Output file format.",
    )
    parser.add_argument(
        "--trace-path",
        default="",
        help="Optional trace log path. Defaults to <output>.trace.log.",
    )
    return parser


def apply_model_env(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["SRE_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["SRE_OPENAI_BASE_URL"] = args.base_url


def load_prometheus_url(config_path: str) -> str:
    raw = json.loads("{}")
    try:
        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise SystemExit(f"Config file not found: {config_path}") from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Failed to read config file {config_path}: {exc}") from exc
    global_cfg = raw.get("global") if isinstance(raw, dict) else None
    prometheus_url = str((global_cfg or {}).get("prometheus_url") or "").strip()
    if not prometheus_url:
        raise SystemExit(f"Config file {config_path} is missing global.prometheus_url")
    return prometheus_url


def _load_yaml_file(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"YAML file {path} must contain a top-level object")
    return raw


def _resolve_live_inventory_path(config_path: str, override: str) -> Path | None:
    if override.strip():
        candidate = Path(override.strip()).expanduser()
        return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()
    config_file = Path(config_path).expanduser()
    raw = _load_yaml_file(config_file)
    discovery = ((raw.get("ontology") or {}).get("discovery") or {}) if isinstance(raw, dict) else {}
    live_inventory_path = str(discovery.get("live_inventory_path") or "").strip()
    if not live_inventory_path:
        return None
    candidate = Path(live_inventory_path).expanduser()
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate).resolve()


def _build_inventory_node(name: str, ssh_cfg: dict[str, Any]) -> Any:
    return SimpleNamespace(
        name=name,
        ssh=SimpleNamespace(
            host=str(ssh_cfg.get("host") or "").strip(),
            port=int(ssh_cfg.get("port") or 22),
            user=str(ssh_cfg.get("user") or "root").strip(),
            key_file=ssh_cfg.get("key_file"),
            password=ssh_cfg.get("password"),
            timeout=int(ssh_cfg.get("timeout") or 30),
            use_sudo=bool(ssh_cfg.get("use_sudo", True)),
        ),
    )


def _flatten_live_inventory(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    raw_inventory = raw_cfg.get("inventory")
    if not isinstance(raw_inventory, dict):
        return {}
    flattened: dict[str, Any] = {}
    for group_nodes in raw_inventory.values():
        if not isinstance(group_nodes, list):
            continue
        for raw_node in group_nodes:
            if not isinstance(raw_node, dict):
                continue
            node_name = str(raw_node.get("name") or "").strip()
            ssh_cfg = raw_node.get("ssh")
            if not node_name or not isinstance(ssh_cfg, dict):
                continue
            flattened[node_name] = _build_inventory_node(node_name, ssh_cfg)
    return flattened


def _flatten_live_redfish_inventory(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    raw_inventory = raw_cfg.get("inventory")
    if not isinstance(raw_inventory, dict):
        return {}
    flattened: dict[str, Any] = {}
    for group_nodes in raw_inventory.values():
        if not isinstance(group_nodes, list):
            continue
        for raw_node in group_nodes:
            if not isinstance(raw_node, dict):
                continue
            node_name = str(raw_node.get("name") or "").strip()
            redfish_cfg = raw_node.get("redfish")
            if not node_name or not isinstance(redfish_cfg, dict):
                continue
            flattened[node_name] = {
                "bmc_host": str(redfish_cfg.get("bmc_host") or "").strip(),
                "username": str(redfish_cfg.get("username") or "").strip(),
                "password": str(redfish_cfg.get("password") or "").strip(),
                "verify_tls": redfish_cfg.get("verify_tls"),
                "timeout": int(redfish_cfg.get("timeout") or 30),
            }
    return flattened


def _extract_instance_host(alert: Alert) -> str:
    instance = str((alert.labels or {}).get("instance") or "").strip()
    if not instance:
        return ""
    return instance.split(":", 1)[0].strip()


def _worker_aliases(name: str) -> set[str]:
    raw = str(name or "").strip().lower()
    if not raw:
        return set()
    aliases = {raw}
    match = re.search(r"(\d+)$", raw)
    if match:
        suffix = int(match.group(1))
        aliases.add(f"wj-lab-cpt-{suffix:02d}")
    return aliases


def _match_inventory_name(
    inventory: dict[str, Any],
    *,
    candidate_keys: set[str],
    hostname_label: str,
) -> str:
    for inventory_name, inventory_node in inventory.items():
        ssh_obj = getattr(inventory_node, "ssh", None)
        ssh_host = str(getattr(ssh_obj, "host", "") or "").strip()
        if ssh_host and ssh_host in candidate_keys:
            return inventory_name
        inventory_aliases = _worker_aliases(inventory_name)
        if hostname_label and hostname_label in inventory_aliases:
            return inventory_name
        if str(getattr(inventory_node, "name", "") or "").strip() in candidate_keys:
            return inventory_name
    return ""


def _derive_default_node(alert_name: str) -> str:
    match = re.search(r"WjLabCpt(\d+)", str(alert_name or ""))
    if not match:
        return ""
    return f"wj-lab-cpt-{int(match.group(1)):02d}"


def build_ssh_inventory(
    *,
    args: argparse.Namespace,
    config_path: str,
    alert: Alert,
    node: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    warnings: dict[str, str] = {}
    inventory: dict[str, Any] = {}

    live_inventory_path = _resolve_live_inventory_path(config_path, args.live_inventory)
    if live_inventory_path and live_inventory_path.exists():
        try:
            inventory = _flatten_live_inventory(_load_yaml_file(live_inventory_path))
        except Exception as exc:  # noqa: BLE001
            warnings["ssh_inventory"] = f"failed to load live inventory: {exc}"

    ssh_user = str(args.ssh_user or "").strip()
    ssh_password = str(args.ssh_password or "").strip()
    instance_host = _extract_instance_host(alert)
    hostname_label = str((alert.labels or {}).get("Hostname") or "").strip().lower()
    candidate_keys = {
        node.strip(),
        instance_host.strip(),
        str((alert.labels or {}).get("Hostname") or "").strip(),
    }

    matched_name = _match_inventory_name(
        inventory,
        candidate_keys=candidate_keys,
        hostname_label=hostname_label,
    )

    if matched_name:
        matched_node = inventory[matched_name]
        ssh_obj = getattr(matched_node, "ssh", None)
        existing_password = str(getattr(ssh_obj, "password", "") or "").strip()
        existing_user = str(getattr(ssh_obj, "user", "") or "").strip()
        if ssh_user or ssh_password or existing_password == "REPLACE_ME":
            inventory[matched_name] = _build_inventory_node(
                matched_name,
                {
                    "host": str(getattr(ssh_obj, "host", "") or "").strip(),
                    "port": int(getattr(ssh_obj, "port", 22) or 22),
                    "user": ssh_user or existing_user or "root",
                    "password": ssh_password or ("" if existing_password == "REPLACE_ME" else existing_password),
                    "timeout": int(getattr(ssh_obj, "timeout", args.ssh_timeout) or args.ssh_timeout),
                    "use_sudo": bool(getattr(ssh_obj, "use_sudo", True)),
                },
            )
        canonical_node = inventory[matched_name]
        for alias in {node.strip(), instance_host.strip(), hostname_label.strip()}:
            if alias and alias not in inventory:
                inventory[alias] = canonical_node
        warnings["ssh_inventory"] = f"using inventory node {matched_name} ({getattr(inventory[matched_name].ssh, 'host', '')}) for alert host {hostname_label or instance_host}"
        return inventory, warnings

    dynamic_host = instance_host or node.strip()
    if dynamic_host and ssh_user and ssh_password:
        inventory[node or dynamic_host] = _build_inventory_node(
            node or dynamic_host,
            {
                "host": dynamic_host,
                "port": args.ssh_port,
                "user": ssh_user,
                "password": ssh_password,
                "timeout": args.ssh_timeout,
                "use_sudo": True,
            },
        )
        warnings["ssh_inventory"] = f"using direct SSH target {dynamic_host} from alert context"
        return inventory, warnings

    if inventory:
        warnings["ssh_inventory"] = "no inventory entry matched the alert node/instance; set --ssh-user/--ssh-password for direct SSH fallback"
    else:
        warnings["ssh_inventory"] = "no SSH inventory available; set --live-inventory or provide --ssh-user/--ssh-password"
    return {}, warnings


def build_redfish_target(
    *,
    args: argparse.Namespace,
    config_path: str,
    node: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    warnings: dict[str, str] = {}
    inventory: dict[str, dict[str, Any]] = {}

    live_inventory_path = _resolve_live_inventory_path(config_path, args.live_inventory)
    if live_inventory_path and live_inventory_path.exists():
        try:
            inventory = _flatten_live_redfish_inventory(_load_yaml_file(live_inventory_path))
        except Exception as exc:  # noqa: BLE001
            warnings["redfish_inventory"] = f"failed to load redfish inventory: {exc}"

    hostname_label = str(node or "").strip().lower()
    candidate_keys = {str(node or "").strip()}

    matched_name = ""
    for inventory_name in inventory:
        aliases = _worker_aliases(inventory_name)
        if hostname_label and hostname_label in aliases:
            matched_name = inventory_name
            break
        if inventory_name in candidate_keys:
            matched_name = inventory_name
            break

    bmc_host_override = str(args.bmc_host or "").strip()
    username_override = str(args.bmc_username or "").strip()
    password_override = str(args.bmc_password or "").strip()
    verify_tls_override = args.bmc_verify_tls

    if matched_name:
        target = dict(inventory[matched_name])
        target["bmc_host"] = bmc_host_override or target.get("bmc_host", "")
        target["username"] = username_override or target.get("username", "")
        password = password_override or str(target.get("password") or "").strip()
        target["password"] = "" if password.upper() == "REPLACE_ME" else password
        if verify_tls_override is not None:
            target["verify_tls"] = bool(verify_tls_override)
        target["node"] = matched_name
        if not str(target.get("bmc_host") or "").strip() or not str(target.get("username") or "").strip() or not str(target.get("password") or "").strip():
            warnings["redfish_inventory"] = (
                f"matched inventory node {matched_name} for {node}, but BMC credentials are incomplete; "
                "provide --bmc-username/--bmc-password or SRE_REDFISH_*"
            )
            return {}, warnings
        warnings["redfish_inventory"] = f"using inventory node {matched_name} ({target.get('bmc_host', '')}) for fan control on {node}"
        return {matched_name: target, node: target}, warnings

    if bmc_host_override and username_override and password_override:
        target = {
            "node": node,
            "bmc_host": bmc_host_override,
            "username": username_override,
            "password": password_override,
            "verify_tls": bool(True if verify_tls_override is None else verify_tls_override),
            "timeout": 30,
        }
        warnings["redfish_inventory"] = f"using direct BMC target {bmc_host_override} for node {node}"
        return {node: target}, warnings

    warnings["redfish_inventory"] = "no redfish target available; set live inventory redfish credentials or provide --bmc-host/--bmc-username/--bmc-password"
    return {}, warnings


def normalize_alert_payload(alert: Alert) -> dict[str, Any]:
    return {
        "alert_name": alert.alert_name,
        "status": alert.status.value,
        "severity": alert.severity.value,
        "summary": alert.summary,
        "description": alert.description,
        "labels": alert.labels,
        "annotations": alert.annotations,
        "starts_at": alert.starts_at.isoformat(),
        "ends_at": alert.ends_at.isoformat() if alert.ends_at else None,
        "fingerprint": alert.fingerprint,
        "source": alert.source,
    }


async def collect_alert_context(*, prometheus_url: str, alert_name: str, lookback: str) -> tuple[Alert, list[Alert], list[Alert]]:
    backend = PrometheusHttpBackend(base_url=prometheus_url, timeout=30.0, retries=3)
    channel = AlertChannel(alertmanager_url="", prometheus_url=prometheus_url, metrics_backend=backend)
    try:
        await channel.connect()
        current = await channel.get_alerts(filter_labels={"alertname": alert_name})
        history = await channel.get_alert_history(alert_name, lookback=lookback)
    finally:
        await channel.disconnect()
        await backend.aclose()
    if current:
        return current[0], current, history
    if history:
        return history[0], [], history
    raise SystemExit(f"No current or historical alerts found for {alert_name}")


def _select_firing_alert(current: list[Alert], history: list[Alert]) -> Alert | None:
    for alert in current:
        if alert.status.value == "firing":
            return alert
    for alert in history:
        if alert.status.value == "firing":
            return alert
    return None


async def wait_for_current_alert(
    *,
    prometheus_url: str,
    alert_name: str,
    lookback: str,
    timeout_sec: float,
    poll_interval_sec: float,
) -> tuple[Alert, list[Alert], list[Alert]]:
    deadline = asyncio.get_running_loop().time() + max(timeout_sec, 1.0)
    last_history: list[Alert] = []
    while True:
        backend = PrometheusHttpBackend(base_url=prometheus_url, timeout=30.0, retries=3)
        channel = AlertChannel(alertmanager_url="", prometheus_url=prometheus_url, metrics_backend=backend)
        try:
            await channel.connect()
            current = await channel.get_alerts(filter_labels={"alertname": alert_name})
            history = await channel.get_alert_history(alert_name, lookback=lookback)
            last_history = history
            selected = _select_firing_alert(current, history)
            if selected is not None:
                return selected, current, history
        finally:
            await channel.disconnect()
            await backend.aclose()
        if asyncio.get_running_loop().time() >= deadline:
            historical_firing = _select_firing_alert([], last_history)
            if historical_firing is not None:
                return historical_firing, [], last_history
            raise SystemExit(
                f"Timed out waiting for firing alert {alert_name} after {int(timeout_sec)}s"
            )
        await asyncio.sleep(max(poll_interval_sec, 1.0))


def _extract_temperature_c(alert: Alert) -> int | None:
    texts = [
        str(alert.description or ""),
        str(alert.summary or ""),
        str((alert.annotations or {}).get("description") or ""),
    ]
    for text in texts:
        explicit_value = re.search(r"value\s*=\s*(\d{2,3})\s*C\b", text, flags=re.IGNORECASE)
        if explicit_value:
            return int(explicit_value.group(1))
        match = re.search(r"(\d{2,3})\s*C\b", text)
        if match:
            return int(match.group(1))
    for key in ("temperature_c", "temperature", "gpu_temperature"):
        value = str((alert.labels or {}).get(key) or "").strip()
        if value.isdigit():
            return int(value)
    return None


def _extract_node(alert: Alert, override: str) -> str:
    if override.strip():
        return override.strip()
    for key in ("node", "Hostname", "instance", "exported_instance"):
        value = str((alert.labels or {}).get(key) or "").strip()
        if value:
            return value
    return "unknown-node"


def _extract_gpu(alert: Alert, override: str) -> str:
    if override.strip():
        return override.strip()
    for key in ("gpu", "gpu_index", "index"):
        value = str((alert.labels or {}).get(key) or "").strip()
        if value:
            return value
    return "unknown-gpu"


def build_query(*, alert: Alert, include_bmc_guidance: bool = False) -> str:
    payload = normalize_alert_payload(alert)
    extra = ""
    if include_bmc_guidance:
        extra = """

If `bmc.get_fan_status` is available for this node, it is relevant evidence for a thermal diagnosis.
""".rstrip()
    return f"""
You are handling a live alert.

Alert payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Diagnose this alert. If a reusable skill is a strong fit, use the skills tools first. Then conclude with a structured diagnosis.{extra}
""".strip()


def _build_node_command_from_args(args: Any) -> str:
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, list):
        return "nvidia-smi " + " ".join(str(item) for item in args if str(item).strip())
    return "nvidia-smi"


def _build_gpu_read_command(metrics: Any) -> str:
    metrics_list = [str(item).strip() for item in (metrics or []) if str(item).strip()]
    metric_flags = {
        "fan_speed": "fan.speed",
        "temperature": "temperature.gpu",
        "power_draw": "power.draw",
        "power_limit": "power.limit",
        "pstate": "pstate",
        "clocks_sm": "clocks.current.sm",
    }
    queries = [metric_flags[item] for item in metrics_list if item in metric_flags]
    if not queries:
        queries = ["fan.speed", "temperature.gpu", "power.draw"]
    return (
        "nvidia-smi "
        f"--query-gpu={','.join(queries)} "
        "--format=csv,noheader"
    )


def _normalize_remediation_plan(
    plan_payload: dict[str, Any] | None,
    *,
    node: str,
) -> RemediationPlan | None:
    if not isinstance(plan_payload, dict):
        return None

    plan_data = json.loads(json.dumps(plan_payload))
    for step in plan_data.get("steps", []):
        if not isinstance(step, dict):
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            params = {}
            step["params"] = params
        params.setdefault("node", node)
        tool_name = str(step.get("tool") or "").strip()
        if tool_name == "write.nvidia_smi":
            params["command"] = _build_node_command_from_args(params.get("args"))
            step["tool"] = "node.run_command"
        elif tool_name in {"read.gpu", "k8s.exec", "k8s.exec_on_node"}:
            command = str(params.get("command") or "").strip()
            if not command:
                if tool_name == "read.gpu":
                    command = _build_gpu_read_command(params.get("metrics"))
                else:
                    command = "true"
            params["command"] = command
            step["tool"] = "node.run_command"
        step.pop("command", None)
    return RemediationPlan.model_validate(plan_data)


async def _node_run_command(params: dict[str, Any], _context: ToolExecutionContext) -> dict[str, Any]:
    node = str(params.get("node") or "").strip()
    command = str(params.get("command") or "").strip()
    return {
        "node": node,
        "command": command,
        "note": "gpu thermal demo remediation uses mock execution by default",
    }


def _extract_redfish_payload(result: Any, *, action: str) -> Any:
    success = getattr(result, "success", None)
    if success is not None and not bool(success):
        message = str(getattr(result, "error", "") or "").strip()
        raise RuntimeError(message or f"redfish action failed: {action}")
    output = getattr(result, "output", None)
    if isinstance(output, str) and output.strip():
        try:
            return json.loads(output)
        except ValueError:
            return output
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return output


def _resolve_bmc_target(params: dict[str, Any], context: ToolExecutionContext) -> tuple[str, bool, str]:
    requested_node = str(params.get("node") or "").strip()
    if not requested_node:
        raise RuntimeError("parameter 'node' is required")
    targets = context.metadata.get("bmc_targets") or {}
    target = targets.get(requested_node)
    if not isinstance(target, dict):
        raise RuntimeError(f"no BMC target mapping found for node '{requested_node}'")
    bmc_host = str(target.get("bmc_host") or "").strip()
    if not bmc_host:
        raise RuntimeError(f"BMC host missing for node '{requested_node}'")
    verify_tls = bool(target.get("verify_tls", True))
    return bmc_host, verify_tls, requested_node


def _summarize_fan_status(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"mode_name": "Unknown", "is_manual": False, "is_fixed_pwm": False}
    fan_mode_raw = payload.get("fanMode")
    try:
        fan_mode = int(fan_mode_raw)
    except (TypeError, ValueError):
        fan_mode = None
    mode_name = {0: "Auto", 1: "Manual"}.get(fan_mode, "Unknown")
    all_fans = payload.get("allFanInfo")
    pwm_values: list[int] = []
    if isinstance(all_fans, list):
        for backplane in all_fans:
            if not isinstance(backplane, dict):
                continue
            for fan in backplane.get("fanInfo") or []:
                if not isinstance(fan, dict):
                    continue
                pwm = fan.get("fanPWM")
                try:
                    pwm_values.append(int(pwm))
                except (TypeError, ValueError):
                    continue
    unique_pwm_values = sorted(set(pwm_values))
    return {
        "mode_name": mode_name,
        "is_manual": fan_mode == 1,
        "is_auto": fan_mode == 0,
        "is_fixed_pwm": len(unique_pwm_values) == 1 and bool(unique_pwm_values),
        "fixed_pwm": unique_pwm_values[0] if len(unique_pwm_values) == 1 else None,
        "unique_pwm_values": unique_pwm_values,
        "fan_count": len(pwm_values),
    }


async def _bmc_get_fan_status(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise RuntimeError("required channel is missing: redfish")
    bmc_host, verify_tls, node = _resolve_bmc_target(params, context)
    value = await channel.get_web_fan_status(bmc_host, verify_tls=verify_tls)
    payload = _extract_redfish_payload(value, action="get_web_fan_status")
    summary = _summarize_fan_status(payload)
    return {
        "node": node,
        "bmc_host": bmc_host,
        "fan_status_summary": summary,
        "fan_status": payload,
    }


async def _bmc_set_fan_control(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = context.channels.get("redfish")
    if channel is None:
        raise RuntimeError("required channel is missing: redfish")
    bmc_host, verify_tls, node = _resolve_bmc_target(params, context)
    fan_index = int(params.get("fan_index", 0))
    fan_bp_index = int(params.get("fan_bp_index", 0xFF))
    mode = str(params.get("mode") or "").strip() or "Auto"
    pwm = params.get("pwm")
    value = await channel.set_web_fan_control(
        bmc_host=bmc_host,
        fan_index=fan_index,
        mode=mode,
        pwm=None if pwm is None else int(pwm),
        fan_bp_index=fan_bp_index,
        verify_tls=verify_tls,
    )
    payload = _extract_redfish_payload(value, action="set_web_fan_control")
    return {
        "node": node,
        "bmc_host": bmc_host,
        "fan_index": fan_index,
        "mode": mode,
        "pwm": None if pwm is None else int(pwm),
        "result": payload,
    }


def build_demo_registry() -> ToolRegistry:
    registry = build_default_registry()
    registry.register(
        ToolDefinition(
            name="bmc.get_fan_status",
            description="Read node fan control mode and PWM from the BMC web fan-status API.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"], "properties": {"node": {"type": "string"}}},
            tags=("bmc", "fan", "thermal", "read"),
            command_template="bmc fan-status --node {node}",
        ),
        _bmc_get_fan_status,
    )
    registry.register(
        ToolDefinition(
            name="bmc.set_fan_control",
            description="Set node fan control mode and optional PWM through the BMC web fan-status API.",
            safety_level=SafetyLevel.HIGH,
            params_schema={
                "type": "object",
                "required": ["node", "mode"],
                "properties": {
                    "node": {"type": "string"},
                    "mode": {"type": "string"},
                    "pwm": {"type": "integer"},
                    "fan_index": {"type": "integer"},
                    "fan_bp_index": {"type": "integer"},
                },
            },
            tags=("bmc", "fan", "thermal", "write"),
            needs_approval=True,
            command_template="bmc fan-control --node {node} --mode {mode} --pwm {pwm}",
        ),
        _bmc_set_fan_control,
    )
    return registry


async def inject_low_pwm_fault(
    *,
    channel: RedfishChannel,
    target: dict[str, Any],
    fan_index: int,
    fan_bp_index: int,
    pwm: int,
) -> dict[str, Any]:
    bmc_host = str(target.get("bmc_host") or "").strip()
    verify_tls = bool(target.get("verify_tls", True))
    before = await channel.get_web_fan_status(bmc_host, verify_tls=verify_tls)
    applied = await channel.set_web_fan_control(
        bmc_host=bmc_host,
        fan_index=fan_index,
        fan_bp_index=fan_bp_index,
        mode="Manual",
        pwm=pwm,
        verify_tls=verify_tls,
    )
    after = await channel.get_web_fan_status(bmc_host, verify_tls=verify_tls)
    return {
        "bmc_host": bmc_host,
        "before": _extract_redfish_payload(before, action="get_web_fan_status"),
        "applied": _extract_redfish_payload(applied, action="set_web_fan_control"),
        "after": _extract_redfish_payload(after, action="get_web_fan_status"),
        "fan_index": fan_index,
        "fan_bp_index": fan_bp_index,
        "pwm": pwm,
    }


async def restore_fan_auto(
    *,
    channel: RedfishChannel,
    target: dict[str, Any],
    fan_index: int,
    fan_bp_index: int,
) -> dict[str, Any]:
    bmc_host = str(target.get("bmc_host") or "").strip()
    verify_tls = bool(target.get("verify_tls", True))
    applied = await channel.set_web_fan_control(
        bmc_host=bmc_host,
        fan_index=fan_index,
        fan_bp_index=fan_bp_index,
        mode="Auto",
        verify_tls=verify_tls,
    )
    after = await channel.get_web_fan_status(bmc_host, verify_tls=verify_tls)
    return {
        "bmc_host": bmc_host,
        "applied": _extract_redfish_payload(applied, action="set_web_fan_control"),
        "after": _extract_redfish_payload(after, action="get_web_fan_status"),
    }


async def start_gpu_burn(
    *,
    channel: SSHChannel,
    node: str,
    workdir: str,
    command: str,
) -> dict[str, Any]:
    log_path = "/tmp/gpu_thermal_skill_demo_gpu_burn.log"
    probe_command = "ps -eo pid=,comm= | awk '$2 == \"gpu_burn\" {print $1}'"
    wrapped = (
        "bash -lc "
        + shlex.quote(
            f"cd {workdir} && setsid nohup {command} </dev/null >{log_path} 2>&1 & echo $!; exit 0"
        )
    )
    result = await channel.run_command(node, wrapped, use_sudo=False)
    pid_text = str(result.output or "").strip().splitlines()[-1].strip() if result.success else ""
    if not result.success:
        probe = await channel.run_command(node, probe_command, use_sudo=False)
        probe_output = str(probe.output or "").strip()
        if not probe.success or not probe_output:
            raise RuntimeError(str(result.error or result.output or "failed to start gpu_burn"))
    probe = await channel.run_command(node, probe_command, use_sudo=False)
    pid_list: list[int] = []
    for line in str(probe.output or "").splitlines():
        value = line.strip()
        if value.isdigit():
            pid_list.append(int(value))
    return {
        "node": node,
        "pid": int(pid_text) if pid_text.isdigit() else None,
        "pids": pid_list,
        "log_path": log_path,
        "command": command,
        "workdir": workdir,
    }


async def stop_gpu_burn(
    *,
    channel: SSHChannel,
    node: str,
    pid: int | None,
    pids: list[int] | None = None,
) -> dict[str, Any]:
    known_pids = [value for value in (pids or []) if isinstance(value, int)]
    use_sudo = False
    if known_pids:
        command = "kill -9 -- " + " ".join(str(value) for value in known_pids) + " || true"
    elif pid is not None:
        command = f"kill -9 -- {pid} || true"
    else:
        command = "pkill -x gpu_burn || pkill -f '/tmp/gpu-burn/gpu_burn' || pkill -f './gpu_burn' || true"
    result = await channel.run_command(node, command, use_sudo=use_sudo)
    return {
        "node": node,
        "pid": pid,
        "pids": known_pids,
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }


async def execute_remediation_plan(
    *,
    plan_payload: dict[str, Any] | None,
    context: ToolExecutionContext,
    wal_path: str,
    approval_user: str,
    node: str,
    tool_registry: ToolRegistry | None = None,
    execution_mode: str = "mock",
) -> dict[str, Any]:
    if not isinstance(plan_payload, dict):
        return {"executed": False, "reason": "no remediation plan generated"}

    try:
        plan = _normalize_remediation_plan(plan_payload, node=node)
    except Exception as exc:  # noqa: BLE001
        return {
            "executed": False,
            "reason": "plan normalization failed",
            "error": str(exc),
            "raw_plan": plan_payload,
        }
    if plan is None:
        return {"executed": False, "reason": "no remediation plan generated"}

    wal_target = Path(wal_path).expanduser()
    wal_target.parent.mkdir(parents=True, exist_ok=True)

    registry = tool_registry or build_default_registry()
    registry.register(
        ToolDefinition(
            name="node.run_command",
            description="Run an administrative command on a target node.",
            safety_level=SafetyLevel.HIGH,
            params_schema={
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "command": {"type": "string"},
                },
                "required": ["node", "command"],
            },
            tags=("ssh", "write", "node"),
            needs_approval=True,
            command_template="ssh {node} -- {command}",
        ),
        _node_run_command,
    )

    engine = RemediationEngine(
        tool_registry=registry,
        approval_gate=ApprovalGate(default_policy="auto_approve"),
        wal=RollbackJournal(wal_target),
        execution_context=context,
        execution_mode=execution_mode,
    )
    session_id = f"gpu-thermal-remediation-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    try:
        result = await engine.execute(plan, session_id=session_id)
    except PlanValidationError as exc:
        return {
            "executed": False,
            "session_id": session_id,
            "approval_user": approval_user,
            "execution_mode": execution_mode,
            "wal_path": str(wal_target),
            "error": "plan validation failed",
            "details": exc.errors,
            "plan": plan.model_dump(mode="json"),
        }

    return {
        "executed": True,
        "session_id": session_id,
        "approval_user": approval_user,
        "execution_mode": execution_mode,
        "wal_path": str(wal_target),
        "plan": plan.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
    }


async def close_context(context: ToolExecutionContext) -> None:
    for channel in context.channels.values():
        for method_name in ("disconnect", "close"):
            method = getattr(channel, method_name, None)
            if not callable(method):
                continue
            value = method()
            if hasattr(value, "__await__"):
                await value
            break


def render_text_output(payload: dict[str, Any]) -> str:
    lines = [
        "GPU Thermal Skill Demo",
        f"Model: {payload.get('model') or '<unset>'}",
        f"Provider Base URL: {payload.get('provider_base_url') or '<default>'}",
        f"Alert Name: {payload.get('alert', {}).get('alert_name')}",
        f"Alert Source: {payload.get('alert_source')}",
        f"Node: {payload.get('node')}",
        f"GPU: {payload.get('gpu')}",
        f"Temperature C: {payload.get('temperature_c')}",
        f"Status: {payload.get('status')}",
        f"Session ID: {payload.get('session_id')}",
        "",
        "Summary:",
        str(payload.get("summary") or ""),
        "",
        "Tool Runs:",
    ]
    tool_runs = payload.get("tool_runs") or []
    if not tool_runs:
        lines.append("- none")
    else:
        for idx, run in enumerate(tool_runs, start=1):
            lines.append(
                f"{idx}. {run.get('tool')} success={run.get('success')} params={json.dumps(run.get('params'), ensure_ascii=False)}"
            )
            error = str(run.get("error") or "").strip()
            if error:
                lines.append(f"   error={error}")
    lines.extend(
        [
            "",
            "Diagnosis Result:",
            json.dumps(payload.get("diagnosis_result"), ensure_ascii=False, indent=2),
            "",
            "Remediation Plan:",
            json.dumps(payload.get("remediation_plan"), ensure_ascii=False, indent=2),
            "",
            "Remediation Execution:",
            json.dumps(payload.get("remediation_execution"), ensure_ascii=False, indent=2),
        ]
    )
    return "\n".join(lines) + "\n"


def maybe_write_output(path_text: str, output_format: str, payload: dict[str, Any]) -> None:
    target = Path(path_text).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    target.write_text(render_text_output(payload), encoding="utf-8")


def resolve_trace_path(output_path: str, trace_path: str) -> Path:
    if trace_path.strip():
        return Path(trace_path).expanduser()
    target = Path(output_path).expanduser()
    return target.parent / f"{target.name}.trace.log"


def append_trace(trace_path: Path, stage: str, **fields: Any) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "stage": stage,
        **fields,
    }
    with trace_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    trace_path = resolve_trace_path(args.output, args.trace_path)
    append_trace(trace_path, "start", alert_name=args.alert_name, inject_faults=args.inject_faults)
    prometheus_url = load_prometheus_url(args.config)
    pre_alert_node = args.node.strip() or _derive_default_node(args.alert_name)
    selected_alert = None
    current_alerts: list[Alert] = []
    history_alerts: list[Alert] = []
    node = pre_alert_node
    gpu = args.gpu.strip()
    temperature_c = None

    inventory: dict[str, Any] = {}
    context_warnings: dict[str, str] = {}
    redfish_targets: dict[str, dict[str, Any]] = {}
    if node:
        dummy_alert = Alert(
            alert_name=args.alert_name,
            status="firing",
            severity="warning",
            summary="",
            description="",
            labels={"Hostname": node},
            annotations={},
            starts_at=datetime.now(UTC),
            ends_at=None,
            fingerprint=f"{args.alert_name}:{node}",
            source=None,
        )
        inventory, context_warnings = build_ssh_inventory(
            args=args,
            config_path=args.config,
            alert=dummy_alert,
            node=node,
        )
        redfish_targets, redfish_warnings = build_redfish_target(
            args=args,
            config_path=args.config,
            node=node,
        )
        context_warnings.update(redfish_warnings)

    channels: dict[str, Any] = {}
    if inventory:
        channels["ssh"] = SSHChannel(inventory=inventory, dry_run=False, command_timeout=args.ssh_timeout)
    if redfish_targets:
        redfish_channel = RedfishChannel(timeout=30)
        unique_hosts: dict[str, dict[str, Any]] = {}
        for target in redfish_targets.values():
            bmc_host = str(target.get("bmc_host") or "").strip()
            if not bmc_host:
                continue
            unique_hosts[bmc_host] = target
        for target in unique_hosts.values():
            username = str(target.get("username") or "").strip()
            password = str(target.get("password") or "").strip()
            if username and password and hasattr(redfish_channel, "set_web_credentials"):
                redfish_channel.set_web_credentials(str(target["bmc_host"]), username, password)
        channels["redfish"] = redfish_channel

    bmc_targets: dict[str, dict[str, Any]] = {}
    for key, target in redfish_targets.items():
        bmc_targets[key] = {
            "bmc_host": str(target.get("bmc_host") or "").strip(),
            "verify_tls": bool(target.get("verify_tls", True)),
            "node": str(target.get("node") or key),
        }
    context = ToolExecutionContext(
        channels=channels,
        metadata={
            "context_warnings": context_warnings,
            "bmc_targets": bmc_targets,
        },
    )
    registry = build_demo_registry()

    injection_artifacts: dict[str, Any] = {}
    result: dict[str, Any] = {}
    remediation_execution: dict[str, Any] = {"executed": False, "reason": "not started"}
    try:
        if args.inject_faults:
            append_trace(trace_path, "inject_faults_begin", node=node)
            if node == "":
                raise SystemExit("Fault injection requires --node or an alert name that can be mapped to a node")
            ssh_channel = channels.get("ssh")
            redfish_channel = channels.get("redfish")
            if ssh_channel is None:
                raise SystemExit("Fault injection requires SSH access to the target node")
            if redfish_channel is None:
                raise SystemExit("Fault injection requires redfish/BMC access to the target node")
            redfish_target = redfish_targets.get(node) or next(iter(redfish_targets.values()), None)
            if not isinstance(redfish_target, dict):
                raise SystemExit(f"No BMC target resolved for node {node}")
            injection_artifacts["fan_injection"] = await inject_low_pwm_fault(
                channel=redfish_channel,
                target=redfish_target,
                fan_index=args.inject_fan_index,
                fan_bp_index=args.inject_fan_bp_index,
                pwm=args.inject_fan_pwm,
            )
            append_trace(
                trace_path,
                "inject_faults_fan_applied",
                fan_index=args.inject_fan_index,
                fan_bp_index=args.inject_fan_bp_index,
                pwm=args.inject_fan_pwm,
            )
            injection_artifacts["gpu_burn"] = await start_gpu_burn(
                channel=ssh_channel,
                node=node,
                workdir=args.gpu_burn_workdir,
                command=args.gpu_burn_command,
            )
            append_trace(trace_path, "inject_faults_gpu_burn_started", gpu_burn=injection_artifacts.get("gpu_burn"))
            append_trace(trace_path, "wait_for_alert_begin", alert_name=args.alert_name)
            selected_alert, current_alerts, history_alerts = await wait_for_current_alert(
                prometheus_url=prometheus_url,
                alert_name=args.alert_name,
                lookback=args.lookback,
                timeout_sec=args.alert_wait_timeout,
                poll_interval_sec=args.alert_poll_interval,
            )
            append_trace(
                trace_path,
                "wait_for_alert_done",
                selected_status=selected_alert.status.value,
                current_alert_count=len(current_alerts),
                history_alert_count=len(history_alerts),
            )
        else:
            append_trace(trace_path, "collect_alert_context_begin", alert_name=args.alert_name)
            selected_alert, current_alerts, history_alerts = await collect_alert_context(
                prometheus_url=prometheus_url,
                alert_name=args.alert_name,
                lookback=args.lookback,
            )
            append_trace(
                trace_path,
                "collect_alert_context_done",
                selected_status=selected_alert.status.value,
                current_alert_count=len(current_alerts),
                history_alert_count=len(history_alerts),
            )

        node = _extract_node(selected_alert, args.node or node)
        gpu = _extract_gpu(selected_alert, args.gpu or gpu)
        temperature_c = _extract_temperature_c(selected_alert)
        append_trace(trace_path, "alert_normalized", node=node, gpu=gpu, temperature_c=temperature_c)
        if not inventory or "ssh" not in channels:
            inventory, ssh_warnings = build_ssh_inventory(
                args=args,
                config_path=args.config,
                alert=selected_alert,
                node=node,
            )
            context_warnings.update(ssh_warnings)
            if inventory and "ssh" not in channels:
                channels["ssh"] = SSHChannel(inventory=inventory, dry_run=False, command_timeout=args.ssh_timeout)
                context.channels["ssh"] = channels["ssh"]
        query = build_query(alert=selected_alert, include_bmc_guidance="redfish" in channels)
        append_trace(trace_path, "diagnosis_begin", allowed_tools=args.allowed_tools)
        result = await run_diagnosis(
            query=query,
            context=context,
            variables=None,
            allowed_tool_names=args.allowed_tools,
            tool_registry=registry,
            checkpoint_dir=args.checkpoint_dir or None,
            total_timeout_sec=args.total_timeout,
            step_timeout_sec=args.step_timeout,
            max_steps=args.max_steps,
        )
        append_trace(
            trace_path,
            "diagnosis_done",
            status=result.get("status"),
            tool_run_count=len(result.get("tool_runs") or []),
            has_remediation_plan=bool(result.get("remediation_plan")),
        )
        remediation_execution = (
            await execute_remediation_plan(
                plan_payload=result.get("remediation_plan"),
                context=context,
                wal_path=args.wal_path,
                approval_user=args.approval_user,
                node=node,
                tool_registry=registry,
                execution_mode=args.remediation_mode,
            )
            if args.execute_remediation
            else {"executed": False, "reason": "execution disabled"}
        )
        append_trace(trace_path, "remediation_done", remediation_execution=remediation_execution)
    except Exception as exc:
        append_trace(
            trace_path,
            "exception",
            error_type=exc.__class__.__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        raise
    finally:
        append_trace(trace_path, "cleanup_begin", inject_faults=args.inject_faults, skip_cleanup=args.skip_cleanup)
        if args.inject_faults and not args.skip_cleanup:
            ssh_channel = channels.get("ssh")
            redfish_channel = channels.get("redfish")
            gpu_burn = injection_artifacts.get("gpu_burn") if isinstance(injection_artifacts, dict) else None
            fan_target = next(iter(redfish_targets.values()), None)
            if ssh_channel is not None and isinstance(gpu_burn, dict):
                try:
                    append_trace(
                        trace_path,
                        "cleanup_stop_gpu_burn_begin",
                        node=node or str(gpu_burn.get("node") or ""),
                        pid=gpu_burn.get("pid"),
                        pids=gpu_burn.get("pids"),
                    )
                    stop_result = await stop_gpu_burn(
                        channel=ssh_channel,
                        node=node or str(gpu_burn.get("node") or ""),
                        pid=gpu_burn.get("pid"),
                        pids=gpu_burn.get("pids"),
                    )
                    append_trace(trace_path, "cleanup_stop_gpu_burn_done", result=stop_result)
                except Exception:  # noqa: BLE001
                    append_trace(trace_path, "cleanup_stop_gpu_burn_error", traceback=traceback.format_exc())
            if redfish_channel is not None and isinstance(fan_target, dict):
                try:
                    append_trace(
                        trace_path,
                        "cleanup_restore_fan_auto_begin",
                        node=str(fan_target.get("node") or ""),
                        bmc_host=str(fan_target.get("bmc_host") or ""),
                        fan_index=args.inject_fan_index,
                        fan_bp_index=args.inject_fan_bp_index,
                    )
                    restore_result = await restore_fan_auto(
                        channel=redfish_channel,
                        target=fan_target,
                        fan_index=args.inject_fan_index,
                        fan_bp_index=args.inject_fan_bp_index,
                    )
                    append_trace(trace_path, "cleanup_restore_fan_auto_done", result=restore_result)
                except Exception:  # noqa: BLE001
                    append_trace(trace_path, "cleanup_restore_fan_auto_error", traceback=traceback.format_exc())
        append_trace(trace_path, "close_context_begin", channel_names=sorted(context.channels.keys()))
        await close_context(context)
        append_trace(trace_path, "close_context_done")
    payload: dict[str, Any] = {
        "provider_base_url": os.getenv("SRE_OPENAI_BASE_URL", "").strip() or None,
        "model": os.getenv("SRE_LLM_MODEL", "").strip() or None,
        "alert_source": "live",
        "prometheus_url": prometheus_url,
        "alert": normalize_alert_payload(selected_alert),
        "current_alert_count": len(current_alerts),
        "history_alert_count": len(history_alerts),
        "node": node,
        "gpu": gpu,
        "temperature_c": temperature_c,
        "severity": selected_alert.severity.value,
        "fault_injection": injection_artifacts or None,
        "context_warnings": context_warnings,
        "status": result.get("status"),
        "summary": result.get("summary"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "remediation_execution": remediation_execution,
        "llm_interactions": result.get("llm_interactions"),
        "session_id": result.get("session_id"),
    }
    maybe_write_output(args.output, args.output_format, payload)
    append_trace(trace_path, "output_written", output=args.output, output_format=args.output_format)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "diagnosed" else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
