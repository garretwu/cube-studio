#!/usr/bin/env python3
"""Run a live GPU thermal alert demo focused on Claude-style skill usage."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.alert import AlertChannel
from lib.tests._real_backends import PrometheusHttpBackend
from sre_agent.agent import run_diagnosis
from sre_agent.models.alert import Alert
from sre_agent.tools import ToolExecutionContext

DEFAULT_OUTPUT = Path("data/demo/gpu-temp-skill-alert-demo.json")
DEFAULT_AGENT_CONFIG = Path("sre_agent/conf/config.yaml")
DEFAULT_ALLOWED_TOOLS = [
    "skills.list_skills",
    "skills.load_skill",
    "skills.read_skill_ref",
    "skills.run_skill",
]
DEFAULT_ALERT_NAME = "GPUTemperatureHighWjLabCpt04"


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
    parser.add_argument(
        "--allowed-tools",
        nargs="*",
        default=DEFAULT_ALLOWED_TOOLS,
        help="Tools exposed to the demo.",
    )
    parser.add_argument("--step-timeout", type=float, default=60.0)
    parser.add_argument("--total-timeout", type=float, default=300.0)
    parser.add_argument("--max-steps", type=int, default=8)
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
    return parser


def apply_model_env(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["SRE_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["SRE_OPENAI_BASE_URL"] = args.base_url


def load_prometheus_url(config_path: str) -> str:
    raw = json.loads("{}")
    try:
        import yaml

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


def build_query(*, alert: Alert) -> str:
    payload = normalize_alert_payload(alert)
    return f"""
You are handling a live alert.

Alert payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Diagnose this alert. If a reusable skill is a strong fit, use the skills tools first. Then conclude with a structured diagnosis.
""".strip()


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


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    prometheus_url = load_prometheus_url(args.config)
    selected_alert, current_alerts, history_alerts = await collect_alert_context(
        prometheus_url=prometheus_url,
        alert_name=args.alert_name,
        lookback=args.lookback,
    )
    node = _extract_node(selected_alert, args.node)
    gpu = _extract_gpu(selected_alert, args.gpu)
    temperature_c = _extract_temperature_c(selected_alert)
    query = build_query(alert=selected_alert)
    result = await run_diagnosis(
        query=query,
        context=ToolExecutionContext(),
        variables=None,
        allowed_tool_names=args.allowed_tools,
        checkpoint_dir=args.checkpoint_dir or None,
        total_timeout_sec=args.total_timeout,
        step_timeout_sec=args.step_timeout,
        max_steps=args.max_steps,
    )
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
        "status": result.get("status"),
        "summary": result.get("summary"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "llm_interactions": result.get("llm_interactions"),
        "session_id": result.get("session_id"),
    }
    maybe_write_output(args.output, args.output_format, payload)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "diagnosed" else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
