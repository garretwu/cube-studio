#!/usr/bin/env python3
"""Run a live VLLMInterTokenLatencyP95High diagnosis demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from sre_agent.agent.prompts import build_alert_diagnosis_prompt
from sre_agent.config import load_config as load_agent_config
from sre_agent.models.alert import Alert
from sre_agent.models.remediation import RemediationPlan, VerificationCondition, VerificationConfig
from sre_agent.remediation import ApprovalGate, PlanValidationError, RemediationEngine, RollbackJournal
from sre_agent.tools import ToolExecutionContext, build_default_registry

DEFAULT_AGENT_CONFIG = Path("config.yaml")
DEFAULT_DEMO_CONFIG = Path("fault_injector/vllm-latency-p95-live-demo.yaml")
DEFAULT_DEMO_KEY = "vllm_inter_token_latency_p95_alert_remediation"
DEFAULT_WAL_PATH = Path("data/wal/vllm-latency-p95-live-demo.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the live VLLMInterTokenLatencyP95High ReAct diagnosis demo.")
    parser.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG), help="SRE agent config path.")
    parser.add_argument(
        "--demo-config",
        default=str(DEFAULT_DEMO_CONFIG),
        help="Demo YAML path with demo section, inventory, and monitor settings.",
    )
    parser.add_argument(
        "--demo-key",
        default=DEFAULT_DEMO_KEY,
        help="Top-level demo key under demo config.",
    )
    parser.add_argument("--output", default="", help="Optional output file path override.")
    parser.add_argument("--output-format", choices=["json", "txt"], default="", help="Optional output format override.")
    parser.add_argument("--model", default="", help="Override SRE_LLM_MODEL for this run.")
    parser.add_argument("--base-url", default="", help="Override SRE_OPENAI_BASE_URL for this run.")
    parser.add_argument(
        "--execute-remediation",
        action="store_true",
        help="Execute the generated remediation plan through RemediationEngine if one is produced.",
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
    return parser


def apply_model_env(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["SRE_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["SRE_OPENAI_BASE_URL"] = args.base_url


def load_demo_config(path: str) -> dict[str, Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"Demo config {path} must contain a YAML object at the top level")
    return raw


def load_demo_settings(raw: dict[str, Any], demo_key: str, path: str) -> dict[str, Any]:
    demos = raw.get("demo")
    if not isinstance(demos, dict):
        raise SystemExit(f"Demo config {path} is missing top-level 'demo' configuration")
    selected = demos.get(demo_key)
    if not isinstance(selected, dict):
        raise SystemExit(f"Demo config {path} is missing demo.{demo_key}")
    return selected


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


def build_threshold_alert(
    *,
    alert_name: str,
    severity: str,
    node: str,
    namespace: str,
    service: str,
    latency_value_ms: float,
    latency_threshold_ms: int,
    latency_promql: str,
) -> Alert:
    now = datetime.now(UTC)
    latency_text = f"{latency_value_ms:.2f}"
    labels = {
        "alertname": alert_name,
        "severity": severity,
        "node": node,
        "Hostname": node,
        "exported_namespace": namespace,
        "namespace": namespace,
        "service": service,
        "exported_container": service,
        "threshold_source": "promql",
    }
    annotations = {
        "summary": "Inter-token latency threshold breached",
        "description": (
            f"PromQL threshold trigger: p95 inter-token latency is {latency_text}ms, "
            f"above threshold {latency_threshold_ms}ms. node={node}, namespace={namespace}, service={service}. "
            f"promql={latency_promql}"
        ),
    }
    fingerprint = f"{alert_name}:{node}:{namespace}:{service}:promql-threshold"
    return Alert(
        alert_name=alert_name,
        severity=severity,
        labels=labels,
        annotations=annotations,
        starts_at=now,
        ends_at=None,
        fingerprint=fingerprint,
        status="firing",
        source="promql_threshold",
    )


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


def _service_match_score(service: str, namespace: str, labels: dict[str, Any]) -> int:
    score = 0
    if namespace and str(labels.get("namespace") or labels.get("exported_namespace") or "").strip() == namespace:
        score += 2
    if not service:
        return score
    service_text = str(service).strip()
    for key in ("service", "exported_service", "exported_container", "app", "model_name"):
        value = str(labels.get(key) or "").strip()
        if service_text and value and service_text in value:
            score += 3
            break
    return score


def _score_alert(alert: Alert, *, node: str, service: str, namespace: str) -> int:
    score = _node_match_score(node, alert.labels)
    score += _service_match_score(service, namespace, alert.labels)
    if alert.status.value == "firing":
        score += 1
    return score


async def collect_alert_context(
    *,
    prometheus_url: str,
    alert_name: str,
    lookback: str,
    node: str,
    service: str,
    namespace: str,
) -> tuple[Alert, list[Alert], list[Alert]]:
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
        selected = max(current, key=lambda item: _score_alert(item, node=node, service=service, namespace=namespace))
        return selected, current, history
    if history:
        selected = max(history, key=lambda item: _score_alert(item, node=node, service=service, namespace=namespace))
        return selected, [], history
    raise SystemExit(f"No current or historical alerts found for {alert_name}")


async def query_latency_value_ms(*, prometheus_url: str, latency_promql: str) -> float | None:
    channel = PrometheusChannel(base_url=prometheus_url)
    try:
        value_seconds = await channel.query_instant(latency_promql)
        if math.isfinite(value_seconds):
            return float(value_seconds) * 1000.0
        end = datetime.now(UTC)
        start = end - timedelta(minutes=3)
        series = await channel.query_range(latency_promql, start, end, step="15s")
    finally:
        await channel.close()
    finite_values = [value for _, value in series if math.isfinite(value)]
    if not finite_values:
        return None
    return float(finite_values[-1]) * 1000.0


def build_query(
    *,
    alert: Alert,
    demo: dict[str, Any],
    node: str,
    namespace: str,
    service: str,
    latency_promql: str,
    latency_threshold_ms: int,
    history_count: int,
) -> str:
    expected_process_prefixes = _expected_gpu_process_prefixes(service, demo)
    available_tools = demo.get("allowed_tools") or []
    investigation_steps: list[str] = []
    if "prometheus.query_instant" in available_tools:
        investigation_steps.append(
            f"Validate the triggering latency signal with prometheus.query_instant using: {latency_promql}"
        )
    if "gpu.get_metrics" in available_tools:
        investigation_steps.append(f"Inspect GPU utilization and memory on node {node} with gpu.get_metrics")
    if "gpu.get_processes" in available_tools:
        investigation_steps.append(f"Inspect GPU-bound processes on node {node} with gpu.get_processes")
    if "k8s.list_pods" in available_tools:
        investigation_steps.append(f"Inspect workloads in namespace {namespace} with k8s.list_pods")
    if "network.get_rdma_stats" in available_tools:
        investigation_steps.append(f"Inspect RDMA and link health on node {node} with network.get_rdma_stats")

    return build_alert_diagnosis_prompt(
        alert_payload=normalize_alert_payload(alert),
        available_tool_names=available_tools,
        diagnosis_goal=(
            "Identify the most likely root cause of the alert, evaluate plausible alternatives, "
            "and explain which hypotheses are confirmed, eliminated, or still uncertain based on evidence."
        ),
        investigation_steps=investigation_steps,
        context_hints={
            "node": node,
            "namespace": namespace,
            "service": service,
            "inter-token latency threshold ms": latency_threshold_ms,
            "expected GPU worker process prefixes": expected_process_prefixes,
        },
        remediation_guidance=(
            "If the evidence supports a single conservative corrective action, propose it as proposal-only remediation. "
            "Prefer the smallest blast-radius action supported by the write-tool schema and the collected evidence. "
            "Do not claim any write action was executed."
        ),
        history_count=history_count,
    )


def _expected_gpu_process_prefixes(service: str, demo: dict[str, Any]) -> list[str]:
    configured = demo.get("expected_gpu_process_prefixes")
    prefixes: list[str] = []
    if isinstance(configured, list):
        prefixes.extend(str(item).strip() for item in configured if str(item).strip())
    service_text = str(service or "").strip()
    service_tokens = [service_text]
    if service_text:
        service_tokens.extend(part for part in service_text.split("-") if part)
    for candidate in ["VLLM::Worker", "vllm", *service_tokens]:
        text = str(candidate).strip()
        if text and text not in prefixes:
            prefixes.append(text)
    return prefixes


def render_text_output(payload: dict[str, Any]) -> str:
    lines = [
        "AIDC Auto-SRE VLLMInterTokenLatencyP95High Demo",
        f"Model: {payload.get('model') or '<unset>'}",
        f"Provider Base URL: {payload.get('provider_base_url') or '<default>'}",
        f"Alert Name: {payload.get('alert', {}).get('alert_name')}",
        f"Alert Source: {payload.get('alert_source')}",
        f"Node: {payload.get('node')}",
        f"Namespace: {payload.get('namespace')}",
        f"Service Hint: {payload.get('service')}",
        f"Prometheus URL: {payload.get('prometheus_url')}",
        f"Latency PromQL: {payload.get('latency_promql')}",
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
        "Remediation Execution:",
        json.dumps(payload.get("remediation_execution"), ensure_ascii=False, indent=2),
        "",
        "LLM Interactions:",
    ]
    llm_interactions = payload.get("llm_interactions") or []
    if not llm_interactions:
        lines.append("- none")
    else:
        for idx, item in enumerate(llm_interactions, start=1):
            lines.append(f"{idx}. step={item.get('step')} mode={item.get('mode')} tool_choice={item.get('tool_choice')}")
            bound_tool_names = item.get("bound_tool_names") or []
            lines.append(f"   bound_tool_names={json.dumps(bound_tool_names, ensure_ascii=False)}")
            lines.append("   prompt_messages=")
            lines.append(json.dumps(item.get("prompt_messages"), ensure_ascii=False, indent=2))
            lines.append("   response_message=")
            lines.append(json.dumps(item.get("response_message"), ensure_ascii=False, indent=2))
            raw_response_text = str(item.get("raw_response_text") or "").strip()
            if raw_response_text:
                lines.append("   raw_response_text=")
                lines.append(raw_response_text)
    lines.extend(
        [
            "",
        "Tool Runs:",
        ]
    )
    tool_runs = payload.get("tool_runs") or []
    if not tool_runs:
        lines.append("- none")
    else:
        for idx, run in enumerate(tool_runs, start=1):
            lines.append(f"{idx}. {run.get('tool')} success={run.get('success')} params={json.dumps(run.get('params'), ensure_ascii=False)}")
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


async def execute_remediation_plan(
    *,
    plan_payload: dict[str, Any] | None,
    context: ToolExecutionContext,
    wal_path: str,
    approval_user: str,
    prometheus_url: str,
    latency_promql: str,
    latency_threshold_ms: int,
    remediation_wait_seconds: int,
) -> dict[str, Any]:
    if not isinstance(plan_payload, dict):
        return {"executed": False, "reason": "no remediation plan generated"}

    plan = _with_promql_verification(
        RemediationPlan.model_validate(plan_payload),
        latency_promql=latency_promql,
        latency_threshold_ms=latency_threshold_ms,
        remediation_wait_seconds=remediation_wait_seconds,
    )
    wal_target = Path(wal_path).expanduser()
    wal_target.parent.mkdir(parents=True, exist_ok=True)

    registry = build_default_registry()
    engine = RemediationEngine(
        tool_registry=registry,
        approval_gate=ApprovalGate(default_policy="auto_approve"),
        wal=RollbackJournal(wal_target),
        prometheus=context.channels.get("prometheus"),
        execution_context=context,
    )
    session_id = f"remediation-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    try:
        result = await engine.execute(plan, session_id=session_id)
    except PlanValidationError as exc:
        return {
            "executed": False,
            "session_id": session_id,
            "approval_user": approval_user,
            "wal_path": str(wal_target),
            "error": "plan validation failed",
            "details": exc.errors,
            "plan": plan.model_dump(mode="json"),
        }

    post_latency_ms = await query_latency_value_ms(prometheus_url=prometheus_url, latency_promql=latency_promql)
    return {
        "executed": True,
        "session_id": session_id,
        "approval_user": approval_user,
        "wal_path": str(wal_target),
        "plan": plan.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "post_remediation_latency_ms": post_latency_ms,
    }


def _with_promql_verification(
    plan: RemediationPlan,
    *,
    latency_promql: str,
    latency_threshold_ms: int,
    remediation_wait_seconds: int,
) -> RemediationPlan:
    threshold_seconds = float(latency_threshold_ms) / 1000.0
    plan_data = plan.model_dump(mode="json")
    for step in plan_data.get("steps", []):
        tool_name = str(step.get("tool") or "").strip()
        if tool_name != "kill_process":
            continue
        step["verification"] = VerificationConfig(
            method="promql",
            query=latency_promql,
            condition=VerificationCondition(
                field="value",
                operator="<",
                value=threshold_seconds,
            ),
            wait_seconds=remediation_wait_seconds,
        ).model_dump(mode="json")
    return RemediationPlan.model_validate(plan_data)


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    agent_cfg = load_agent_config(args.config)
    raw_demo_cfg = load_demo_config(args.demo_config)
    demo = load_demo_settings(raw_demo_cfg, args.demo_key, args.demo_config)
    inventory = flatten_inventory(raw_demo_cfg)

    node = str(demo.get("node") or "").strip()
    namespace_hint = str(demo.get("namespace") or "").strip()
    service_hint = str(demo.get("service") or "").strip()
    alert_name = str(demo.get("alert_name") or "VLLMInterTokenLatencyP95High").strip()
    alert_severity = str(demo.get("alert_severity") or "critical").strip()
    lookback = str(demo.get("lookback") or "6h").strip()
    kubeconfig = str(demo.get("kubeconfig") or "~/.kube/config").strip()
    latency_promql = str(demo.get("latency_promql") or "").strip()
    latency_threshold_ms = int(demo.get("latency_threshold_ms") or 50)
    remediation_wait_seconds = int(demo.get("remediation_wait_seconds") or 60)
    if not node:
        raise SystemExit("demo.node is required")
    if node not in inventory:
        raise SystemExit(f"demo.node {node!r} not found in demo inventory")
    if not latency_promql:
        raise SystemExit("demo.latency_promql is required")

    allowed_tools = demo.get("allowed_tools") or [
        "prometheus.query_instant",
        "gpu.get_metrics",
        "gpu.get_processes",
        "k8s.list_pods",
        "network.get_rdma_stats",
    ]
    if not isinstance(allowed_tools, list) or not all(isinstance(item, str) for item in allowed_tools):
        raise SystemExit("demo.allowed_tools must be a list of tool names")

    prometheus_url = load_prometheus_url(raw_demo_cfg) or str(agent_cfg.global_.prometheus_url or "").strip()
    if not prometheus_url:
        raise SystemExit("Prometheus URL is required in demo config or agent config")

    latency_value_ms = await query_latency_value_ms(prometheus_url=prometheus_url, latency_promql=latency_promql)

    alert_source = "live"
    current_alerts: list[Alert] = []
    history_alerts: list[Alert] = []
    try:
        selected_alert, current_alerts, history_alerts = await collect_alert_context(
            prometheus_url=prometheus_url,
            alert_name=alert_name,
            lookback=lookback,
            node=node,
            service=service_hint,
            namespace=namespace_hint,
        )
    except SystemExit:
        if latency_value_ms is None:
            raise SystemExit(
                f"No current or historical alerts found for {alert_name}, and PromQL returned no usable value"
            )
        if latency_value_ms < float(latency_threshold_ms):
            raise SystemExit(
                f"No current or historical alerts found for {alert_name}, and PromQL value "
                f"{latency_value_ms:.2f}ms is below threshold {latency_threshold_ms}ms"
            )
        selected_alert = build_threshold_alert(
            alert_name=alert_name,
            severity=alert_severity,
            node=node,
            namespace=namespace_hint or "default",
            service=service_hint or "unknown-service",
            latency_value_ms=latency_value_ms,
            latency_threshold_ms=latency_threshold_ms,
            latency_promql=latency_promql,
        )
        alert_source = "promql_threshold"

    labels = selected_alert.labels
    namespace = str(labels.get("exported_namespace") or labels.get("namespace") or namespace_hint or "default").strip()
    service = str(labels.get("exported_container") or labels.get("service") or service_hint).strip()
    pod_name = str(labels.get("exported_pod") or labels.get("pod") or "").strip()

    query = build_query(
        alert=selected_alert,
        demo=demo,
        node=node,
        namespace=namespace,
        service=service,
        latency_promql=latency_promql,
        latency_threshold_ms=latency_threshold_ms,
        history_count=len(history_alerts),
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
                "service": service,
                "pod_name": pod_name,
                "promql": latency_promql,
                "latency_threshold_ms": latency_threshold_ms,
                "alert": normalize_alert_payload(selected_alert),
            },
            allowed_tool_names=allowed_tools,
            checkpoint_dir=None,
            step_timeout_sec=float(demo.get("step_timeout") or 60.0),
            total_timeout_sec=float(demo.get("total_timeout") or 180.0),
            max_steps=int(demo.get("max_steps") or 6),
        )

        remediation_execution = (
            await execute_remediation_plan(
                plan_payload=result.get("remediation_plan"),
                context=context,
                wal_path=str(args.wal_path),
                approval_user=str(args.approval_user),
                prometheus_url=prometheus_url,
                latency_promql=latency_promql,
                latency_threshold_ms=latency_threshold_ms,
                remediation_wait_seconds=remediation_wait_seconds,
            )
            if args.execute_remediation
            else {"executed": False, "reason": "execution disabled"}
        )
    finally:
        await close_context(context)

    payload: dict[str, Any] = {
        "provider_base_url": os.getenv("SRE_OPENAI_BASE_URL", "").strip() or None,
        "model": os.getenv("SRE_LLM_MODEL", "").strip() or None,
        "alert": normalize_alert_payload(selected_alert),
        "alert_source": alert_source,
        "current_alert_count": len(current_alerts),
        "history_alert_count": len(history_alerts),
        "node": node,
        "namespace": namespace,
        "service": service,
        "pod_name": pod_name,
        "prometheus_url": prometheus_url,
        "latency_promql": latency_promql,
        "latency_threshold_ms": latency_threshold_ms,
        "latency_value_ms": latency_value_ms,
        "status": result.get("status"),
        "summary": result.get("summary"),
        "llm_interactions": result.get("llm_interactions"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "remediation_execution": remediation_execution,
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
