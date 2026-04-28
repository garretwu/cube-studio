"""Topology discovery helpers shared by runtime services."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lib.channels.prometheus import PrometheusChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.switch import SwitchChannel
from sre_agent.config import SREAgentConfig
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.discovery.bmc_scanner import BMCScanner
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.discovery.prometheus_scanner import PrometheusScanner
from sre_agent.ontology.discovery.switch_scanner import SwitchScanner

LOGGER = logging.getLogger(__name__)

_DISCOVER_ALL_K8S_NAMESPACE_TOKENS = frozenset({"*", "all"})
_ENV_VAR_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _summary_counts(nodes: list[OntologyNode], edges: list[OntologyEdge]) -> dict[str, int]:
    return {"nodes": len(nodes), "edges": len(edges)}


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


def switch_payloads(config: SREAgentConfig) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for switch in config.ontology.discovery.switches:
        switch_payload = switch.model_dump(mode="python", exclude={"ports"}, exclude_none=True)
        switch_payload["id"] = switch.id or switch.name
        payloads.append(
            {
                **switch_payload,
                "ports": [
                    {
                        **port.model_dump(mode="python", exclude_none=True),
                        "id": port.id or f"{switch.id or switch.name}:{port.name}",
                    }
                    for port in switch.ports
                ],
            }
        )
    return payloads


def load_lab_seed_records(config: SREAgentConfig) -> list[dict[str, Any]]:
    extra = getattr(config, "model_extra", None)
    if not isinstance(extra, dict):
        return []
    payload = extra.get("lab_seed")
    if not isinstance(payload, dict):
        return []
    records = payload.get("nodes", [])
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _resolve_env_reference(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_VAR_PATTERN.match(value.strip())
        if not match:
            return value
        env_name = match.group(1)
        return os.getenv(env_name, value)
    if isinstance(value, list):
        return [_resolve_env_reference(item) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_env_reference(raw) for key, raw in value.items()}
    return value


def _relation_from_text(value: Any) -> RelationType:
    normalized = str(value or "").strip().lower()
    if normalized in {"part_of", "contains", "contain"}:
        return RelationType.PART_OF
    if normalized in {"connected_to", "connects_to", "connect"}:
        return RelationType.CONNECTED_TO
    if normalized in {"hosted_on", "runs_on", "run_on"}:
        return RelationType.HOSTED_ON
    if normalized in {"serves"}:
        return RelationType.SERVES
    if normalized in {"depends_on", "depends"}:
        return RelationType.DEPENDS_ON
    if normalized in {"manages", "manage"}:
        return RelationType.MANAGES
    if normalized in {"monitors", "monitor"}:
        return RelationType.MONITORS
    raise RuntimeError(f"unsupported relation in static_relations: {value}")


def _resolve_unified_inventory_path(config: SREAgentConfig) -> Path | None:
    value = str(config.ontology.discovery.unified_inventory_path or "").strip()
    if not value:
        return None
    return Path(value)


def _validate_unified_static_topology(static_topology: dict[str, Any]) -> None:
    required_sections = (
        "clusters",
        "switches",
        "switch_ports",
        "nodes",
        "gpus",
        "bmc_endpoints",
        "static_relations",
    )
    for section in required_sections:
        if section not in static_topology:
            raise RuntimeError(f"unified static_topology missing section: {section}")
        if not isinstance(static_topology.get(section), list):
            raise RuntimeError(f"unified static_topology.{section} must be a list")

    clusters = static_topology["clusters"]
    switches = static_topology["switches"]
    switch_ports = static_topology["switch_ports"]
    nodes = static_topology["nodes"]
    gpus = static_topology["gpus"]
    bmc_endpoints = static_topology["bmc_endpoints"]
    static_relations = static_topology["static_relations"]

    cluster_ids = {str(item.get("id", "")).strip() for item in clusters if isinstance(item, dict)}
    switch_ids = {str(item.get("id", "")).strip() for item in switches if isinstance(item, dict)}
    port_ids = {str(item.get("id", "")).strip() for item in switch_ports if isinstance(item, dict)}
    node_ids = {str(item.get("id", "")).strip() for item in nodes if isinstance(item, dict)}
    gpu_ids = {str(item.get("id", "")).strip() for item in gpus if isinstance(item, dict)}
    bmc_ids = {str(item.get("id", "")).strip() for item in bmc_endpoints if isinstance(item, dict)}
    all_ids = cluster_ids | switch_ids | port_ids | node_ids | gpu_ids | bmc_ids
    if "" in all_ids:
        raise RuntimeError("unified static_topology entities must provide non-empty id")
    if len(all_ids) != (
        len(cluster_ids) + len(switch_ids) + len(port_ids) + len(node_ids) + len(gpu_ids) + len(bmc_ids)
    ):
        raise RuntimeError("unified static_topology contains duplicate entity id")

    for port in switch_ports:
        if not isinstance(port, dict):
            continue
        switch_id = str(port.get("switch_id", "")).strip()
        connected_node_id = str(port.get("connected_node_id", "")).strip()
        if switch_id and switch_id not in switch_ids:
            raise RuntimeError(f"switch_port references unknown switch_id={switch_id}")
        if connected_node_id and connected_node_id not in node_ids:
            raise RuntimeError(f"switch_port references unknown connected_node_id={connected_node_id}")

    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        node_id = str(gpu.get("node_id", "")).strip()
        if node_id and node_id not in node_ids:
            raise RuntimeError(f"gpu references unknown node_id={node_id}")

    for bmc in bmc_endpoints:
        if not isinstance(bmc, dict):
            continue
        node_id = str(bmc.get("node_id", "")).strip()
        if node_id and node_id not in node_ids:
            raise RuntimeError(f"bmc_endpoint references unknown node_id={node_id}")

    for relation in static_relations:
        if not isinstance(relation, dict):
            continue
        source_id = str(relation.get("source_id", "")).strip()
        target_id = str(relation.get("target_id", "")).strip()
        if source_id not in all_ids:
            raise RuntimeError(f"static_relations references unknown source_id={source_id}")
        if target_id not in all_ids:
            raise RuntimeError(f"static_relations references unknown target_id={target_id}")
        _relation_from_text(relation.get("relation"))


def _validate_unified_dynamic_discovery(dynamic_discovery: dict[str, Any]) -> None:
    required_sections = (
        "k8s",
        "prometheus",
        "node_providers",
        "switch_providers",
        "discovery_policies",
    )
    for section in required_sections:
        if section not in dynamic_discovery:
            raise RuntimeError(f"unified dynamic_discovery missing section: {section}")
        if not isinstance(dynamic_discovery.get(section), dict):
            raise RuntimeError(f"unified dynamic_discovery.{section} must be a mapping")

    workers = dynamic_discovery["node_providers"].get("workers", [])
    if not isinstance(workers, list) or not workers:
        raise RuntimeError("unified dynamic_discovery.node_providers.workers must be a non-empty list")

    switches = dynamic_discovery["switch_providers"].get("switches", {})
    if not isinstance(switches, dict) or not switches:
        raise RuntimeError("unified dynamic_discovery.switch_providers.switches must be a non-empty mapping")

    prometheus = dynamic_discovery["prometheus"]
    if not str(prometheus.get("url", "")).strip():
        raise RuntimeError("unified dynamic_discovery.prometheus.url is required")
    baseline_queries = prometheus.get("baseline_queries", {})
    if not isinstance(baseline_queries, dict) or not baseline_queries:
        raise RuntimeError("unified dynamic_discovery.prometheus.baseline_queries must be a non-empty mapping")


def _normalize_unified_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    static_topology = payload.get("static_topology")
    dynamic_discovery = payload.get("dynamic_discovery")
    if not isinstance(static_topology, dict):
        raise RuntimeError("unified ontology config requires static_topology mapping")
    if not isinstance(dynamic_discovery, dict):
        raise RuntimeError("unified ontology config requires dynamic_discovery mapping")
    _validate_unified_static_topology(static_topology)
    _validate_unified_dynamic_discovery(dynamic_discovery)
    return payload


def load_unified_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"missing unified ontology config: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"unified ontology config must be a mapping: {path}")
    resolved = _resolve_env_reference(payload)
    if not isinstance(resolved, dict):
        raise RuntimeError(f"unified ontology config must be a mapping after env expansion: {path}")
    return _normalize_unified_inventory(resolved)


def _build_switch_payloads_from_unified(static_topology: dict[str, Any]) -> list[dict[str, Any]]:
    switch_items = static_topology.get("switches", [])
    port_items = static_topology.get("switch_ports", [])
    ports_by_switch: dict[str, list[dict[str, Any]]] = {}
    for port in port_items:
        if not isinstance(port, dict):
            continue
        switch_id = str(port.get("switch_id", "")).strip()
        port_id = str(port.get("id", "")).strip()
        port_name = str(port.get("name", "")).strip()
        if not switch_id or not port_id or not port_name:
            continue
        list_ref = ports_by_switch.setdefault(switch_id, [])
        port_payload = {k: v for k, v in port.items() if k not in {"connected_node_id"}}
        port_payload.update(
            {
                "id": port_id,
                "name": port_name,
                "connected_to": str(port.get("connected_node_id", "")).strip() or None,
                "status": str(port.get("status", "up")),
            }
        )
        list_ref.append(port_payload)

    payloads: list[dict[str, Any]] = []
    for switch in switch_items:
        if not isinstance(switch, dict):
            continue
        switch_id = str(switch.get("id", "")).strip()
        switch_name = str(switch.get("name", "")).strip() or switch_id
        if not switch_id:
            continue
        switch_payload = {k: v for k, v in switch.items() if k not in {"id", "ports"}}
        switch_payload.update(
            {
                "id": switch_id,
                "name": switch_name,
                "status": str(switch.get("status", "online")),
                "source": switch.get("source", "unified_static"),
                "ports": ports_by_switch.get(switch_id, []),
            }
        )
        payloads.append(switch_payload)
    return payloads


def _build_lab_seed_records_from_unified(
    static_topology: dict[str, Any],
    workers: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    gpus = static_topology.get("gpus", [])
    bmc_endpoints = static_topology.get("bmc_endpoints", [])
    nodes = static_topology.get("nodes", [])
    worker_ip_by_node: dict[str, str] = {}
    gpus_by_node: dict[str, list[str]] = {}
    for gpu in gpus:
        if not isinstance(gpu, dict):
            continue
        node_id = str(gpu.get("node_id", "")).strip()
        gpu_id = str(gpu.get("id", "")).strip()
        if not node_id or not gpu_id:
            continue
        gpus_by_node.setdefault(node_id, []).append(gpu_id)

    bmc_ip_by_node: dict[str, str] = {}
    for bmc in bmc_endpoints:
        if not isinstance(bmc, dict):
            continue
        node_id = str(bmc.get("node_id", "")).strip()
        ip = str(bmc.get("ip", "")).strip()
        if node_id and ip:
            bmc_ip_by_node[node_id] = ip

    for worker in workers or []:
        if not isinstance(worker, dict):
            continue
        node_id = str(worker.get("name", "")).strip()
        ssh_cfg = worker.get("ssh", {})
        ssh_host = str(ssh_cfg.get("host", "")).strip() if isinstance(ssh_cfg, dict) else ""
        if node_id and ssh_host:
            worker_ip_by_node[node_id] = ssh_host

    records: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        switch_ports = node.get("switch_ports", [])
        normalized_switch_ports: list[dict[str, Any]] = []
        if isinstance(switch_ports, list):
            for switch_port in switch_ports:
                if not isinstance(switch_port, dict):
                    continue
                switch_id = str(switch_port.get("switch_id", "")).strip()
                port_id = str(switch_port.get("port_id", "")).strip()
                nic = str(switch_port.get("nic", "")).strip()
                if not switch_id and not port_id and not nic:
                    continue
                normalized_switch_ports.append(
                    {
                        "switch_id": switch_id or None,
                        "port_id": port_id or None,
                        "nic": nic or None,
                    }
                )
        primary_switch_port = normalized_switch_ports[0] if normalized_switch_ports else {}
        records.append(
            {
                "id": node_id,
                "name": str(node.get("name", node_id)).strip() or node_id,
                "role": node.get("role", "unknown"),
                "source": node.get("source", "unified_static"),
                "status": str(node.get("status", "online")),
                "ip": str(node.get("ip", "")).strip() or worker_ip_by_node.get(node_id, ""),
                "bmc_ip": bmc_ip_by_node.get(node_id, ""),
                "switch": primary_switch_port.get("switch_id"),
                "port": primary_switch_port.get("port_id"),
                "switch_ports": normalized_switch_ports,
                "gpu_ids": sorted(gpus_by_node.get(node_id, [])),
            }
        )
    return records


def _cluster_nodes_from_unified(static_topology: dict[str, Any]) -> list[OntologyNode]:
    now = datetime.now(UTC)
    clusters = static_topology.get("clusters", [])
    nodes: list[OntologyNode] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("id", "")).strip()
        if not cluster_id:
            continue
        nodes.append(
            OntologyNode(
                id=cluster_id,
                entity_type=EntityType.CLUSTER,
                name=str(cluster.get("name", cluster_id)).strip() or cluster_id,
                properties={
                    "source": cluster.get("source", "unified_static"),
                    "domain": cluster.get("domain"),
                    "region": cluster.get("region"),
                    "zone": cluster.get("zone"),
                },
                status=str(cluster.get("status", "online")),
                updated_at=now,
            )
        )
    return nodes


def _cluster_edges_from_unified(static_topology: dict[str, Any]) -> list[OntologyEdge]:
    edges: list[OntologyEdge] = []
    clusters = static_topology.get("clusters", [])
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("id", "")).strip()
        for switch_id_raw in cluster.get("switch_ids", []) if isinstance(cluster.get("switch_ids"), list) else []:
            switch_id = str(switch_id_raw).strip()
            if not cluster_id or not switch_id:
                continue
            edges.append(
                OntologyEdge(
                    source_id=cluster_id,
                    target_id=switch_id,
                    relation=RelationType.PART_OF,
                    properties={"source": "unified_static", "semantic": "cluster_contains_switch"},
                )
            )

    for raw in static_topology.get("static_relations", []):
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_id", "")).strip()
        target_id = str(raw.get("target_id", "")).strip()
        if not source_id or not target_id:
            continue
        edges.append(
            OntologyEdge(
                source_id=source_id,
                target_id=target_id,
                relation=_relation_from_text(raw.get("relation")),
                properties={
                    "source": "unified_static",
                    "relation_semantic": raw.get("semantic"),
                },
            )
        )
    return edges

def lab_seed_topology(records: list[dict[str, Any]]) -> tuple[list[OntologyNode], list[OntologyEdge]]:
    now = datetime.now(UTC)
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    for raw in records:
        node_id = str(raw.get("id", "")).strip()
        if not node_id:
            continue

        nodes.append(
            OntologyNode(
                id=node_id,
                entity_type=EntityType.NODE,
                name=str(raw.get("name", node_id)).strip() or node_id,
                properties={
                    "role": raw.get("role", "unknown"),
                    "source": raw.get("source", "lab_seed"),
                    "ip": raw.get("ip"),
                    "switch": raw.get("switch"),
                    "port": raw.get("port"),
                    "switch_ports": list(raw.get("switch_ports", [])),
                },
                status=str(raw.get("status", "online")),
                updated_at=now,
            )
        )

        bmc_ip = str(raw.get("bmc_ip", "")).strip()
        if bmc_ip:
            bmc_id = f"bmc:{node_id}"
            nodes.append(
                OntologyNode(
                    id=bmc_id,
                    entity_type=EntityType.BMC_ENDPOINT,
                    name=f"BMC {node_id}",
                    properties={"ip": bmc_ip, "source": raw.get("source", "lab_seed")},
                    status="online",
                    updated_at=now,
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=bmc_id,
                    target_id=node_id,
                    relation=RelationType.MANAGES,
                    properties={},
                )
            )

        gpu_ids = raw.get("gpu_ids", [])
        if not isinstance(gpu_ids, list):
            continue
        for gpu_id in gpu_ids:
            value = str(gpu_id).strip()
            if not value:
                continue
            nodes.append(
                OntologyNode(
                    id=value,
                    entity_type=EntityType.GPU,
                    name=value,
                    properties={"host": node_id, "source": raw.get("source", "lab_seed")},
                    status="online",
                    updated_at=now,
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=value,
                    target_id=node_id,
                    relation=RelationType.PART_OF,
                    properties={},
                )
            )

    return nodes, edges


async def discover_static_snapshot(
    config: SREAgentConfig,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    unified_path = _resolve_unified_inventory_path(config)
    if unified_path is not None:
        unified_payload = load_unified_inventory(unified_path)
        static_topology = unified_payload["static_topology"]
        dynamic_discovery = unified_payload.get("dynamic_discovery", {})
        node_providers = dynamic_discovery.get("node_providers", {}) if isinstance(dynamic_discovery, dict) else {}
        workers = node_providers.get("workers", []) if isinstance(node_providers, dict) else []
        switch_nodes, switch_edges = await SwitchScanner(channel=None).scan(
            _build_switch_payloads_from_unified(static_topology)
        )
        lab_nodes, lab_edges = lab_seed_topology(_build_lab_seed_records_from_unified(static_topology, workers))
        cluster_nodes = _cluster_nodes_from_unified(static_topology)
        relation_edges = _cluster_edges_from_unified(static_topology)
        nodes = _dedupe_nodes([*switch_nodes, *lab_nodes, *cluster_nodes])
        edges = _dedupe_edges([*switch_edges, *lab_edges, *relation_edges])
        scanner_counts = {
            "switch": _summary_counts(switch_nodes, switch_edges),
            "lab_seed": _summary_counts(lab_nodes, lab_edges),
            "cluster": _summary_counts(cluster_nodes, relation_edges),
        }
        return nodes, edges, scanner_counts

    LOGGER.warning(
        "ontology unified_inventory_path not configured; using legacy static discovery fields "
        "(ontology.discovery.switches + lab_seed)"
    )
    scanner = SwitchScanner(channel=None)
    switch_nodes, switch_edges = await scanner.scan(switch_payloads(config))
    lab_nodes, lab_edges = lab_seed_topology(load_lab_seed_records(config))
    nodes = _dedupe_nodes([*switch_nodes, *lab_nodes])
    edges = _dedupe_edges([*switch_edges, *lab_edges])
    scanner_counts = {
        "switch": _summary_counts(switch_nodes, switch_edges),
        "lab_seed": _summary_counts(lab_nodes, lab_edges),
    }
    return nodes, edges, scanner_counts


def _normalize_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    workers = payload.get("inventory", {}).get("workers", [])
    if not isinstance(workers, list) or not workers:
        raise RuntimeError("live inventory requires inventory.workers list")
    switches = payload.get("switches", {})
    if not isinstance(switches, dict) or not switches:
        raise RuntimeError("live inventory requires switches mapping")
    monitor = payload.get("monitor", {})
    if not isinstance(monitor, dict):
        raise RuntimeError("live inventory monitor section must be a mapping")
    prometheus_url = str(monitor.get("prometheus_url", "")).strip()
    if not prometheus_url:
        raise RuntimeError("live inventory requires monitor.prometheus_url")
    baseline_queries = monitor.get("baseline_queries", {})
    if not isinstance(baseline_queries, dict) or not baseline_queries:
        raise RuntimeError("live inventory requires monitor.baseline_queries mapping")
    return payload


def load_live_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"missing live inventory config: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError(f"live inventory must be a mapping: {path}")
    return _normalize_inventory(payload)


def build_live_inventory_from_unified(unified_payload: dict[str, Any]) -> dict[str, Any]:
    dynamic_discovery = unified_payload.get("dynamic_discovery", {})
    if not isinstance(dynamic_discovery, dict):
        raise RuntimeError("unified ontology config missing dynamic_discovery mapping")
    node_providers = dynamic_discovery.get("node_providers", {})
    switch_providers = dynamic_discovery.get("switch_providers", {})
    prometheus = dynamic_discovery.get("prometheus", {})

    workers = node_providers.get("workers", []) if isinstance(node_providers, dict) else []
    switches = switch_providers.get("switches", {}) if isinstance(switch_providers, dict) else {}
    monitor = {
        "prometheus_url": prometheus.get("url"),
        "baseline_queries": prometheus.get("baseline_queries", {}),
        "prometheus_targets": prometheus.get("targets", {}),
    }
    return _normalize_inventory(
        {
            "inventory": {"workers": workers},
            "switches": switches,
            "monitor": monitor,
        }
    )


def resolve_discovery_runtime_inputs(
    config: SREAgentConfig,
) -> tuple[list[str], str, dict[str, str]]:
    discovery_cfg = config.ontology.discovery
    namespaces = list(discovery_cfg.k8s_namespaces)
    cluster_name = str(discovery_cfg.k8s_cluster_name).strip() or "lab-cluster"
    prom_targets = dict(discovery_cfg.prometheus_targets)
    unified_path = _resolve_unified_inventory_path(config)
    if unified_path is None:
        return namespaces, cluster_name, prom_targets

    unified_payload = load_unified_inventory(unified_path)
    dynamic_discovery = unified_payload["dynamic_discovery"]
    k8s_cfg = dynamic_discovery.get("k8s", {}) if isinstance(dynamic_discovery, dict) else {}
    prometheus_cfg = dynamic_discovery.get("prometheus", {}) if isinstance(dynamic_discovery, dict) else {}

    if not namespaces:
        raw_namespaces = k8s_cfg.get("namespaces", []) if isinstance(k8s_cfg, dict) else []
        if isinstance(raw_namespaces, list):
            namespaces = [str(item).strip() for item in raw_namespaces if str(item).strip()]
    if not cluster_name or cluster_name == "lab-cluster":
        configured_name = str(k8s_cfg.get("cluster_name", "")).strip() if isinstance(k8s_cfg, dict) else ""
        if configured_name:
            cluster_name = configured_name
    if not prom_targets:
        raw_targets = prometheus_cfg.get("targets", {}) if isinstance(prometheus_cfg, dict) else {}
        if isinstance(raw_targets, dict):
            prom_targets = {
                str(alias): str(target)
                for alias, target in raw_targets.items()
                if str(alias).strip() and str(target).strip()
            }

    return namespaces, cluster_name, prom_targets


class _LiveK8sDiscoveryChannel:
    def __init__(self, kubeconfig: str = "~/.kube/config") -> None:
        self._kubeconfig = kubeconfig

    async def list_namespaces(self) -> list[str]:
        def _fetch() -> list[str]:
            try:
                from kubernetes import client as k8s_client
                from kubernetes import config as k8s_config
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("python package 'kubernetes' is required for live K8s discovery") from exc

            k8s_config.load_kube_config(config_file=self._kubeconfig)
            api = k8s_client.CoreV1Api()
            namespaces = api.list_namespace()
            names: list[str] = []
            for namespace in namespaces.items:
                name = str(namespace.metadata.name or "").strip()
                if name:
                    names.append(name)
            return names

        return await asyncio.to_thread(_fetch)

    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            try:
                from kubernetes import client as k8s_client
                from kubernetes import config as k8s_config
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("python package 'kubernetes' is required for live K8s discovery") from exc

            k8s_config.load_kube_config(config_file=self._kubeconfig)
            api = k8s_client.CoreV1Api()
            pods = api.list_namespaced_pod(namespace, label_selector=label_selector)
            payloads: list[dict[str, Any]] = []
            for pod in pods.items:
                payloads.append(
                    {
                        "metadata": {
                            "name": pod.metadata.name,
                            "labels": dict(pod.metadata.labels or {}),
                        },
                        "status": {"phase": pod.status.phase or "Unknown"},
                        "spec": {"nodeName": pod.spec.node_name},
                    }
                )
            return payloads

        return await asyncio.to_thread(_fetch)

    async def list_services(self, namespace: str) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            try:
                from kubernetes import client as k8s_client
                from kubernetes import config as k8s_config
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("python package 'kubernetes' is required for live K8s discovery") from exc

            k8s_config.load_kube_config(config_file=self._kubeconfig)
            api = k8s_client.CoreV1Api()
            services = api.list_namespaced_service(namespace)
            payloads: list[dict[str, Any]] = []
            for service in services.items:
                payloads.append(
                    {
                        "metadata": {
                            "name": service.metadata.name,
                            "labels": dict(service.metadata.labels or {}),
                        },
                        "spec": {
                            "selector": dict(service.spec.selector or {}),
                            "type": service.spec.type,
                            "clusterIP": service.spec.cluster_ip,
                        },
                        "status": {},
                    }
                )
            return payloads

        return await asyncio.to_thread(_fetch)


def _normalize_k8s_namespace_allowlist(k8s_namespaces: list[str] | None) -> list[str]:
    if not k8s_namespaces:
        return []
    allowlist: list[str] = []
    for item in k8s_namespaces:
        namespace = str(item or "").strip()
        if not namespace:
            continue
        if namespace.casefold() in _DISCOVER_ALL_K8S_NAMESPACE_TOKENS:
            return []
        if namespace not in allowlist:
            allowlist.append(namespace)
    return allowlist


def _should_exclude_dynamic_k8s_namespace(namespace: str) -> bool:
    """Exclude only Kubernetes system namespaces by prefix."""

    normalized = namespace.strip().casefold()
    return normalized.startswith("kube")


async def _resolve_k8s_namespaces(
    channel: Any,
    k8s_namespaces: list[str] | None,
) -> list[str]:
    allowlist = _normalize_k8s_namespace_allowlist(k8s_namespaces)
    if allowlist:
        return allowlist

    discovered = await channel.list_namespaces()
    filtered: list[str] = []
    for item in discovered:
        namespace = str(item or "").strip()
        if not namespace or _should_exclude_dynamic_k8s_namespace(namespace):
            continue
        if namespace not in filtered:
            filtered.append(namespace)
    return sorted(filtered)


class _LivePrometheusQueryChannel:
    def __init__(self, base_url: str, queries: dict[str, str]) -> None:
        self._channel = PrometheusChannel(base_url=base_url, timeout=15)
        self._queries = dict(queries)

    async def query_instant(self, metric_alias: str) -> float:
        promql = self._queries.get(metric_alias, "")
        if not promql:
            raise RuntimeError(f"missing Prometheus query alias: {metric_alias}")
        return await self._channel.query_instant(promql)

    async def close(self) -> None:
        await self._channel.close()


def _extract_connected_worker(interface: Any, worker_names: list[str]) -> str | None:
    hints: list[str] = []
    for field in ("description", "alias", "name", "abbreviated_name", "lldp_peer", "peer"):
        value = getattr(interface, field, None)
        if isinstance(value, str) and value.strip():
            hints.append(value.strip().lower())
    merged = " ".join(hints)
    for worker in worker_names:
        if worker.lower() in merged:
            return worker
    return None


def _normalize_secret_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.upper() in {"REPLACE_ME", "CHANGEME", "TODO"}:
        return ""
    return text


def _resolve_bool(raw: Any, *, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _build_live_inventory_base_nodes(workers: list[dict[str, Any]]) -> list[OntologyNode]:
    now = datetime.now(UTC)
    nodes: list[OntologyNode] = []
    for worker in workers:
        name = str(worker.get("name", "")).strip()
        if not name:
            continue
        ssh_cfg = worker.get("ssh")
        ssh_host = str(ssh_cfg.get("host", "")).strip() if isinstance(ssh_cfg, dict) else ""
        k8s_node_name = str(worker.get("k8s_node_name", "")).strip()
        properties: dict[str, Any] = {"source": "live_inventory"}
        if ssh_host:
            properties["ip"] = ssh_host
        if k8s_node_name:
            properties["k8s_node_name"] = k8s_node_name
        nodes.append(
            OntologyNode(
                id=name,
                entity_type=EntityType.NODE,
                name=name,
                properties=properties,
                status="online",
                updated_at=now,
            )
        )
    return nodes


async def scan_live_sources(
    raw_config: dict[str, Any],
    *,
    kubeconfig: str | None = None,
    k8s_namespaces: list[str] | None = None,
    k8s_cluster_name: str = "lab-cluster",
    prometheus_targets: dict[str, str] | None = None,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    workers: list[dict[str, Any]] = raw_config["inventory"]["workers"]
    switches: dict[str, dict[str, Any]] = raw_config["switches"]
    monitor: dict[str, Any] = raw_config["monitor"]
    worker_names = [str(worker["name"]).strip() for worker in workers]
    representative_worker = worker_names[0]
    worker_node_set = set(worker_names)
    base_nodes = _build_live_inventory_base_nodes(workers)

    bmc_payloads: list[dict[str, Any]] = []
    bmc_warnings: list[str] = []
    env_redfish_username = _normalize_secret_value(os.getenv("SRE_REDFISH_USERNAME", ""))
    env_redfish_password = _normalize_secret_value(os.getenv("SRE_REDFISH_PASSWORD", ""))
    env_redfish_verify_tls = os.getenv("SRE_REDFISH_VERIFY_TLS", "")
    for worker in workers:
        worker_name = str(worker["name"]).strip()
        redfish_cfg: dict[str, Any] = worker["redfish"]
        username = _normalize_secret_value(redfish_cfg.get("username")) or env_redfish_username
        password = _normalize_secret_value(redfish_cfg.get("password")) or env_redfish_password
        if not username or not password:
            bmc_warnings.append(
                f"worker={worker_name} missing redfish credentials; "
                "provide redfish.username/password or SRE_REDFISH_USERNAME/SRE_REDFISH_PASSWORD"
            )
            continue
        verify_tls_raw = redfish_cfg.get("verify_tls")
        verify_tls = _resolve_bool(
            verify_tls_raw if verify_tls_raw is not None else env_redfish_verify_tls,
            default=True,
        )
        timeout_sec = int(redfish_cfg.get("timeout", 30))
        redfish = RedfishChannel(timeout=timeout_sec)
        try:
            auth = await redfish.authenticate(
                str(redfish_cfg["bmc_host"]),
                username,
                password,
                verify_tls=verify_tls,
            )
            if not auth.success:
                bmc_warnings.append(
                    f"worker={worker_name} redfish auth failed for host={redfish_cfg['bmc_host']}: {auth.error}"
                )
                continue
            info = await redfish.get_bmc_info(
                str(redfish_cfg["bmc_host"]),
                verify_tls=verify_tls,
            )
            if not info.success:
                bmc_warnings.append(
                    f"worker={worker_name} redfish info failed for host={redfish_cfg['bmc_host']}: {info.error}"
                )
                continue
            info_json = yaml.safe_load(info.output) if info.output else {}
            if not isinstance(info_json, dict):
                info_json = {}
        except Exception as exc:  # noqa: BLE001
            bmc_warnings.append(f"worker={worker_name} redfish scan exception: {exc}")
            continue
        finally:
            await redfish.close()

        bmc_payloads.append(
            {
                "node_id": worker_name,
                "id": f"bmc:{worker_name}",
                "name": str((info_json.get("manager", {}) or {}).get("name") or f"BMC {worker_name}"),
                "ip": str(redfish_cfg["bmc_host"]),
                "firmware_version": (info_json.get("manager", {}) or {}).get("firmware_version"),
                "capabilities": (info_json.get("manager", {}) or {}).get("actions", []),
                "status": "online",
            }
        )
    bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan(bmc_payloads)

    effective_kubeconfig = kubeconfig or (os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config")
    k8s_channel = _LiveK8sDiscoveryChannel(kubeconfig=effective_kubeconfig)
    namespaces = await _resolve_k8s_namespaces(k8s_channel, k8s_namespaces)
    k8s_nodes: list[OntologyNode] = []
    k8s_edges: list[OntologyEdge] = []
    for namespace in namespaces:
        ns_nodes, ns_edges = await K8sScanner(channel=k8s_channel).scan(
            namespace=namespace,
            label_selector=None,
            cluster_name=k8s_cluster_name,
        )
        k8s_nodes.extend(ns_nodes)
        k8s_edges.extend(ns_edges)
    discovered_k8s_node_ids = sorted(
        {
            edge.target_id
            for edge in k8s_edges
            if edge.target_id and edge.relation == RelationType.HOSTED_ON
        }
    )
    k8s_extra_nodes = [
        OntologyNode(
            id=node_id,
            entity_type=EntityType.NODE,
            name=node_id,
            properties={"source": "live_k8s"},
            status="online",
            updated_at=datetime.now(UTC),
        )
        for node_id in discovered_k8s_node_ids
        if node_id not in worker_node_set
    ]

    baseline_queries = monitor["baseline_queries"]
    prom_aliases = {str(alias): str(query) for alias, query in baseline_queries.items() if str(query).strip()}
    resolved_targets = {
        str(alias): str(target)
        for alias, target in (prometheus_targets or monitor.get("prometheus_targets") or {}).items()
        if str(alias).strip() and str(target).strip()
    }
    if not resolved_targets:
        for alias in prom_aliases:
            default_target = representative_worker
            if alias == "memory_util" and discovered_k8s_node_ids:
                default_target = discovered_k8s_node_ids[0]
            resolved_targets[alias] = default_target
    prom_channel = _LivePrometheusQueryChannel(str(monitor["prometheus_url"]), prom_aliases)
    try:
        prom_nodes, prom_edges = await PrometheusScanner(channel=prom_channel).scan(resolved_targets)
    finally:
        await prom_channel.close()

    switch_timeout = max(int(item.get("timeout", 30)) for item in switches.values())
    switch_channel = SwitchChannel(devices=switches, dry_run=False, timeout=switch_timeout)
    switch_payloads: list[dict[str, Any]] = []
    try:
        for switch_name, switch_cfg in sorted(switches.items()):
            interfaces = switch_channel.get_all_interfaces(switch_name)
            if not interfaces:
                raise RuntimeError(f"live switch interface discovery returned no ports for switch={switch_name}")
            ports: list[dict[str, Any]] = []
            for interface in interfaces:
                interface_name = str(
                    getattr(interface, "abbreviated_name", "")
                    or getattr(interface, "name", "")
                    or getattr(interface, "if_index", "unknown")
                ).strip()
                if not interface_name:
                    continue
                connected_to = _extract_connected_worker(interface, worker_names)
                speed_value = getattr(interface, "speed_gbps", None) or getattr(interface, "speed", None)
                speed_gbps: int | None = None
                if isinstance(speed_value, (int, float)):
                    speed_gbps = int(speed_value)
                ports.append(
                    {
                        "id": f"{switch_name}:{interface_name}",
                        "name": interface_name,
                        "status": str(getattr(interface, "oper_status", "unknown")),
                        "connected_to": connected_to,
                        "speed_gbps": speed_gbps,
                    }
                )
            switch_payloads.append(
                {
                    "id": switch_name,
                    "name": switch_name,
                    "type": switch_cfg.get("type"),
                    "status": "online",
                    "ports": ports,
                }
            )
    finally:
        switch_channel.close()
    switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan(switch_payloads)

    all_nodes = _dedupe_nodes([*base_nodes, *k8s_extra_nodes, *bmc_nodes, *k8s_nodes, *prom_nodes, *switch_nodes])
    all_edges = _dedupe_edges([*bmc_edges, *k8s_edges, *prom_edges, *switch_edges])
    scanner_counts = {
        "bmc": _summary_counts(bmc_nodes, bmc_edges),
        "k8s": _summary_counts(k8s_nodes, k8s_edges),
        "prometheus": _summary_counts(prom_nodes, prom_edges),
        "switch": _summary_counts(switch_nodes, switch_edges),
    }
    if bmc_warnings:
        preview = "; ".join(bmc_warnings[:3])
        suffix = f" (and {len(bmc_warnings) - 3} more)" if len(bmc_warnings) > 3 else ""
        LOGGER.warning("live BMC scan degraded: %s%s", preview, suffix)
    return all_nodes, all_edges, scanner_counts


def _merge_static_dynamic_nodes(
    static_nodes: list[OntologyNode],
    dynamic_nodes: list[OntologyNode],
) -> list[OntologyNode]:
    by_id: dict[str, OntologyNode] = {node.id: node for node in static_nodes}
    for node in dynamic_nodes:
        # Static inventory remains authoritative for long-lived infra assets.
        if node.id in by_id:
            continue
        by_id[node.id] = node
    return sorted(by_id.values(), key=lambda item: item.id)


async def discover_k8s_workload_snapshot(
    config: SREAgentConfig,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    effective_kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    namespaces, cluster_name, _prom_targets = resolve_discovery_runtime_inputs(config)

    k8s_channel = _LiveK8sDiscoveryChannel(kubeconfig=effective_kubeconfig)
    namespaces = await _resolve_k8s_namespaces(k8s_channel, namespaces)
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []
    for namespace in namespaces:
        ns_nodes, ns_edges = await K8sScanner(channel=k8s_channel).scan(
            namespace=namespace,
            label_selector=None,
            cluster_name=cluster_name,
        )
        nodes.extend(ns_nodes)
        edges.extend(ns_edges)
    deduped_nodes = _dedupe_nodes(nodes)
    deduped_edges = _dedupe_edges(edges)
    return deduped_nodes, deduped_edges, {"k8s_workload": _summary_counts(deduped_nodes, deduped_edges)}


async def discover_hybrid_snapshot(
    config: SREAgentConfig,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]], str | None]:
    static_nodes, static_edges, static_counts = await discover_static_snapshot(config)
    static_node_ids = {node.id for node in static_nodes}
    fallback_reason: str | None = None
    try:
        dynamic_nodes, dynamic_edges, dynamic_counts = await discover_k8s_workload_snapshot(config)
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"K8s workload discovery failed: {exc}"
        LOGGER.warning(fallback_reason)
        dynamic_nodes: list[OntologyNode] = []
        dynamic_edges: list[OntologyEdge] = []
        dynamic_counts: dict[str, dict[str, int]] = {"k8s_workload": {"nodes": 0, "edges": 0}, "k8s_error": {"detail": 1}}

    # Deduplicate cluster entities: K8s creates k8s:<name> while static uses cluster:<name>.
    # When both exist for the same cluster name, keep the static one and remap dynamic edges.
    static_cluster_map: dict[str, str] = {}  # k8s cluster name → static cluster id
    for node in static_nodes:
        is_static_cluster = node.entity_type == EntityType.CLUSTER or str(node.id).startswith("cluster:")
        if not is_static_cluster:
            continue
        # Extract name from "cluster:<name>"
        cluster_name = node.id.split(":", 1)[1] if ":" in node.id else node.id
        cluster_name = cluster_name.strip()
        if cluster_name:
            static_cluster_map[cluster_name] = node.id

    k8s_cluster_ids_to_remove: set[str] = set()
    for node in dynamic_nodes:
        if node.entity_type == EntityType.K8S_CLUSTER:
            # Extract name from "k8s:<name>"
            cluster_name = node.id.split(":", 1)[1] if ":" in node.id else node.id
            if cluster_name in static_cluster_map:
                k8s_cluster_ids_to_remove.add(node.id)

    if k8s_cluster_ids_to_remove:
        dynamic_nodes = [n for n in dynamic_nodes if n.id not in k8s_cluster_ids_to_remove]
        # Remap edges: replace k8s:<name> targets with cluster:<name>
        remap: dict[str, str] = {}
        for k8s_id in k8s_cluster_ids_to_remove:
            k8s_name = k8s_id.split(":", 1)[1] if ":" in k8s_id else k8s_id
            remap[k8s_id] = static_cluster_map[k8s_name]
        dynamic_edges = [
            OntologyEdge(
                source_id=remap.get(e.source_id, e.source_id),
                target_id=remap.get(e.target_id, e.target_id),
                relation=e.relation,
                properties=e.properties,
            )
            for e in dynamic_edges
        ]

    hosted_node_ids = {
        edge.target_id
        for edge in dynamic_edges
        if edge.relation == RelationType.HOSTED_ON and edge.target_id and edge.target_id not in static_node_ids
    }
    placeholder_nodes = [
        OntologyNode(
            id=node_id,
            entity_type=EntityType.NODE,
            name=node_id,
            properties={
                "source": "k8s_placeholder",
                "placeholder": True,
            },
            status="online",
            updated_at=datetime.now(UTC),
        )
        for node_id in sorted(hosted_node_ids)
    ]

    merged_nodes = _merge_static_dynamic_nodes(static_nodes, [*dynamic_nodes, *placeholder_nodes])
    merged_edges = _dedupe_edges([*static_edges, *dynamic_edges])
    scanner_counts = {**static_counts, **dynamic_counts}
    return merged_nodes, merged_edges, scanner_counts, fallback_reason


async def discover_live_snapshot(
    config: SREAgentConfig,
    *,
    inventory_path: Path | None = None,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    discovery_cfg = config.ontology.discovery
    unified_path = _resolve_unified_inventory_path(config)
    if inventory_path is not None:
        raw_inventory = load_live_inventory(inventory_path)
    elif unified_path is not None:
        unified_payload = load_unified_inventory(unified_path)
        raw_inventory = build_live_inventory_from_unified(unified_payload)
    else:
        LOGGER.warning(
            "ontology unified_inventory_path not configured; using legacy live inventory path "
            "(ontology.discovery.live_inventory_path)"
        )
        path = Path(discovery_cfg.live_inventory_path)
        raw_inventory = load_live_inventory(path)

    k8s_namespaces, cluster_name, prom_targets = resolve_discovery_runtime_inputs(config)
    return await scan_live_sources(
        raw_inventory,
        k8s_namespaces=k8s_namespaces,
        k8s_cluster_name=cluster_name,
        prometheus_targets=prom_targets,
    )
