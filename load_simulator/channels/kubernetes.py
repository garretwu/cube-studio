"""Kubernetes API channel (read-focused for load-simulator)."""
from __future__ import annotations

from typing import Any

from load_simulator.channels.base import BaseChannel, ChannelResult


class K8sChannel(BaseChannel):
    """Thin wrapper around a Kubernetes-like API client object."""

    def __init__(self, client: Any, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run, wal=None)
        self.client = client

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "list_pods":
            pods = await self.list_pods(params.get("namespace", "default"), params.get("label_selector"))
            return ChannelResult(success=True, data=pods)
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
