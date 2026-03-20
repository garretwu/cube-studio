"""Purpose: kubectl apply/delete/scale/cordon/drain.

Primary tools: apply_manifest, delete_pod, scale_deployment, cordon_node,
drain_node.
Channels used: k8s.
Safety: write operations are expected to run with ToolRegistry approval.
"""

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


def _is_unknown_action_error(error: str) -> bool:
    return "unknown action" in error.casefold()


def _extract_result(value: Any, *, action: str, unsupported_message: str | None = None) -> Any:
    success = getattr(value, "success", None)
    if success is not None:
        if not bool(success):
            error_text = str(getattr(value, "error", "") or "").strip()
            if unsupported_message and _is_unknown_action_error(error_text):
                raise ToolValidationError(unsupported_message)
            if error_text:
                raise ToolValidationError(error_text)
            raise ToolValidationError(f"k8s channel action failed: {action}")
        data = getattr(value, "data", None)
        if data is not None:
            return data
        output = getattr(value, "output", None)
        error = getattr(value, "error", None)
        if output is None and error is None:
            return value
        return {"output": output or "", "error": error or ""}

    return value


async def apply_manifest(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    manifest = params.get("manifest")
    if manifest is None:
        raise ToolValidationError("parameter 'manifest' is required")

    if hasattr(k8s, "execute"):
        result = await k8s.execute("apply_manifest", {"manifest": manifest, "namespace": params.get("namespace", "default")})
        return _extract_result(
            result,
            action="apply_manifest",
            unsupported_message="k8s channel does not support apply_manifest action",
        )
    raise ToolValidationError("k8s channel does not support apply_manifest action")


async def delete_pod(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    namespace = _require_str(params, "namespace")
    label_selector_raw = params.get("label_selector")
    pod_name_raw = params.get("pod_name")

    label_selector = str(label_selector_raw).strip() if label_selector_raw is not None else ""
    pod_name = str(pod_name_raw).strip() if pod_name_raw is not None else ""

    direct_error: Exception | None = None
    if label_selector and hasattr(k8s, "delete_pod"):
        try:
            value = await k8s.delete_pod(label_selector=label_selector, namespace=namespace)
            return _extract_result(value, action="delete_pod")
        except Exception as exc:  # noqa: BLE001
            direct_error = exc
    if pod_name and hasattr(k8s, "execute"):
        result = await k8s.execute("delete_pod", {"pod_name": pod_name, "namespace": namespace})
        return _extract_result(result, action="delete_pod")
    if direct_error is not None:
        raise ToolValidationError(str(direct_error))
    raise ToolValidationError("delete_pod needs label_selector (mock path) or pod_name + execute() (real path)")


async def scale_deployment(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    namespace = _require_str(params, "namespace")
    name = _require_str(params, "name")
    replicas = int(params.get("replicas", 1))
    direct_error: Exception | None = None
    if hasattr(k8s, "scale_deployment"):
        try:
            value = await k8s.scale_deployment(name=name, namespace=namespace, replicas=replicas)
            return _extract_result(value, action="scale_deployment")
        except Exception as exc:  # noqa: BLE001
            direct_error = exc
    if hasattr(k8s, "execute"):
        result = await k8s.execute("scale_deployment", {"name": name, "namespace": namespace, "replicas": replicas})
        return _extract_result(result, action="scale_deployment")
    if direct_error is not None:
        raise ToolValidationError(str(direct_error))
    raise ToolValidationError("k8s channel does not support scale_deployment")


async def cordon_node(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    node = _require_str(params, "node")
    if hasattr(k8s, "execute"):
        result = await k8s.execute("cordon_node", {"node": node})
        return _extract_result(
            result,
            action="cordon_node",
            unsupported_message="k8s channel does not support cordon_node action",
        )
    raise ToolValidationError("k8s channel does not support cordon_node action")


async def drain_node(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    k8s = _require_k8s(context)
    node = _require_str(params, "node")
    force = bool(params.get("force", False))
    if hasattr(k8s, "execute"):
        result = await k8s.execute("drain_node", {"node": node, "force": force})
        return _extract_result(
            result,
            action="drain_node",
            unsupported_message="k8s channel does not support drain_node action",
        )
    raise ToolValidationError("k8s channel does not support drain_node action")

