"""Kubernetes-to-ontology scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


def _selector_to_label_selector(selector: dict[str, Any] | None) -> str | None:
    if not isinstance(selector, dict) or not selector:
        return None
    parts: list[str] = []
    for key, value in selector.items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if key_text and value_text:
            parts.append(f"{key_text}={value_text}")
    return ",".join(parts) if parts else None


def _dedupe_nodes(nodes: list[OntologyNode]) -> list[OntologyNode]:
    by_id: dict[str, OntologyNode] = {}
    for node in nodes:
        by_id[node.id] = node
    return sorted(by_id.values(), key=lambda item: item.id)


def _dedupe_edges(edges: list[OntologyEdge]) -> list[OntologyEdge]:
    by_key: dict[tuple[str, str, str], OntologyEdge] = {}
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.relation.value)
        by_key[key] = edge
    return sorted(by_key.values(), key=lambda item: (item.source_id, item.target_id, item.relation.value))


class K8sScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def _list_services(self, namespace: str) -> list[dict[str, Any]]:
        list_services = getattr(self.channel, "list_services", None)
        if not callable(list_services):
            return []
        services = await list_services(namespace)
        if not isinstance(services, list):
            return []
        return [item for item in services if isinstance(item, dict)]

    async def _resolve_service_pod_names(
        self,
        *,
        namespace: str,
        service_name: str,
        selector: dict[str, Any] | None,
    ) -> list[str]:
        resolver = getattr(self.channel, "resolve_pod_names_for_service", None)
        if callable(resolver):
            pod_names = await resolver(namespace=namespace, service_name=service_name)
            if isinstance(pod_names, list):
                return [str(name).strip() for name in pod_names if str(name).strip()]
            return []

        label_selector = _selector_to_label_selector(selector)
        if not label_selector:
            return []

        pods = await self.channel.list_pods(namespace, label_selector)
        pod_names: list[str] = []
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            metadata = pod.get("metadata", {})
            name = (
                str((metadata.get("name") if isinstance(metadata, dict) else "") or "").strip()
                or str(pod.get("name", "")).strip()
            )
            if name:
                pod_names.append(name)
        return pod_names

    async def scan(
        self,
        namespace: str = "default",
        label_selector: str | None = None,
        cluster_name: str = "lab-cluster",
    ) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        now = datetime.now(UTC)
        pods = await self.channel.list_pods(namespace, label_selector)
        services = await self._list_services(namespace)

        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        normalized_cluster = cluster_name.strip() or "lab-cluster"
        cluster_id = normalized_cluster if normalized_cluster.startswith("k8s:") else f"k8s:{normalized_cluster}"
        # NOTE: Do NOT create a K8S_CLUSTER node here — the cluster entity is owned by
        # static topology (discover_static_snapshot).  Hybrid merge deduplicates, but
        # skipping creation avoids an unnecessary entity entirely.

        # Namespace service group node: aggregates pods and services under a namespace
        ns_group_id = f"ns:{namespace}"
        nodes.append(
            OntologyNode(
                id=ns_group_id,
                entity_type=EntityType.INFERENCE_SERVICE,
                name=namespace,
                properties={
                    "namespace": namespace,
                    "kind": "namespace_group",
                    "cluster_id": cluster_id,
                    "source": "k8s",
                },
                status="online",
                updated_at=now,
            )
        )

        linked_nodes: set[str] = set()
        pod_id_by_name: dict[str, str] = {}
        pod_node_by_id: dict[str, str] = {}

        for pod in pods:
            if not isinstance(pod, dict):
                continue
            metadata = pod.get("metadata", {})
            status = pod.get("status", {})
            pod_name = str((metadata.get("name") if isinstance(metadata, dict) else "") or pod.get("name") or "").strip()
            if not pod_name:
                continue
            pod_id = f"pod:{namespace}:{pod_name}"
            node_name = (
                str(
                    (pod.get("spec", {}).get("nodeName") if isinstance(pod.get("spec"), dict) else "")
                    or pod.get("node_id")
                    or ""
                ).strip()
            )
            phase = str((status.get("phase") if isinstance(status, dict) else "") or "Unknown")
            labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
            pod_id_by_name[pod_name] = pod_id
            if node_name:
                pod_node_by_id[pod_id] = node_name

            nodes.append(
                OntologyNode(
                    id=pod_id,
                    entity_type=EntityType.K8S_POD,
                    name=pod_name,
                    properties={
                        "namespace": namespace,
                        "labels": labels if isinstance(labels, dict) else {},
                        "phase": phase,
                        "cluster_id": cluster_id,
                        "source": "k8s",
                    },
                    status=phase,
                    updated_at=now,
                )
            )
            # pod → namespace group
            edges.append(
                OntologyEdge(
                    source_id=pod_id,
                    target_id=ns_group_id,
                    relation=RelationType.PART_OF,
                    properties={"namespace": namespace},
                )
            )
            if node_name and node_name not in linked_nodes:
                    linked_nodes.add(node_name)
                    edges.append(
                        OntologyEdge(
                            source_id=node_name,
                            target_id=cluster_id,
                            relation=RelationType.PART_OF,
                            properties={"source": "k8s"},
                        )
                    )

        for service in services:
            metadata = service.get("metadata", {})
            spec = service.get("spec", {})
            status = service.get("status", {})
            service_name = (
                str((metadata.get("name") if isinstance(metadata, dict) else "") or service.get("name") or "").strip()
            )
            if not service_name:
                continue
            selector = spec.get("selector") if isinstance(spec, dict) else {}
            selector = selector if isinstance(selector, dict) else {}
            service_id = f"svc:{namespace}:{service_name}"
            service_status = str((status.get("phase") if isinstance(status, dict) else "") or "online")

            nodes.append(
                OntologyNode(
                    id=service_id,
                    entity_type=EntityType.INFERENCE_SERVICE,
                    name=service_name,
                    properties={
                        "namespace": namespace,
                        "labels": metadata.get("labels", {}) if isinstance(metadata, dict) else {},
                        "selector": selector,
                        "cluster_ip": spec.get("clusterIP") if isinstance(spec, dict) else None,
                        "service_type": spec.get("type") if isinstance(spec, dict) else None,
                        "cluster_id": cluster_id,
                        "source": "k8s",
                    },
                    status=service_status,
                    updated_at=now,
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=service_id,
                    target_id=cluster_id,
                    relation=RelationType.PART_OF,
                    properties={"namespace": namespace},
                )
            )
            # service → namespace group
            edges.append(
                OntologyEdge(
                    source_id=service_id,
                    target_id=ns_group_id,
                    relation=RelationType.PART_OF,
                    properties={"namespace": namespace},
                )
            )

            service_pod_names = await self._resolve_service_pod_names(
                namespace=namespace,
                service_name=service_name,
                selector=selector,
            )
            service_nodes: set[str] = set()
            for pod_name in sorted(set(service_pod_names)):
                pod_id = pod_id_by_name.get(pod_name, f"pod:{namespace}:{pod_name}")
                if pod_id not in pod_id_by_name.values():
                    # Keep graph complete when the service references pods omitted by current pod list call.
                    nodes.append(
                        OntologyNode(
                            id=pod_id,
                            entity_type=EntityType.K8S_POD,
                            name=pod_name,
                            properties={
                                "namespace": namespace,
                                "labels": {},
                                "phase": "Unknown",
                                "cluster_id": cluster_id,
                                "source": "k8s_placeholder",
                            },
                            status="Unknown",
                            updated_at=now,
                        )
                    )
                edges.append(
                    OntologyEdge(
                        source_id=service_id,
                        target_id=pod_id,
                        relation=RelationType.SERVES,
                        properties={"namespace": namespace},
                    )
                )
                node_name = pod_node_by_id.get(pod_id)
                if not node_name:
                    continue
                service_nodes.add(node_name)
            for node_name in sorted(service_nodes):
                edges.append(
                    OntologyEdge(
                        source_id=service_id,
                        target_id=node_name,
                        relation=RelationType.HOSTED_ON,
                        properties={
                            "namespace": namespace,
                            "derived_from": "service_pod_mapping",
                        },
                    )
                )

        return _dedupe_nodes(nodes), _dedupe_edges(edges)
