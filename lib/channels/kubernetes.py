"""
Kubernetes channel.
"""
from __future__ import annotations

import asyncio
from typing import Any

from lib.channels.base import BaseChannel
from fault_injector.config.schema import ChannelResult
from fault_injector.safety.guard import SafetyViolationError


class K8sChannel(BaseChannel):
    """Kubernetes API operations."""

    FORBIDDEN_OPERATIONS = {
        ("delete", "Namespace"),
        ("delete", "CustomResourceDefinition"),
    }

    LABEL_SELECTORS = {
        "backend": "app=kubeflow-dashboard",
        "worker": "app=kubeflow-dashboard-worker",
        "beat": "app=kubeflow-dashboard-schedule",
        "inference": "app={service_name}",
        "training_operator": "control-plane=kubeflow-training-operator",
        "istio_ingress": "app=istio-ingress",
    }

    def __init__(self, kubeconfig: str = "~/.kube/config", dry_run: bool = False, wal: Any = None, guard: Any = None):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.kubeconfig = kubeconfig
        self._core_v1 = None
        self._apps_v1 = None

    def _ensure_client(self) -> None:
        if self._core_v1 is not None:
            return
        from kubernetes import client, config

        config.load_kube_config(config_file=self.kubeconfig)
        api_client = client.ApiClient()
        self._core_v1 = client.CoreV1Api(api_client)
        self._apps_v1 = client.AppsV1Api(api_client)

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        resource_type = params.get("resource_type", "")
        if (action, resource_type) in self.FORBIDDEN_OPERATIONS:
            raise SafetyViolationError(f"Forbidden K8s operation: {action} {resource_type}")

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "get_pods":
            return await self._get_pods(params["namespace"], params.get("label_selector"))
        if action == "delete_pod":
            return await self._delete_pod(params["pod_name"], params["namespace"])
        if action == "scale_deployment":
            return await self._scale_deployment(params["name"], params["namespace"], int(params["replicas"]))
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    async def _get_pods(self, namespace: str, label_selector: str | None = None) -> ChannelResult:
        try:
            self._ensure_client()
            pods = await asyncio.to_thread(
                self._core_v1.list_namespaced_pod,
                namespace,
                label_selector=label_selector,
            )
            return ChannelResult(success=True, output="\n".join(p.metadata.name for p in pods.items))
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
