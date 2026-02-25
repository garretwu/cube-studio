"""Kubernetes API channel (read-focused for load-simulator)."""
from __future__ import annotations

from typing import Any

from load_simulator.channels.base import BaseChannel, ChannelResult, SafetyViolationError


class K8sChannel(BaseChannel):
    """Kubernetes API operations with safety guards and LABEL_SELECTORS."""

    # Forbidden operations (security guard)
    FORBIDDEN_OPERATIONS = [
        ("delete", "Namespace"),  #禁止删除命名空间
    ]

    # Cube Studio 使用的标签选择器
    LABEL_SELECTORS = {
        "backend": "app=kubeflow-dashboard",
        "worker": "app=kubeflow-dashboard-worker",
        "beat": "app=kubeflow-dashboard-schedule",
        "inference": "app={service_name}",
        "training_operator": "control-plane=kubeflow-training-operator",
        "istio_ingress": "app=istio-ingress",
    }

    def __init__(self, client: Any, dry_run: bool = False, wal: Any | None = None) -> None:
        super().__init__(dry_run=dry_run, wal=wal)
        self.client = client

    def _is_forbidden(self, action: str, params: dict[str, Any]) -> bool:
        """Check if action is forbidden."""
        verb = params.get("verb", action)
        resource = params.get("resource", "")
        for forbidden_verb, forbidden_resource in self.FORBIDDEN_OPERATIONS:
            if verb == forbidden_verb and resource == forbidden_resource:
                return True
        return False

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
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
        cluster: str = "default",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Delete pods matching label selector (relies on K8s restart policy for recovery)."""
        # Use instance dry_run if not explicitly specified
        is_dry_run = dry_run if dry_run is not None else self.dry_run

        # Check if forbidden
        if self._is_forbidden("delete", {"verb": "delete", "resource": "Pod"}):
            raise SafetyViolationError("delete Pod is forbidden")

        # Record to WAL for recovery if available (skip in dry_run)
        if not is_dry_run and self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                record(
                    action="delete_pod",
                    recovery_action="restart_pod",
                    recovery_params={"label_selector": label_selector, "namespace": namespace},
                )

        if is_dry_run:
            return {"dry_run": True, "action": "delete_pod", "label_selector": label_selector, "namespace": namespace}

        return self.client.delete_pods(namespace=namespace, label_selector=label_selector)

    async def scale_deployment(
        self,
        name: str,
        namespace: str,
        replicas: int,
        cluster: str = "default",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Scale deployment to specified replicas (records original value for recovery)."""
        # Use instance dry_run if not explicitly specified
        is_dry_run = dry_run if dry_run is not None else self.dry_run

        # Get current replicas for recovery
        current = self.client.get_deployment(namespace=namespace, name=name)
        current_replicas = current.get("spec", {}).get("replicas", 0) if current else 0

        # Record to WAL for recovery (skip in dry_run)
        if not is_dry_run and self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                record(
                    action="scale_deployment",
                    recovery_action="scale_deployment",
                    recovery_params={"name": name, "namespace": namespace, "replicas": current_replicas},
                )

        if is_dry_run:
            return {
                "dry_run": True,
                "action": "scale_deployment",
                "name": name,
                "namespace": namespace,
                "replicas": replicas,
            }

        return self.client.scale_deployment(namespace=namespace, name=name, replicas=replicas)

    async def delete_crd(self, crd_name: str, cluster: str = "default") -> dict[str, Any]:
        """Delete CRD (dangerous operation, requires safety confirmation)."""
        if self._is_forbidden("delete", {"verb": "delete", "resource": "CRD"}):
            raise SafetyViolationError("delete CRD is forbidden - requires explicit safety confirmation")

        if self.dry_run:
            return {"dry_run": True, "action": "delete_crd", "crd_name": crd_name}

        return self.client.delete_crd(crd_name=crd_name)
