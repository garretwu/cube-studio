#!/usr/bin/env python3
"""Run a live GPU missing-card alert demo focused on the gpu-drop skill."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.ssh import SSHChannel
from sre_agent.agent import run_diagnosis
from sre_agent.models.alert import Alert
from sre_agent.scripts.gpu_thermal_skill_demo import (
    apply_model_env,
    build_ssh_inventory,
    close_context,
    collect_alert_context,
    load_prometheus_url,
    normalize_alert_payload,
)
from sre_agent.tools import ToolExecutionContext, build_default_registry

DEFAULT_OUTPUT = Path("data/demo/gpu-card-missing-live-demo.json")
DEFAULT_AGENT_CONFIG = Path("sre_agent/conf/config.yaml")
DEFAULT_ALERT_NAME = "GPUCardMissing"
DEFAULT_ALLOWED_TOOLS = [
    "skills.list_skills",
    "skills.load_skill",
    "skills.read_skill_ref",
    "skills.run_skill",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live GPU missing-card demo that exercises the gpu-drop skill.",
    )
    parser.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG), help="SRE agent config path.")
    parser.add_argument("--alert-name", default=DEFAULT_ALERT_NAME, help="Live alert name to fetch.")
    parser.add_argument("--lookback", default="24h", help="Alert history lookback window.")
    parser.add_argument("--node", default="", help="Optional node hint override.")
    parser.add_argument("--expected-gpu-count", type=int, default=4, help="Expected healthy GPU count for the target node.")
    parser.add_argument("--model", default="", help="Override SRE_LLM_MODEL for this run.")
    parser.add_argument("--base-url", default="", help="Override SRE_OPENAI_BASE_URL for this run.")
    parser.add_argument("--ssh-user", default=os.getenv("GPU_DROP_DEMO_SSH_USER", ""), help="Optional SSH username override.")
    parser.add_argument("--ssh-password", default=os.getenv("GPU_DROP_DEMO_SSH_PASSWORD", ""), help="Optional SSH password override.")
    parser.add_argument("--ssh-port", type=int, default=int(os.getenv("GPU_DROP_DEMO_SSH_PORT", "22")), help="Optional SSH port override.")
    parser.add_argument("--ssh-timeout", type=int, default=int(os.getenv("GPU_DROP_DEMO_SSH_TIMEOUT", "30")), help="SSH command timeout in seconds.")
    parser.add_argument(
        "--live-inventory",
        default="",
        help="Optional live inventory YAML path. Defaults to ontology.discovery.live_inventory_path from config.",
    )
    parser.add_argument(
        "--allowed-tools",
        nargs="*",
        default=DEFAULT_ALLOWED_TOOLS,
        help="Tools exposed to the demo.",
    )
    parser.add_argument("--step-timeout", type=float, default=60.0)
    parser.add_argument("--total-timeout", type=float, default=240.0)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--checkpoint-dir", default="./data/checkpoints/gpu_card_missing_live_demo")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output file path.")
    parser.add_argument("--output-format", choices=["json", "txt"], default="json", help="Output file format.")
    return parser


def _resolve_node(alert: Alert, override: str) -> str:
    if override.strip():
        return override.strip()
    instance = str((alert.labels or {}).get("instance") or "").strip()
    return instance.split(":", 1)[0].strip() or "unknown-node"


def _build_script_env(node: str, inventory: dict[str, Any], expected_gpu_count: int) -> dict[str, str]:
    inventory_node = inventory.get(node)
    if inventory_node is None:
        # Fallback to first entry if the caller passed an alias that points to the same object.
        inventory_node = next(iter(inventory.values()))
    ssh_obj = getattr(inventory_node, "ssh", None)
    return {
        "SRE_GPU_DROP_NODE": str(getattr(inventory_node, "name", node) or node).strip(),
        "SRE_GPU_DROP_HOST": str(getattr(ssh_obj, "host", "") or "").strip(),
        "SRE_GPU_DROP_SSH_USER": str(getattr(ssh_obj, "user", "") or "").strip(),
        "SRE_GPU_DROP_SSH_PASSWORD": str(getattr(ssh_obj, "password", "") or "").strip(),
        "SRE_GPU_DROP_SSH_PORT": str(int(getattr(ssh_obj, "port", 22) or 22)),
        "SRE_GPU_DROP_SSH_TIMEOUT": str(int(getattr(ssh_obj, "timeout", 30) or 30)),
        "SRE_GPU_DROP_EXPECTED_GPU_COUNT": str(int(expected_gpu_count)),
        "SRE_GPU_DROP_AUTO_RECOVER": "1",
        "SRE_GPU_DROP_PAUSE_CLIENTS": "1",
    }



@contextmanager
def push_env(env_updates: dict[str, str]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in env_updates}
    try:
        for key, value in env_updates.items():
            os.environ[key] = value
        yield
    finally:
        for key, old in old_values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def build_query(*, alert: Alert, node: str, expected_gpu_count: int) -> str:
    payload = normalize_alert_payload(alert)
    return f"""
You are handling a live GPU missing-card alert.

Alert payload:
{json.dumps(payload, ensure_ascii=False, indent=2)}

Additional context:
- target node: {node}
- expected healthy GPU count: {expected_gpu_count}
- reported missing GPU index: {alert.labels.get('gpu_index') or alert.labels.get('gpu') or 'unknown'}
""".strip()


async def main_async(args: argparse.Namespace) -> int:
    apply_model_env(args)
    prometheus_url = load_prometheus_url(args.config)
    selected_alert, current_alerts, history_alerts = await collect_alert_context(
        prometheus_url=prometheus_url,
        alert_name=args.alert_name,
        lookback=args.lookback,
    )

    node_hint = _resolve_node(selected_alert, args.node)
    inventory, ssh_warnings = build_ssh_inventory(
        args=args,
        config_path=args.config,
        alert=selected_alert,
        node=node_hint,
    )
    if not inventory:
        raise SystemExit(f"Could not resolve SSH inventory for alert node {node_hint}: {ssh_warnings}")

    resolved_node = node_hint if node_hint in inventory else next(iter(inventory.keys()))
    context = ToolExecutionContext(
        channels={"ssh": SSHChannel(inventory=inventory, dry_run=False, command_timeout=args.ssh_timeout)},
        metadata={
            "aidc_id": "lab-aidc",
            "context_warnings": ssh_warnings,
        },
    )
    registry = build_default_registry()
    query = build_query(alert=selected_alert, node=resolved_node, expected_gpu_count=args.expected_gpu_count)
    script_env = _build_script_env(resolved_node, inventory, args.expected_gpu_count)

    try:
        with push_env(script_env):
            result = await run_diagnosis(
                query=query,
                context=context,
                variables={
                    "aidc_id": "lab-aidc",
                    "alert_name": args.alert_name,
                    "node": resolved_node,
                    "expected_gpu_count": args.expected_gpu_count,
                    "alert": normalize_alert_payload(selected_alert),
                },
                allowed_tool_names=args.allowed_tools,
                tool_registry=registry,
                checkpoint_dir=args.checkpoint_dir or None,
                step_timeout_sec=args.step_timeout,
                total_timeout_sec=args.total_timeout,
                max_steps=args.max_steps,
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
        "node": resolved_node,
        "expected_gpu_count": args.expected_gpu_count,
        "skill_env": {key: ("***" if "PASSWORD" in key else value) for key, value in script_env.items()},
        "status": result.get("status"),
        "summary": result.get("summary"),
        "tool_runs": result.get("tool_runs"),
        "diagnosis_result": result.get("diagnosis_result"),
        "remediation_plan": result.get("remediation_plan"),
        "session_id": result.get("session_id"),
        "llm_interactions": result.get("llm_interactions"),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.output_format == "json":
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "diagnosed" else 1


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
