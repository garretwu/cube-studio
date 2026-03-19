"""Purpose: kubectl get/describe/logs/top.

Primary tools: list_pods, describe_pod, read_pod_logs, count_pending_pods,
count_oomkilled_pods.
Channels used: k8s, log.
"""

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


def _optional_label_selector(params: dict[str, Any]) -> str | None:
    raw_selector = params.get("label_selector")
    label_selector = str(raw_selector).strip() if raw_selector is not None else None
    if label_selector == "":
        return None
    return label_selector


def _is_unknown_action_error(error: str) -> bool:
    return "unknown action" in error.casefold()


def _unwrap_execute_result(value: Any, *, action: str) -> Any:
    success = getattr(value, "success", None)
    if success is not None:
        if not bool(success):
            error_text = str(getattr(value, "error", "") or "").strip()
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


def _normalize_pod_entry(item: Any, namespace: str) -> dict[str, Any] | None:
    if isinstance(item, dict):
        normalized = dict(item)
        normalized.setdefault("namespace", namespace)
        return normalized
    name = str(item).strip()
    if not name:
        return None
    return {
        "namespace": namespace,
        "name": name,
        "status": {"phase": "Unknown"},
    }


def _normalize_pod_collection(value: Any, namespace: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        for key in ("items", "pods", "data"):
            maybe_items = value.get(key)
            if isinstance(maybe_items, list):
                return _normalize_pod_collection(maybe_items, namespace)
        output = value.get("output")
        if isinstance(output, str):
            return _normalize_pod_collection(output, namespace)
        return []
    if isinstance(value, str):
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        return [
            {
                "namespace": namespace,
                "name": line,
                "status": {"phase": "Unknown"},
            }
            for line in lines
        ]
    if isinstance(value, list):
        pods: list[dict[str, Any]] = []
        for item in value:
            normalized = _normalize_pod_entry(item, namespace)
            if normalized is not None:
                pods.append(normalized)
        return pods
    return []


def _pod_name(pod: dict[str, Any]) -> str:
    value = pod.get("name")
    if value:
        return str(value)
    metadata = pod.get("metadata")
    if isinstance(metadata, dict):
        return str(metadata.get("name", ""))
    return ""


def _pod_phase_or_none(pod: dict[str, Any]) -> str | None:
    status = pod.get("status")
    if not isinstance(status, dict):
        return None
    value = str(status.get("phase", "")).strip()
    if not value or value.casefold() == "unknown":
        return None
    return value


def _pod_container_statuses_or_none(pod: dict[str, Any]) -> list[Any] | None:
    status = pod.get("status")
    if not isinstance(status, dict):
        return None
    if "containerStatuses" not in status:
        return None
    container_statuses = status.get("containerStatuses")
    if container_statuses is None:
        return []
    if not isinstance(container_statuses, list):
        return None
    return container_statuses


async def _list_pods_best_effort(channel: Any, *, namespace: str, label_selector: str | None) -> list[dict[str, Any]]:
    direct_error: Exception | None = None
    if hasattr(channel, "list_pods"):
        try:
            pods = await channel.list_pods(namespace, label_selector=label_selector)
            return _normalize_pod_collection(pods, namespace)
        except Exception as exc:  # noqa: BLE001
            direct_error = exc

    execute_errors: list[str] = []
    if hasattr(channel, "execute"):
        for action in ("list_pods", "get_pods"):
            payload: dict[str, Any] = {"namespace": namespace}
            if label_selector is not None:
                payload["label_selector"] = label_selector
            try:
                value = await channel.execute(action, payload)
                unwrapped = _unwrap_execute_result(value, action=action)
                return _normalize_pod_collection(unwrapped, namespace)
            except ToolValidationError as exc:
                message = str(exc)
                if _is_unknown_action_error(message):
                    continue
                execute_errors.append(message)
                break

    if execute_errors:
        raise ToolValidationError(execute_errors[-1])
    if direct_error is not None:
        raise ToolValidationError(str(direct_error))
    if hasattr(channel, "execute"):
        raise ToolValidationError("k8s channel does not support list_pods/get_pods actions")
    raise ToolValidationError("k8s channel does not support pod listing")


async def list_pods(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    label_selector = _optional_label_selector(params)
    return await _list_pods_best_effort(channel, namespace=namespace, label_selector=label_selector)


async def describe_pod(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    pod_name = _require_str(params, "pod_name")

    if hasattr(channel, "get_pod_status"):
        try:
            status = await channel.get_pod_status(namespace, pod_name)
            return {"namespace": namespace, "pod_name": pod_name, "status": status}
        except Exception:  # noqa: BLE001
            pass

    pods = await _list_pods_best_effort(channel, namespace=namespace, label_selector=None)
    for pod in pods:
        if _pod_name(pod) == pod_name:
            status = _pod_phase_or_none(pod) or "Unknown"
            return {"namespace": namespace, "pod_name": pod_name, "status": status}
    raise ToolValidationError(f"pod {pod_name!r} not found in namespace {namespace!r}")


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
    label_selector = _optional_label_selector(params)
    if hasattr(channel, "count_pending_pods"):
        try:
            return await channel.count_pending_pods(namespace, label_selector=label_selector)
        except Exception:  # noqa: BLE001
            pass
    pods = await _list_pods_best_effort(channel, namespace=namespace, label_selector=label_selector)
    count = 0
    for pod in pods:
        phase = _pod_phase_or_none(pod)
        if phase is None:
            raise ToolValidationError(
                "k8s channel capability gap: pod phase data is unavailable for pending pod count",
            )
        if phase == "Pending":
            count += 1
    return count


async def count_oomkilled_pods(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    channel = _require_channel(context, "k8s")
    namespace = _require_str(params, "namespace")
    label_selector = _optional_label_selector(params)
    if hasattr(channel, "count_oomkilled_pods"):
        try:
            return await channel.count_oomkilled_pods(namespace, label_selector=label_selector)
        except Exception:  # noqa: BLE001
            pass

    pods = await _list_pods_best_effort(channel, namespace=namespace, label_selector=label_selector)
    count = 0
    for pod in pods:
        container_statuses = _pod_container_statuses_or_none(pod)
        if container_statuses is None:
            raise ToolValidationError(
                "k8s channel capability gap: container state is unavailable for OOMKilled count",
            )
        for cs in container_statuses:
            if not isinstance(cs, dict):
                continue
            state = cs.get("state", {})
            last_state = cs.get("lastState", {})
            if isinstance(state, dict) and state.get("terminated", {}).get("reason") == "OOMKilled":
                count += 1
                break
            if isinstance(last_state, dict) and last_state.get("terminated", {}).get("reason") == "OOMKilled":
                count += 1
                break
    return count
