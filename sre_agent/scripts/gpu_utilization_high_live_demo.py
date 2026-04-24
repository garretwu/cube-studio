#!/usr/bin/env python3
"""Run a live GPUUtilizationHigh diagnosis demo from live alerts and node access config."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.alert import AlertChannel
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.ssh import SSHChannel
from lib.tests._real_backends import PrometheusHttpBackend
from sre_agent.agent import run_diagnosis
from sre_agent.config import load_config as load_agent_config
from sre_agent.models.alert import Alert
from sre_agent.tools import ToolExecutionContext

DEFAULT_AGENT_CONFIG = Path("config.yaml")
DEFAULT_DEMO_CONFIG = Path("fault_injector/gpu-utilization-high-live-demo.yaml")
DEFAULT_DEMO_KEY = "gpu_utilization_high_alert_remediation"
DEFAULT_ALLOWED_TOOLS = [
    "gpu.get_metrics",
    "gpu.get_processes",
    "bmc.get_fan_status",
    "prometheus.query_instant",
    "k8s.list_pods",
]
REQUIRED_MULTI_ROOT_TOOLS = (
    "gpu.get_metrics",
    "gpu.get_processes",
    "bmc.get_fan_status",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the live GPUUtilizationHigh ReAct diagnosis demo.")
    parser.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG), help="SRE agent config path.")
    parser.add_argument(
        "--demo-config",
        "--fault-config",
        dest="demo_config",
        default=str(DEFAULT_DEMO_CONFIG),
        help="Demo YAML path with demo section, inventory, and monitor settings.",
    )
    parser.add_argument(
        "--demo-key",
        default=DEFAULT_DEMO_KEY,
        help="Top-level demo key under demo config, for example gpu_utilization_high.",
    )
    parser.add_argument("--output", default="", help="Optional output file path override.")
    parser.add_argument(
        "--output-format",
        choices=["json", "txt"],
        default="",
        help="Optional output format override.",
    )
    parser.add_argument("--model", default="", help="Override SRE_LLM_MODEL for this run.")
    parser.add_argument("--base-url", default="", help="Override SRE_OPENAI_BASE_URL for this run.")
    return parser


def apply_model_env(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["SRE_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["SRE_OPENAI_BASE_URL"] = args.base_url


def load_demo_settings(path: str, demo_key: str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    demos = raw.get("demo")
    if not isinstance(demos, dict):
        raise SystemExit(f"Demo config {path} is missing top-level 'demo' configuration")
    selected = demos.get(demo_key)
    if not isinstance(selected, dict):
        raise SystemExit(f"Demo config {path} is missing demo.{demo_key}")
    return selected


def load_demo_config(path: str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"Demo config {path} must contain a YAML object at the top level")
    return raw


def _build_node_config(node_name: str, raw_node: dict[str, Any]) -> Any:
    ssh = raw_node.get("ssh")
    if not isinstance(ssh, dict):
        raise SystemExit(f"inventory node {node_name!r} is missing ssh configuration")
    ssh_config = SimpleNamespace(
        host=str(ssh.get("host") or "").strip(),
        port=int(ssh.get("port") or 22),
        user=str(ssh.get("user") or "root").strip(),
        key_file=ssh.get("key_file"),
        password=ssh.get("password"),
        timeout=int(ssh.get("timeout") or 30),
        use_sudo=bool(ssh.get("use_sudo", True)),
    )
    if not ssh_config.host:
        raise SystemExit(f"inventory node {node_name!r} is missing ssh.host")
    return SimpleNamespace(name=node_name, ssh=ssh_config)


def flatten_inventory(raw_cfg: dict[str, Any]) -> dict[str, Any]:
    raw_inventory = raw_cfg.get("inventory")
    if not isinstance(raw_inventory, dict):
        raise SystemExit("Demo config is missing top-level inventory section")

    flattened: dict[str, Any] = {}
    for group_nodes in raw_inventory.values():
        if not isinstance(group_nodes, list):
            continue
        for raw_node in group_nodes:
            if not isinstance(raw_node, dict):
                continue
            node_name = str(raw_node.get("name") or "").strip()
            if not node_name:
                continue
            flattened[node_name] = _build_node_config(node_name, raw_node)
    return flattened


def load_prometheus_url(raw_cfg: dict[str, Any]) -> str:
    monitor = raw_cfg.get("monitor")
    if not isinstance(monitor, dict):
        return ""
    return str(monitor.get("prometheus_url") or "").strip()


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


def infer_service_from_pod_name(pod_name: str) -> str:
    text = str(pod_name or "").strip()
    if not text:
        return ""
    parts = [part for part in text.split("-") if part]
    if len(parts) >= 3 and len(parts[-1]) == 5 and parts[-1].isalnum():
        previous = parts[-2]
        if 6 <= len(previous) <= 12 and previous.isalnum():
            return "-".join(parts[:-2])
    if len(parts) >= 2 and parts[-1].isdigit():
        return "-".join(parts[:-1])
    return text


def resolve_allowed_tools(raw_allowed_tools: Any) -> list[str]:
    candidate = raw_allowed_tools if raw_allowed_tools is not None else DEFAULT_ALLOWED_TOOLS
    if not isinstance(candidate, list) or not all(isinstance(item, str) for item in candidate):
        raise SystemExit("demo.allowed_tools must be a list of tool names")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidate:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        deduped.append(name)
        seen.add(name)

    # Demo reliability guard: keep required evidence tools available even if YAML misses one.
    for required in REQUIRED_MULTI_ROOT_TOOLS:
        if required in seen:
            continue
        deduped.append(required)
        seen.add(required)
    return deduped


def _node_match_score(node: str, labels: dict[str, Any]) -> int:
    node_text = str(node or "").strip().lower()
    if not node_text:
        return 0

    label_node = str(labels.get("node") or "").strip().lower()
    label_host = str(labels.get("Hostname") or "").strip().lower()

    if node_text and (node_text == label_node or node_text == label_host):
        return 4

    suffix = node_text.split("-")[-1]
    if suffix and label_host.endswith(f"-{suffix}"):
        return 3
    if suffix and label_node.endswith(f"-{suffix}"):
        return 3
    return 0


def _gpu_sort_key(alert: Alert) -> tuple[int, int]:
    value = str(alert.labels.get("gpu", "")).strip()
    if value.isdigit():
        return (0, int(value))
    return (1, 9999)


def _score_alert(
    alert: Alert,
    *,
    node: str,
) -> int:
    score = 0
    labels = alert.labels
    score += _node_match_score(node, labels)
    if alert.status.value == "firing":
        score += 1
    return score


async def collect_alert_context(
    *,
    prometheus_url: str,
    alert_name: str,
    lookback: str,
    node: str,
) -> tuple[Alert, list[Alert], list[Alert]]:
    backend = PrometheusHttpBackend(base_url=prometheus_url, timeout=30.0, retries=3)
    channel = AlertChannel(
        alertmanager_url="",
        prometheus_url=prometheus_url,
        metrics_backend=backend,
    )
    try:
        await channel.connect()
        current = await channel.get_alerts(filter_labels={"alertname": alert_name})
        history = await channel.get_alert_history(alert_name, lookback=lookback)
    finally:
        await channel.disconnect()
        await backend.aclose()

    if current:
        selected = max(
            current,
            key=lambda item: (_score_alert(item, node=node), -_gpu_sort_key(item)[1]),
        )
        return selected, current, history
    if history:
        selected = max(
            history,
            key=lambda item: (_score_alert(item, node=node), -_gpu_sort_key(item)[1]),
        )
        return selected, [], history
    raise SystemExit(f"No current or historical alerts found for {alert_name}")


def build_query(
    *,
    alert: Alert,
    demo: dict[str, Any],
    node: str,
    namespace: str,
    service: str,
    history_count: int,
    target_gpu: str,
) -> str:
    alert_payload = json.dumps(normalize_alert_payload(alert), ensure_ascii=False, indent=2)
    return (
        "You are running the GPUUtilizationHigh demo.\n"
        "An alert was received and you should perform a read-only ReAct diagnosis.\n"
        "Focus on identifying all plausible concurrent causes for high GPU utilization.\n"
        "Treat root causes as non-exclusive.\n"
        "This demo explicitly targets two root-cause tracks:\n"
        f"- Service/workload track on node {node}: GPU burn or contention from non-vLLM compute processes.\n"
        f"- Hardware track on node {node}: thermal risk shown by high GPU temperature and fan/cooling anomalies.\n"
        "If evidence supports both tracks, diagnosis_result.root_cause MUST contain at least two items, one per track.\n"
        "Do not collapse both tracks into one generic root cause.\n"
        "Use the available read-only tools to gather concrete evidence.\n"
        "Required evidence order:\n"
        f"1. Inspect gpu metrics on node {node} with gpu.get_metrics and pay special attention to GPU {target_gpu or '<target>'}; capture utilization and temperature.\n"
        f"2. Inspect GPU processes on node {node} with gpu.get_processes and look for non-vLLM compute processes on GPU {target_gpu or '<target>'}.\n"
        f"3. Inspect BMC fan status on node {node} with bmc.get_fan_status and capture fan mode/PWM/RPM.\n"
        f"4. If useful, inspect pods in namespace {namespace} with k8s.list_pods.\n"
        "5. If useful, use Prometheus for one supporting metric check.\n"
        "Output contract:\n"
        "- diagnosis_result.root_cause is the canonical output field.\n"
        "- Keep root_cause[0] as the highest-confidence primary cause for remediation planning.\n"
        "- When burn/contention and thermal evidence both exist, return at least two root_cause items.\n"
        "- Use layer=service/platform for burn-contention and layer=hardware for thermal.\n"
        "- If thermal evidence is missing because bmc.get_fan_status failed, explicitly mark that evidence gap and lower confidence.\n"
        "If the evidence is strong enough, produce a proposal-only remediation plan.\n"
        "If you propose k8s.delete_pod, use params.namespace plus either params.label_selector or params.pod_name.\n"
        "Do not use params.pod_selector.\n"
        "Do not claim any write action was executed.\n\n"
        f"Configured demo target node: {node}\n"
        f"Configured target GPU: {target_gpu or '<unset>'}\n"
        f"Configured namespace: {namespace}\n"
        f"Configured service hint: {service}\n"
        f"Historical alert samples found in lookback window: {history_count}\n"
        f"Reference scenario: {json.dumps(demo, ensure_ascii=False)}\n\n"
        f"Live alert payload:\n{alert_payload}"
    )


def render_text_output(payload: dict[str, Any]) -> str:
    lines = [
        "AIDC Auto-SRE GPUUtilizationHigh Demo",
        f"Model: {payload.get('model') or '<unset>'}",
        f"Provider Base URL: {payload.get('provider_base_url') or '<default>'}",
        f"Alert Name: {payload.get('alert', {}).get('alert_name')}",
        f"Alert Source: {payload.get('alert_source')}",
        f"Target GPU: {payload.get('target_gpu')}",
        f"Node: {payload.get('node')}",
        f"Namespace: {payload.get('namespace')}",
        f"Service Hint: {payload.get('service')}",
        f"Prometheus URL: {payload.get('prometheus_url')}",
        f"Status: {payload.get('status')}",
        f"Session ID: {payload.get('session_id')}",
        "",
        "Selected Alert:",
        json.dumps(payload.get("alert"), ensure_ascii=False, indent=2),
        "",
        "Summary:",
        str(payload.get("summary") or ""),
        "",
        "Diagnosis Result:",
        json.dumps(payload.get("diagnosis_result"), ensure_ascii=False, indent=2),
        "",
        "Remediation Plan:",
        json.dumps(payload.get("remediation_plan"), ensure_ascii=False, indent=2),
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
    return "\n".join(lines) + "\n"


def maybe_write_output(path_text: str, output_format: str, payload: dict[str, Any]) -> None:
    target = Path(path_text).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return
    target.write_text(render_text_output(payload), encoding="utf-8")


async def close_context(context: ToolExecutionContext) -> None:
    for channel in context.channels.values():
        close = getattr(channel, "close", None)
        if callable(close):
            value = close()
            if hasattr(value, "__await__"):
                await value


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    agent_cfg = load_agent_config(args.config)
    raw_demo_cfg = load_demo_config(args.demo_config)
    demo = load_demo_settings(args.demo_config, args.demo_key)
    inventory = flatten_inventory(raw_demo_cfg)
    node = str(demo.get("node") or "").strip()
    if not node:
        raise SystemExit("demo.node is required")
    if node not in inventory:
        raise SystemExit(f"demo.node {node!r} not found in demo inventory")

    alert_name = str(demo.get("alert_name") or "GPUUtilizationHigh").strip()
    lookback = str(demo.get("lookback") or "6h").strip()
    kubeconfig = str(demo.get("kubeconfig") or "~/.kube/config").strip()
    promql = str(demo.get("promql") or "DCGM_FI_DEV_GPU_UTIL").strip()
    allowed_tools = resolve_allowed_tools(demo.get("allowed_tools"))

    prometheus_url = load_prometheus_url(raw_demo_cfg) or str(agent_cfg.global_.prometheus_url or "").strip()
    if not prometheus_url:
        raise SystemExit("Prometheus URL is required in demo config or agent config")

    selected_alert, current_alerts, history_alerts = await collect_alert_context(
        prometheus_url=prometheus_url,
        alert_name=alert_name,
        lookback=lookback,
        node=node,
    )
    selected_labels = selected_alert.labels
    namespace = str(selected_labels.get("exported_namespace") or "default").strip()
    pod_name = str(selected_labels.get("exported_pod") or "").strip()
    service = str(selected_labels.get("exported_container") or selected_labels.get("service") or "").strip()
    if not service:
        service = infer_service_from_pod_name(pod_name)
    target_gpu = str(selected_labels.get("gpu") or "").strip()
    query = build_query(
        alert=selected_alert,
        demo=demo,
        node=node,
        namespace=namespace,
        service=service,
        history_count=len(history_alerts),
        target_gpu=target_gpu,
    )

    context = ToolExecutionContext(
        channels={
            "ssh": SSHChannel(inventory=inventory, dry_run=False),
            "k8s": K8sChannel(client=None, kubeconfig=kubeconfig),
            "prometheus": PrometheusChannel(base_url=prometheus_url),
        },
        metadata={"aidc_id": agent_cfg.global_.aidc_id},
    )
    try:
        result = await run_diagnosis(
            query=query,
            context=context,
            variables={
                "aidc_id": agent_cfg.global_.aidc_id,
                "alert_name": alert_name,
                "namespace": namespace,
                "node": node,
                "target_gpu": target_gpu,
                "pod_name": pod_name,
                "service": service,
                "promql": promql,
                "demo_require_multi_root_cause": True,
                "demo_expected_root_cause_tracks": [
                    "gpu_burn_or_contention",
                    "gpu_thermal_or_cooling",
                ],
                "demo_required_evidence_tools": list(REQUIRED_MULTI_ROOT_TOOLS),
                "alert": normalize_alert_payload(selected_alert),
            },
            allowed_tool_names=allowed_tools,
            checkpoint_dir=None,
            step_timeout_sec=float(demo.get("step_timeout") or 60.0),
            total_timeout_sec=float(demo.get("total_timeout") or 180.0),
            max_steps=int(demo.get("max_steps") or 5),
        )
    finally:
        await close_context(context)

    payload: dict[str, Any] = {
        "provider_base_url": os.getenv("SRE_OPENAI_BASE_URL", "").strip() or None,
        "model": os.getenv("SRE_LLM_MODEL", "").strip() or None,
        "alert": normalize_alert_payload(selected_alert),
        "alert_source": "live",
        "current_alert_count": len(current_alerts),
        "history_alert_count": len(history_alerts),
        "target_gpu": target_gpu,
        "node": node,
        "namespace": namespace,
        "pod_name": pod_name,
        "service": service,
        "prometheus_url": prometheus_url,
        "status": result.get("status"),
        "summary": result.get("summary"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "session_id": result.get("session_id"),
    }

    output = str(args.output or demo.get("output") or "").strip()
    output_format = str(args.output_format or demo.get("output_format") or "json").strip()
    if output:
        maybe_write_output(output, output_format, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "diagnosed" else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
