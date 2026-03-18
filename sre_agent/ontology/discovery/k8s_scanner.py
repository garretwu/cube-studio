"""Kubernetes-to-ontology scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


class K8sScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def scan(self, namespace: str = "default", label_selector: str | None = None) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        pods = await self.channel.list_pods(namespace, label_selector)
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        for pod in pods:
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            pod_name = metadata.get("name") or pod.get("name") or "unknown-pod"
            pod_id = f"pod:{namespace}:{pod_name}"
            node_name = pod.get("spec", {}).get("nodeName") or pod.get("node_id")
            nodes.append(
                OntologyNode(
                    id=pod_id,
                    entity_type=EntityType.K8S_POD,
                    name=pod_name,
                    properties={
                        "namespace": namespace,
                        "labels": metadata.get("labels", {}),
                        "phase": status.get("phase", "Unknown"),
                    },
                    status=status.get("phase", "Unknown"),
                    updated_at=datetime.now(UTC),
                )
            )
            if node_name:
                edges.append(
                    OntologyEdge(
                        source_id=pod_id,
                        target_id=node_name,
                        relation=RelationType.HOSTED_ON,
                        properties={"namespace": namespace},
                    )
                )
        return nodes, edges
