"""Topology discovery helpers shared by runtime services."""

from __future__ import annotations

import asyncio
import os
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
                    "switch": raw.get("switch"),
                    "port": raw.get("port"),
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


class _LiveK8sDiscoveryChannel:
    def __init__(self, kubeconfig: str = "~/.kube/config") -> None:
        self._kubeconfig = kubeconfig

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

    base_nodes = [
        OntologyNode(
            id=name,
            entity_type=EntityType.NODE,
            name=name,
            properties={"source": "live_inventory"},
            status="online",
            updated_at=datetime.now(UTC),
        )
        for name in worker_names
    ]

    bmc_payloads: list[dict[str, Any]] = []
    for worker in workers:
        worker_name = str(worker["name"]).strip()
        redfish_cfg: dict[str, Any] = worker["redfish"]
        timeout_sec = int(redfish_cfg.get("timeout", 30))
        redfish = RedfishChannel(timeout=timeout_sec)
        try:
            auth = await redfish.authenticate(
                str(redfish_cfg["bmc_host"]),
                str(redfish_cfg["username"]),
                str(redfish_cfg["password"]),
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            if not auth.success:
                raise RuntimeError(
                    f"live BMC auth failed for worker={worker_name} host={redfish_cfg['bmc_host']}: {auth.error}"
                )
            info = await redfish.get_bmc_info(
                str(redfish_cfg["bmc_host"]),
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            if not info.success:
                raise RuntimeError(
                    f"live BMC info query failed for worker={worker_name} host={redfish_cfg['bmc_host']}: {info.error}"
                )
            info_json = yaml.safe_load(info.output) if info.output else {}
            if not isinstance(info_json, dict):
                info_json = {}
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
    namespaces = [item.strip() for item in (k8s_namespaces or ["default"]) if item and item.strip()]
    if not namespaces:
        namespaces = ["default"]
    k8s_channel = _LiveK8sDiscoveryChannel(kubeconfig=effective_kubeconfig)
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
    return all_nodes, all_edges, scanner_counts


async def discover_live_snapshot(
    config: SREAgentConfig,
    *,
    inventory_path: Path | None = None,
) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    discovery_cfg = config.ontology.discovery
    path = inventory_path or Path(discovery_cfg.live_inventory_path)
    raw_inventory = load_live_inventory(path)
    k8s_namespaces = list(discovery_cfg.k8s_namespaces)
    cluster_name = str(discovery_cfg.k8s_cluster_name).strip() or "lab-cluster"
    prom_targets = dict(discovery_cfg.prometheus_targets)
    return await scan_live_sources(
        raw_inventory,
        k8s_namespaces=k8s_namespaces,
        k8s_cluster_name=cluster_name,
        prometheus_targets=prom_targets,
    )
