from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_channel(context: ToolExecutionContext, name: str) -> Any:
    channel = context.channels.get(name)
    if channel is None:
        raise ToolValidationError(f"required channel is missing: {name}")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def list_pods(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    raw_selector = params.get("label_selector")
    label_selector = str(raw_selector).strip() if raw_selector is not None else None
    if label_selector == "":
        label_selector = None
    return await channel.list_pods(namespace, label_selector=label_selector)


async def describe_pod(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    pod_name = _require_str(params, "pod_name")
    status = await channel.get_pod_status(namespace, pod_name)
    return {"namespace": namespace, "pod_name": pod_name, "status": status}


async def read_pod_logs(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    log_channel = _require_channel(context, "log")
    namespace = _require_str(params, "namespace")
    pod_name = _require_str(params, "pod_name")
    tail = int(params.get("tail", 200))
    since_raw = params.get("since")
    since = str(since_raw).strip() if since_raw is not None else None
    if since == "":
        since = None
    return await log_channel.read_pod_logs(pod_name, namespace, tail=tail, since=since)


async def count_pending_pods(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    raw_selector = params.get("label_selector")
    label_selector = str(raw_selector).strip() if raw_selector is not None else None
    if label_selector == "":
        label_selector = None
    if hasattr(channel, "count_pending_pods"):
        return await channel.count_pending_pods(namespace, label_selector=label_selector)
    pods = await channel.list_pods(namespace, label_selector=label_selector)
    return sum(1 for pod in pods if str(pod.get("status", {}).get("phase", "")) == "Pending")


async def count_oomkilled_pods(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    raw_selector = params.get("label_selector")
    label_selector = str(raw_selector).strip() if raw_selector is not None else None
    if label_selector == "":
        label_selector = None
    if hasattr(channel, "count_oomkilled_pods"):
        return await channel.count_oomkilled_pods(namespace, label_selector=label_selector)
    return 0
