#!/usr/bin/env python3
"""Run a narrow, operator-driven remediation scenario against a real cluster.

This script is intentionally small and opinionated:
- read-only discovery first (`list-pods`)
- explicit dry-run by default for write scenarios
- real execution only with `--execute --yes`

It wires the existing Agent C remediation stack together:
ToolRegistry -> ApprovalGate -> WAL -> RemediationEngine -> K8s/Prometheus channels
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel
from sre_agent.models.remediation import (
    RemediationPlan,
    RemediationStep,
    VerificationCondition,
    VerificationConfig,
)
from sre_agent.remediation import ApprovalGate, PlanValidationError, RemediationEngine, RollbackJournal
from sre_agent.tools import ToolExecutionContext, build_default_registry


DEFAULT_FAULT_CONFIG = Path("fault_injector/fault-injector-test.yaml")
DEFAULT_WAL_PATH = Path("data/wal/live-remediation.jsonl")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real remediation playbook using the Agent C remediation engine.",
    )
    parser.add_argument(
        "--fault-config",
        default=str(DEFAULT_FAULT_CONFIG),
        help="Fault injector config used to resolve the Prometheus URL.",
    )
    parser.add_argument(
        "--prometheus-url",
        default="",
        help="Override Prometheus URL. If empty, read from --fault-config.",
    )
    parser.add_argument(
        "--kubeconfig",
        default="~/.kube/config",
        help="Path to kubeconfig for the live Kubernetes channel.",
    )
    parser.add_argument(
        "--wal-path",
        default=str(DEFAULT_WAL_PATH),
        help="Write-ahead log path for remediation recovery records.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_pods = subparsers.add_parser("list-pods", help="Read-only: list pods in a namespace.")
    list_pods.add_argument("--namespace", default="default")
    list_pods.add_argument("--label-selector", default="")

    list_deployments = subparsers.add_parser(
        "list-deployments",
        help="Read-only: list deployments in a namespace.",
    )
    list_deployments.add_argument("--namespace", default="default")

    delete_pod = subparsers.add_parser(
        "delete-pod",
        help="Delete a single pod through the remediation engine.",
    )
    _add_common_write_args(delete_pod)
    delete_pod.add_argument("--namespace", required=True)
    delete_pod.add_argument("--pod-name", required=True)
    delete_pod.add_argument(
        "--verify-wait-seconds",
        type=int,
        default=15,
        help="Wait-based verification window when PromQL verification is not used.",
    )
    _add_promql_verification_args(delete_pod)

    scale_deploy = subparsers.add_parser(
        "scale-deployment",
        help="Scale a deployment through the remediation engine with rollback support.",
    )
    _add_common_write_args(scale_deploy)
    scale_deploy.add_argument("--namespace", required=True)
    scale_deploy.add_argument("--name", required=True, help="Deployment name.")
    scale_deploy.add_argument("--replicas", type=int, required=True, help="Target replicas.")
    scale_deploy.add_argument(
        "--rollback-replicas",
        type=int,
        default=None,
        help="Rollback replicas. If omitted, read the current replica count from the cluster.",
    )
    scale_deploy.add_argument(
        "--verify-wait-seconds",
        type=int,
        default=15,
        help="Wait-based verification window when PromQL verification is not used.",
    )
    _add_promql_verification_args(scale_deploy)
    return parser


def _add_common_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the write action. If omitted, k8s writes stay in dry-run mode.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Acknowledge that the target and blast radius have been reviewed.",
    )
    parser.add_argument(
        "--session-id",
        default="live-remediation-session",
        help="Logical remediation session id for WAL and engine tracking.",
    )


def _add_promql_verification_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--verify-promql",
        default="",
        help="Optional PromQL verification query. If omitted, verification uses wait.",
    )
    parser.add_argument(
        "--verify-operator",
        choices=["<", "<=", ">", ">=", "==", "!="],
        default="<",
        help="PromQL verification operator.",
    )
    parser.add_argument(
        "--verify-value",
        type=float,
        default=None,
        help="PromQL verification threshold value.",
    )


def resolve_prometheus_url(args: argparse.Namespace) -> str:
    explicit = str(args.prometheus_url or "").strip()
    if explicit:
        return explicit

    config_path = Path(args.fault_config)
    if not config_path.exists():
        return ""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    monitor = raw.get("monitor") or {}
    return str(monitor.get("prometheus_url") or "").strip()


def ensure_execute_ack(args: argparse.Namespace) -> None:
    if getattr(args, "execute", False) and not getattr(args, "yes", False):
        raise SystemExit("--execute requires --yes so we have an explicit operator acknowledgement.")


def build_verification(args: argparse.Namespace) -> VerificationConfig:
    promql = str(getattr(args, "verify_promql", "") or "").strip()
    if promql:
        if args.verify_value is None:
            raise SystemExit("--verify-promql requires --verify-value.")
        return VerificationConfig(
            method="promql",
            query=promql,
            condition=VerificationCondition(
                field="value",
                operator=args.verify_operator,
                value=args.verify_value,
            ),
            wait_seconds=max(1, int(getattr(args, "verify_wait_seconds", 15))),
        )
    return VerificationConfig(
        method="wait",
        wait_seconds=max(1, int(getattr(args, "verify_wait_seconds", 15))),
    )


async def list_pods(namespace: str, label_selector: str, kubeconfig: str) -> dict[str, Any]:
    channel = K8sChannel(kubeconfig=kubeconfig)
    action = "get_pods"
    params = {"namespace": namespace}
    if label_selector:
        params["label_selector"] = label_selector
    result = await channel.execute(action, params)
    if not result.success:
        raise RuntimeError(result.error or "failed to list pods")
    output = str(result.output or "").strip()
    pods = [line.strip() for line in output.splitlines() if line.strip()]
    return {
        "namespace": namespace,
        "label_selector": label_selector,
        "count": len(pods),
        "pods": pods,
    }


async def list_deployments(namespace: str, kubeconfig: str) -> dict[str, Any]:
    from kubernetes import client, config

    config.load_kube_config(config_file=kubeconfig)
    apps = client.AppsV1Api(client.ApiClient())
    deployments = await asyncio.to_thread(apps.list_namespaced_deployment, namespace)
    items = [
        {
            "name": item.metadata.name,
            "replicas": int(item.spec.replicas or 0),
            "available_replicas": int(item.status.available_replicas or 0),
        }
        for item in deployments.items
    ]
    return {
        "namespace": namespace,
        "count": len(items),
        "deployments": items,
    }


def read_current_replicas(name: str, namespace: str, kubeconfig: str) -> int:
    from kubernetes import client, config

    config.load_kube_config(config_file=kubeconfig)
    apps = client.AppsV1Api(client.ApiClient())
    deployment = apps.read_namespaced_deployment(name=name, namespace=namespace)
    return int(deployment.spec.replicas or 0)


def build_delete_pod_plan(args: argparse.Namespace) -> RemediationPlan:
    verification = build_verification(args)
    return RemediationPlan(
        plan_id=f"delete-pod-{args.namespace}-{args.pod_name}",
        root_cause="manual live remediation",
        description=f"Restart pod {args.pod_name} in namespace {args.namespace}",
        estimated_impact="single pod restart",
        confidence=0.95,
        priority="P1",
        steps=[
            RemediationStep(
                step_id=1,
                description=f"Delete pod {args.pod_name} for controlled restart",
                tool="k8s.delete_pod",
                params={"namespace": args.namespace, "pod_name": args.pod_name},
                verification=verification,
            )
        ],
    )


def build_scale_plan(args: argparse.Namespace, rollback_replicas: int) -> RemediationPlan:
    verification = build_verification(args)
    return RemediationPlan(
        plan_id=f"scale-deployment-{args.namespace}-{args.name}",
        root_cause="manual live remediation",
        description=f"Scale deployment {args.name} in namespace {args.namespace}",
        estimated_impact="replica count adjustment",
        confidence=0.95,
        priority="P1",
        steps=[
            RemediationStep(
                step_id=1,
                description=f"Scale deployment {args.name} to {args.replicas}",
                tool="k8s.scale_deployment",
                params={"namespace": args.namespace, "name": args.name, "replicas": args.replicas},
                rollback_tool="k8s.scale_deployment",
                rollback_params={"namespace": args.namespace, "name": args.name, "replicas": rollback_replicas},
                verification=verification,
            )
        ],
    )


async def run_plan(args: argparse.Namespace, plan: RemediationPlan, prometheus_url: str) -> dict[str, Any]:
    wal_path = Path(args.wal_path)
    wal_path.parent.mkdir(parents=True, exist_ok=True)

    k8s_channel = K8sChannel(
        kubeconfig=args.kubeconfig,
        dry_run=not args.execute,
    )
    prom_channel = PrometheusChannel(base_url=prometheus_url) if prometheus_url else None
    registry = build_default_registry()
    context = ToolExecutionContext(
        channels={
            "k8s": k8s_channel,
            "prometheus": prom_channel,
        },
    )
    engine = RemediationEngine(
        tool_registry=registry,
        approval_gate=ApprovalGate(default_policy="auto_approve"),
        wal=RollbackJournal(wal_path),
        prometheus=prom_channel,
        execution_context=context,
    )

    try:
        result = await engine.execute(plan, session_id=args.session_id)
    except PlanValidationError as exc:
        return {
            "success": False,
            "error": "plan validation failed",
            "details": exc.errors,
            "plan": plan.model_dump(mode="json"),
        }

    return {
        "success": result.success,
        "mode": "execute" if args.execute else "dry_run",
        "session_id": args.session_id,
        "plan": plan.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "wal_path": str(wal_path),
        "prometheus_url": prometheus_url or None,
    }


async def main_async(args: argparse.Namespace) -> int:
    if args.command == "list-pods":
        payload = await list_pods(args.namespace, args.label_selector, args.kubeconfig)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if args.command == "list-deployments":
        payload = await list_deployments(args.namespace, args.kubeconfig)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    ensure_execute_ack(args)
    prometheus_url = resolve_prometheus_url(args)

    if args.command == "delete-pod":
        plan = build_delete_pod_plan(args)
    elif args.command == "scale-deployment":
        rollback_replicas = args.rollback_replicas
        if rollback_replicas is None:
            rollback_replicas = read_current_replicas(args.name, args.namespace, args.kubeconfig)
        plan = build_scale_plan(args, rollback_replicas)
    else:  # pragma: no cover - argparse guarantees command
        raise SystemExit(f"unsupported command: {args.command}")

    payload = await run_plan(args, plan, prometheus_url)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("success") else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
