"""Kubernetes-to-ontology scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


class K8sScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def scan(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
        cluster_name: str = "lab-cluster",
    ) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        pods = await self.channel.list_pods(namespace, label_selector)
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        normalized_cluster = cluster_name.strip() or "lab-cluster"
        cluster_id = normalized_cluster if normalized_cluster.startswith("k8s:") else f"k8s:{normalized_cluster}"
        cluster_display_name = normalized_cluster[4:] if normalized_cluster.startswith("k8s:") else normalized_cluster
        nodes.append(
            OntologyNode(
                id=cluster_id,
                entity_type=EntityType.K8S_CLUSTER,
                name=cluster_display_name,
                properties={"source": "k8s"},
                status="online",
                updated_at=datetime.now(UTC),
            )
        )
        linked_nodes: set[str] = set()
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
            edges.append(
                OntologyEdge(
                    source_id=pod_id,
                    target_id=cluster_id,
                    relation=RelationType.PART_OF,
                    properties={"namespace": namespace},
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
                if node_name not in linked_nodes:
                    linked_nodes.add(node_name)
                    edges.append(
                        OntologyEdge(
                            source_id=node_name,
                            target_id=cluster_id,
                            relation=RelationType.PART_OF,
                            properties={"source": "k8s"},
                        )
                    )
        return nodes, edges
