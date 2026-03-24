#!/usr/bin/env python3
"""Run a real read-only Agent demo against live K8s and Prometheus.

This script is intentionally scoped to a safe demo path:
- read-only tools only
- real K8s + Prometheus channels
- provider-compatible OpenAI-style LLM endpoint configuration
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from sre_agent.agent import run_diagnosis
from sre_agent.config import load_config
from sre_agent.tools import ToolExecutionContext


DEFAULT_CONFIG = Path("config.yaml")
DEFAULT_FAULT_CONFIG = Path("fault_injector/fault-injector-test.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the live read-only agent demo on real K8s + Prometheus.",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="SRE agent config path. Used for defaults such as aidc id.",
    )
    parser.add_argument(
        "--fault-config",
        default=str(DEFAULT_FAULT_CONFIG),
        help="Fault injector config path. Used to resolve Prometheus URL.",
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Kubernetes namespace to diagnose.",
    )
    parser.add_argument(
        "--promql",
        default="up",
        help="PromQL used as a default read-only signal.",
    )
    parser.add_argument(
        "--query",
        default="Read-only diagnose the health of namespace {namespace}. Use Prometheus and Kubernetes evidence only, then summarize clearly.",
        help="Diagnosis query template. '{namespace}' and '{aidc_id}' are supported.",
    )
    parser.add_argument(
        "--kubeconfig",
        default="~/.kube/config",
        help="Path to kubeconfig for the live Kubernetes channel.",
    )
    parser.add_argument(
        "--prometheus-url",
        default="",
        help="Override Prometheus URL. If empty, load from config files.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Override SRE_LLM_MODEL for this run.",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Override SRE_OPENAI_BASE_URL for this run.",
    )
    parser.add_argument(
        "--allowed-tools",
        nargs="*",
        default=["prometheus.query_instant", "k8s.list_pods"],
        help="Read-only tools allowed in this demo.",
    )
    parser.add_argument("--step-timeout", type=float, default=45.0)
    parser.add_argument("--total-timeout", type=float, default=120.0)
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument(
        "--checkpoint-dir",
        default="",
        help="Optional checkpoint dir. Empty disables local checkpoints.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output file path. Supports json or txt via --output-format.",
    )
    parser.add_argument(
        "--output-format",
        choices=["json", "txt"],
        default="json",
        help="Output file format when --output is provided.",
    )
    return parser


def resolve_prometheus_url(config_path: str, fault_config_path: str, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()

    try:
        cfg = load_config(config_path)
        from_global = str(cfg.global_.prometheus_url or "").strip()
        if from_global:
            return from_global
    except Exception:
        pass

    fault_path = Path(fault_config_path)
    if fault_path.exists():
        raw = yaml.safe_load(fault_path.read_text(encoding="utf-8")) or {}
        monitor = raw.get("monitor") or {}
        value = str(monitor.get("prometheus_url") or "").strip()
        if value:
            return value

    return ""


def apply_model_env(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["SRE_LLM_MODEL"] = args.model
    if args.base_url:
        os.environ["SRE_OPENAI_BASE_URL"] = args.base_url


def render_text_output(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "AIDC Auto-SRE Live Agent Demo",
        f"Model: {payload.get('model') or '<unset>'}",
        f"Provider Base URL: {payload.get('provider_base_url') or '<default>'}",
        f"Namespace: {payload.get('namespace')}",
        f"Prometheus URL: {payload.get('prometheus_url')}",
        f"Status: {payload.get('status')}",
        f"Session ID: {payload.get('session_id')}",
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


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    cfg = load_config(args.config)
    prompt = args.query.format(namespace=args.namespace, aidc_id=cfg.global_.aidc_id)
    prometheus_url = resolve_prometheus_url(args.config, args.fault_config, args.prometheus_url)
    if not prometheus_url:
        raise SystemExit("Prometheus URL is required. Provide --prometheus-url or configure it in config.yaml / fault config.")

    context = ToolExecutionContext(
        channels={
            "k8s": K8sChannel(client=None, kubeconfig=args.kubeconfig),
            "prometheus": PrometheusChannel(base_url=prometheus_url),
        }
    )
    result = await run_diagnosis(
        query=prompt,
        context=context,
        variables={
            "namespace": args.namespace,
            "promql": args.promql,
            "aidc_id": cfg.global_.aidc_id,
        },
        allowed_tool_names=args.allowed_tools,
        checkpoint_dir=args.checkpoint_dir or None,
        total_timeout_sec=args.total_timeout,
        step_timeout_sec=args.step_timeout,
        max_steps=args.max_steps,
    )
    output: dict[str, Any] = {
        "provider_base_url": os.getenv("SRE_OPENAI_BASE_URL", "").strip() or None,
        "model": os.getenv("SRE_LLM_MODEL", "").strip() or None,
        "namespace": args.namespace,
        "prometheus_url": prometheus_url,
        "status": result.get("status"),
        "summary": result.get("summary"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "session_id": result.get("session_id"),
    }
    if args.output:
        maybe_write_output(args.output, args.output_format, output)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "diagnosed" else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
