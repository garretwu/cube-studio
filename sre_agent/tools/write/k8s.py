from __future__ import annotations

from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_k8s(context: ToolExecutionContext) -> Any:
    channel = context.channels.get("k8s")
    if channel is None:
        raise ToolValidationError("required channel is missing: k8s")
    return channel


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def apply_manifest(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    manifest = params.get("manifest")
    if manifest is None:
        raise ToolValidationError("parameter 'manifest' is required")

    if hasattr(k8s, "execute"):
        return await k8s.execute("apply_manifest", {"manifest": manifest, "namespace": params.get("namespace", "default")})
    raise ToolValidationError("k8s channel does not support apply_manifest action")


async def delete_pod(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    namespace = _require_str(params, "namespace")
    label_selector_raw = params.get("label_selector")
    pod_name_raw = params.get("pod_name")

    label_selector = str(label_selector_raw).strip() if label_selector_raw is not None else ""
    pod_name = str(pod_name_raw).strip() if pod_name_raw is not None else ""

    if label_selector and hasattr(k8s, "delete_pod"):
        return await k8s.delete_pod(label_selector=label_selector, namespace=namespace)
    if pod_name and hasattr(k8s, "execute"):
        return await k8s.execute("delete_pod", {"pod_name": pod_name, "namespace": namespace})
    raise ToolValidationError("delete_pod needs label_selector (mock path) or pod_name + execute() (real path)")


async def scale_deployment(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    namespace = _require_str(params, "namespace")
    name = _require_str(params, "name")
    replicas = int(params.get("replicas", 1))
    if hasattr(k8s, "scale_deployment"):
        return await k8s.scale_deployment(name=name, namespace=namespace, replicas=replicas)
    if hasattr(k8s, "execute"):
        return await k8s.execute("scale_deployment", {"name": name, "namespace": namespace, "replicas": replicas})
    raise ToolValidationError("k8s channel does not support scale_deployment")


async def cordon_node(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    node = _require_str(params, "node")
    if hasattr(k8s, "execute"):
        return await k8s.execute("cordon_node", {"node": node})
    raise ToolValidationError("k8s channel does not support cordon_node action")


async def drain_node(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    node = _require_str(params, "node")
    force = bool(params.get("force", False))
    if hasattr(k8s, "execute"):
        return await k8s.execute("drain_node", {"node": node, "force": force})
    raise ToolValidationError("k8s channel does not support drain_node action")

