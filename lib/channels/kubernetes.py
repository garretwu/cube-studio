"""Kubernetes API channel (unified for load-simulator and fault-injector)."""
from __future__ import annotations

import asyncio
from typing import Any

from .base import BaseChannel, ChannelResult, SafetyViolationError


class K8sChannel(BaseChannel):
    """Kubernetes API operations with safety guards and LABEL_SELECTORS."""

    # Forbidden operations (security guard)
    FORBIDDEN_OPERATIONS = {
        ("delete", "Namespace"),       # 禁止删除命名空间
        ("delete", "CRD"),             # 禁止删除 CRD (load_simulator)
        ("delete", "CustomResourceDefinition"),  # 禁止删除 CRD (fault_injector)
    }

    # Cube Studio 使用的标签选择器
    LABEL_SELECTORS = {
        "backend": "app=kubeflow-dashboard",
        "worker": "app=kubeflow-dashboard-worker",
        "beat": "app=kubeflow-dashboard-schedule",
        "inference": "app={service_name}",
        "training_operator": "control-plane=kubeflow-training-operator",
        "istio_ingress": "app=istio-ingress",
    }

    def __init__(
        self,
        client: Any = None,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
        kubeconfig: str = "~/.kube/config",
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.client = client
        self.kubeconfig = kubeconfig
        # Lazy-init K8s API clients (fault_injector path)
        self._core_v1: Any | None = None
        self._apps_v1: Any | None = None

    def _ensure_client(self) -> None:
        """Lazy-load kubernetes client (fault_injector path, real K8s API)."""
        if self._core_v1 is not None:
            return
        from kubernetes import client, config

        config.load_kube_config(config_file=self.kubeconfig)
        api_client = client.ApiClient()
        self._core_v1 = client.CoreV1Api(api_client)
        self._apps_v1 = client.AppsV1Api(api_client)

    def _is_forbidden(self, action: str, params: dict[str, Any]) -> bool:
        """Check if action is forbidden."""
        verb = params.get("verb", action)
        resource = params.get("resource", "")
        return (verb, resource) in self.FORBIDDEN_OPERATIONS

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        """Safety check for fault_injector guard path."""
        resource_type = params.get("resource_type", "")
        if (action, resource_type) in self.FORBIDDEN_OPERATIONS:
            raise SafetyViolationError(f"Forbidden K8s operation: {action} {resource_type}")

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        # load_simulator path (mock client)
        if self.client is not None:
            return await self._execute_with_mock_client(action, params)
        # fault_injector path (real K8s API)
        return await self._execute_with_real_client(action, params)

    # ── load_simulator execution path ──

    async def _execute_with_mock_client(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "list_pods":
            pods = await self.list_pods(params.get("namespace", "default"), params.get("label_selector"))
            return ChannelResult(success=True, data=pods)
        if action == "delete_pod":
            result = await self.delete_pod(
                params.get("label_selector"),
                params.get("namespace", "default"),
            )
            return ChannelResult(success=True, data=result)
        if action == "scale_deployment":
            result = await self.scale_deployment(
                params.get("name"),
                params.get("namespace", "default"),
                params.get("replicas"),
            )
            return ChannelResult(success=True, data=result)
        if action == "apply_manifest":
            result = await self.apply_manifest(
                params.get("manifest"),
                params.get("namespace", "default"),
            )
            return ChannelResult(success=True, data=result)
        if action == "cordon_node":
            result = await self.cordon_node(params.get("node"))
            return ChannelResult(success=True, data=result)
        if action == "drain_node":
            result = await self.drain_node(params.get("node"), bool(params.get("force", False)))
            return ChannelResult(success=True, data=result)
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        return self.client.list_pods(namespace=namespace, label_selector=label_selector)

    async def get_pod_status(self, namespace: str, pod_name: str) -> str:
        pod = self.client.get_pod(namespace=namespace, pod_name=pod_name)
        status = pod.get("status", {})
        phase = status.get("phase")
        return str(phase or "Unknown")

    async def count_pending_pods(self, namespace: str, label_selector: str | None = None) -> int:
        pods = await self.list_pods(namespace, label_selector)
        return sum(1 for p in pods if str(p.get("status", {}).get("phase", "")) == "Pending")

    async def count_oomkilled_pods(self, namespace: str, label_selector: str | None = None) -> int:
        """Count pods that have been OOMKilled (checking both current and last state)."""
        pods = await self.list_pods(namespace, label_selector)
        count = 0
        for pod in pods:
            container_statuses = pod.get("status", {}).get("containerStatuses", [])
            oom = False
            for cs in container_statuses:
                state = cs.get("state", {})
                last_state = cs.get("lastState", {})
                if state.get("terminated", {}).get("reason") == "OOMKilled":
                    oom = True
                    break
                if last_state.get("terminated", {}).get("reason") == "OOMKilled":
                    oom = True
                    break
            if oom:
                count += 1
        return count

    async def delete_pod(
        self,
        label_selector: str,
        namespace: str = "default",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Delete pods matching label selector (relies on K8s restart policy for recovery)."""
        is_dry_run = dry_run if dry_run is not None else self.dry_run

        if is_dry_run:
            return {"dry_run": True, "action": "delete_pod", "label_selector": label_selector, "namespace": namespace}

        # Record to WAL for recovery if available
        if self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                record(
                    action="delete_pod",
                    recovery_action="restart_pod",
                    recovery_params={"label_selector": label_selector, "namespace": namespace},
                )

        return self.client.delete_pods(namespace=namespace, label_selector=label_selector)

    async def scale_deployment(
        self,
        name: str,
        namespace: str,
        replicas: int,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Scale deployment to specified replicas (records original value for recovery)."""
        is_dry_run = dry_run if dry_run is not None else self.dry_run

        if is_dry_run:
            return {
                "dry_run": True,
                "action": "scale_deployment",
                "name": name,
                "namespace": namespace,
                "replicas": replicas,
            }

        # Get current replicas for WAL recovery record
        current = self.client.get_deployment(namespace=namespace, name=name)
        current_replicas = current.get("spec", {}).get("replicas", 0) if current else 0

        if self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                record(
                    action="scale_deployment",
                    recovery_action="scale_deployment",
                    recovery_params={"name": name, "namespace": namespace, "replicas": current_replicas},
                )

        return self.client.scale_deployment(namespace=namespace, name=name, replicas=replicas)

    async def apply_manifest(self, manifest: dict[str, Any], namespace: str = "default") -> dict[str, Any]:
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError("manifest must be a non-empty mapping")
        if hasattr(self.client, "apply_manifest"):
            return self.client.apply_manifest(manifest=manifest, namespace=namespace)

        metadata = manifest.get("metadata", {})
        kind = str(manifest.get("kind", "")).strip() or "Unknown"
        name = ""
        if isinstance(metadata, dict):
            name = str(metadata.get("name", "")).strip()
        return {
            "applied": True,
            "namespace": namespace,
            "kind": kind,
            "name": name,
            "mode": "mock-noop",
        }

    async def cordon_node(self, node: str) -> dict[str, Any]:
        node_name = str(node or "").strip()
        if not node_name:
            raise ValueError("node is required")
        if hasattr(self.client, "cordon_node"):
            return self.client.cordon_node(node=node_name)
        return {
            "node": node_name,
            "cordoned": True,
            "mode": "mock-noop",
        }

    async def drain_node(self, node: str, force: bool = False) -> dict[str, Any]:
        node_name = str(node or "").strip()
        if not node_name:
            raise ValueError("node is required")
        if hasattr(self.client, "drain_node"):
            return self.client.drain_node(node=node_name, force=force)
        return {
            "node": node_name,
            "force": force,
            "drained": True,
            "evicted_pods": 0,
            "skipped_pods": 0,
            "mode": "mock-noop",
        }

    async def delete_crd(self, crd_name: str) -> dict[str, Any]:
        """Delete CRD (dangerous operation — blocked by FORBIDDEN_OPERATIONS)."""
        if self._is_forbidden("delete", {"verb": "delete", "resource": "CRD"}):
            raise SafetyViolationError("delete CRD is forbidden")

        if self.dry_run:
            return {"dry_run": True, "action": "delete_crd", "crd_name": crd_name}

        return self.client.delete_crd(crd_name=crd_name)

    # ── fault_injector execution path (real K8s API) ──

    async def _execute_with_real_client(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "list_pods":
            return await self._get_pods(params["namespace"], params.get("label_selector"))
        if action == "get_pods":
            return await self._get_pods(params["namespace"], params.get("label_selector"))
        if action == "delete_pod":
            return await self._delete_pod(params["pod_name"], params["namespace"])
        if action == "scale_deployment":
            return await self._scale_deployment(params["name"], params["namespace"], int(params["replicas"]))
        if action == "apply_manifest":
            return await self._apply_manifest(params["manifest"], params.get("namespace", "default"))
        if action == "cordon_node":
            return await self._cordon_node(params["node"])
        if action == "drain_node":
            return await self._drain_node(params["node"], bool(params.get("force", False)))
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    async def _get_pods(self, namespace: str, label_selector: str | None = None) -> ChannelResult:
        try:
            self._ensure_client()
            pods = await asyncio.to_thread(
                self._core_v1.list_namespaced_pod,
                namespace,
                label_selector=label_selector,
            )
            structured: list[dict[str, Any]] = []
            names: list[str] = []
            for pod in pods.items:
                pod_name = str(getattr(getattr(pod, "metadata", None), "name", "") or "")
                pod_ns = str(getattr(getattr(pod, "metadata", None), "namespace", "") or namespace)
                phase = str(getattr(getattr(pod, "status", None), "phase", "") or "Unknown")
                names.append(pod_name)

                container_statuses_raw = getattr(getattr(pod, "status", None), "container_statuses", None) or []
                container_statuses: list[dict[str, Any]] = []
                for cs in container_statuses_raw:
                    state = getattr(cs, "state", None)
                    last_state = getattr(cs, "last_state", None)
                    current_terminated = getattr(state, "terminated", None) if state is not None else None
                    last_terminated = getattr(last_state, "terminated", None) if last_state is not None else None
                    container_statuses.append(
                        {
                            "state": {
                                "terminated": (
                                    {"reason": str(getattr(current_terminated, "reason", "") or "")}
                                    if current_terminated is not None
                                    else {}
                                )
                            },
                            "lastState": {
                                "terminated": (
                                    {"reason": str(getattr(last_terminated, "reason", "") or "")}
                                    if last_terminated is not None
                                    else {}
                                )
                            },
                        }
                    )

                structured.append(
                    {
                        "name": pod_name,
                        "namespace": pod_ns,
                        "status": {
                            "phase": phase,
                            "containerStatuses": container_statuses,
                        },
                    }
                )

            return ChannelResult(
                success=True,
                data=structured,
                output="\n".join(name for name in names if name),
            )
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def _delete_pod(self, pod_name: str, namespace: str) -> ChannelResult:
        try:
            self._ensure_client()
            await asyncio.to_thread(self._core_v1.delete_namespaced_pod, pod_name, namespace)
            return ChannelResult(success=True)
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def _scale_deployment(self, name: str, namespace: str, replicas: int) -> ChannelResult:
        try:
            self._ensure_client()
            deployment = await asyncio.to_thread(self._apps_v1.read_namespaced_deployment, name, namespace)
            current_replicas = int(deployment.spec.replicas or 0)
            if self.wal:
                self.wal.record(
                    fault_id=f"scale_{namespace}_{name}",
                    channel=self.channel_name,
                    target=f"{namespace}/{name}",
                    inject_action="scale_deployment",
                    inject_params={"name": name, "namespace": namespace, "replicas": replicas},
                    recover_action="scale_deployment",
                    recover_params={"name": name, "namespace": namespace, "replicas": current_replicas},
                )
            await asyncio.to_thread(
                self._apps_v1.patch_namespaced_deployment_scale,
                name,
                namespace,
                {"spec": {"replicas": replicas}},
            )
            return ChannelResult(success=True)
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def _apply_manifest(self, manifest: dict[str, Any], namespace: str = "default") -> ChannelResult:
        if not isinstance(manifest, dict) or not manifest:
            return ChannelResult(success=False, error="manifest must be a non-empty mapping")

        try:
            self._ensure_client()
            metadata = manifest.get("metadata", {})
            name = ""
            if isinstance(metadata, dict):
                name = str(metadata.get("name", "")).strip()
            kind = str(manifest.get("kind", "")).strip()
            effective_namespace = namespace
            if isinstance(metadata, dict):
                declared_ns = str(metadata.get("namespace", "")).strip()
                if declared_ns:
                    effective_namespace = declared_ns

            # Fast-path common config objects for idempotent apply in repeated real tests.
            if kind == "ConfigMap" and name:
                from kubernetes.client.exceptions import ApiException

                try:
                    await asyncio.to_thread(
                        self._core_v1.create_namespaced_config_map,
                        effective_namespace,
                        manifest,
                    )
                    return ChannelResult(
                        success=True,
                        data={"applied": True, "kind": kind, "name": name, "namespace": effective_namespace, "operation": "create"},
                    )
                except ApiException as exc:
                    if int(getattr(exc, "status", 0) or 0) != 409:
                        raise
                    await asyncio.to_thread(
                        self._core_v1.patch_namespaced_config_map,
                        name,
                        effective_namespace,
                        manifest,
                    )
                    return ChannelResult(
                        success=True,
                        data={"applied": True, "kind": kind, "name": name, "namespace": effective_namespace, "operation": "patch"},
                    )

            from kubernetes.utils import create_from_dict

            created = await asyncio.to_thread(
                create_from_dict,
                self._core_v1.api_client,
                manifest,
                namespace=effective_namespace,
                verbose=False,
            )
            resource_count = len(created) if isinstance(created, list) else 1
            return ChannelResult(
                success=True,
                data={
                    "applied": True,
                    "kind": kind or "Unknown",
                    "name": name,
                    "namespace": effective_namespace,
                    "resources": resource_count,
                },
            )
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def _cordon_node(self, node: str) -> ChannelResult:
        node_name = str(node or "").strip()
        if not node_name:
            return ChannelResult(success=False, error="node is required")
        try:
            self._ensure_client()
            await asyncio.to_thread(
                self._core_v1.patch_node,
                node_name,
                {"spec": {"unschedulable": True}},
            )
            return ChannelResult(success=True, data={"node": node_name, "cordoned": True})
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def _drain_node(self, node: str, force: bool = False) -> ChannelResult:
        node_name = str(node or "").strip()
        if not node_name:
            return ChannelResult(success=False, error="node is required")
        try:
            self._ensure_client()
            await asyncio.to_thread(
                self._core_v1.patch_node,
                node_name,
                {"spec": {"unschedulable": True}},
            )

            pods = await asyncio.to_thread(
                self._core_v1.list_pod_for_all_namespaces,
                field_selector=f"spec.nodeName={node_name}",
            )
            from kubernetes import client as k8s_client
            from kubernetes.client.exceptions import ApiException

            policy_api = k8s_client.PolicyV1Api(self._core_v1.api_client)
            evicted = 0
            skipped = 0
            errors: list[str] = []

            for pod in pods.items:
                pod_name = str(getattr(getattr(pod, "metadata", None), "name", "") or "")
                namespace = str(getattr(getattr(pod, "metadata", None), "namespace", "") or "")
                annotations = getattr(getattr(pod, "metadata", None), "annotations", {}) or {}
                owner_refs = getattr(getattr(pod, "metadata", None), "owner_references", None) or []

                if not pod_name or not namespace:
                    skipped += 1
                    continue
                if "kubernetes.io/config.mirror" in annotations:
                    skipped += 1
                    continue
                if any(str(getattr(ref, "kind", "")).strip() == "DaemonSet" for ref in owner_refs):
                    if not force:
                        skipped += 1
                        continue
                if namespace == "kube-system" and not force:
                    skipped += 1
                    continue

                eviction = k8s_client.V1Eviction(
                    metadata=k8s_client.V1ObjectMeta(name=pod_name, namespace=namespace),
                )
                try:
                    await asyncio.to_thread(
                        policy_api.create_namespaced_pod_eviction,
                        name=pod_name,
                        namespace=namespace,
                        body=eviction,
                    )
                    evicted += 1
                except ApiException as exc:
                    errors.append(f"{namespace}/{pod_name}: {exc.status} {exc.reason}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{namespace}/{pod_name}: {exc}")

            if errors:
                preview = "; ".join(errors[:3])
                if len(errors) > 3:
                    preview = f"{preview}; ... ({len(errors)} total errors)"
                return ChannelResult(
                    success=False,
                    error=f"drain node encountered eviction errors: {preview}",
                    data={"node": node_name, "evicted_pods": evicted, "skipped_pods": skipped},
                )

            return ChannelResult(
                success=True,
                data={"node": node_name, "drained": True, "evicted_pods": evicted, "skipped_pods": skipped},
            )
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))
