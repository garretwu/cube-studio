"""Topology discovery helpers shared by runtime services."""

from __future__ import annotations

import asyncio
import logging
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

LOGGER = logging.getLogger(__name__)

_DISCOVER_ALL_K8S_NAMESPACE_TOKENS = frozenset({"*", "all"})
_EXCLUDED_DYNAMIC_K8S_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})


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
        if not namespace or namespace in _EXCLUDED_DYNAMIC_K8S_NAMESPACES:
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
    discovery_cfg = config.ontology.discovery
    effective_kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    cluster_name = str(discovery_cfg.k8s_cluster_name).strip() or "lab-cluster"

    k8s_channel = _LiveK8sDiscoveryChannel(kubeconfig=effective_kubeconfig)
    namespaces = await _resolve_k8s_namespaces(k8s_channel, discovery_cfg.k8s_namespaces)
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
    try:
        dynamic_nodes, dynamic_edges, dynamic_counts = await discover_k8s_workload_snapshot(config)
    except Exception as exc:  # noqa: BLE001
        fallback_reason = f"dynamic K8s workload discovery failed, retained static snapshot: {exc}"
        LOGGER.warning(fallback_reason)
        return static_nodes, static_edges, static_counts, fallback_reason

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
    return merged_nodes, merged_edges, scanner_counts, None


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
