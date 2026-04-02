"""Click CLI entrypoint for the SRE agent."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import Any, Literal

import click
import uvicorn
import yaml

from lib.channels.prometheus import PrometheusChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.switch import SwitchChannel
from sre_agent.config import SREAgentConfig, apply_llm_env_from_config, load_config
from sre_agent.knowledge.ingest import KnowledgeIngestSummary, KnowledgeIngestor
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.memory import IncidentRecord
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.discovery.bmc_scanner import BMCScanner
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.discovery.prometheus_scanner import PrometheusScanner
from sre_agent.ontology.discovery.switch_scanner import SwitchScanner
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.server import create_app

_LIVE_INVENTORY_PATH = Path("fault_injector/fault-injector-test.yaml")
LOGGER = logging.getLogger(__name__)


def _format_topology_summary(config: SREAgentConfig, summary: dict[str, object]) -> str:
    lines = [
        "Topology Summary",
        f"AIDC: {config.global_.aidc_id}",
        f"DB: {config.ontology.db_path}",
        f"Nodes: {summary['node_count']}",
        f"Edges: {summary['edge_count']}",
        "Entity Types:",
    ]
    entity_counts = summary.get("entity_type_counts", {})
    if isinstance(entity_counts, dict) and entity_counts:
        for entity_type, count in entity_counts.items():
            lines.append(f"- {entity_type}: {count}")
    else:
        lines.append("- none: 0")
    return "\n".join(lines)


async def _load_topology_summary(config: SREAgentConfig) -> dict[str, object]:
    graph = OntologyGraph(db_path=config.ontology.db_path)
    await graph.connect()
    try:
        return graph.summarize()
    finally:
        await graph.close()


def _switch_payloads(config: SREAgentConfig) -> list[dict[str, Any]]:
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


def _load_lab_seed(config_path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []
    lab_seed = raw.get("lab_seed", {})
    if not isinstance(lab_seed, dict):
        return []
    nodes = lab_seed.get("nodes", [])
    if not isinstance(nodes, list):
        return []
    return [entry for entry in nodes if isinstance(entry, dict)]


def _lab_seed_topology(records: list[dict[str, Any]]) -> tuple[list[OntologyNode], list[OntologyEdge]]:
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
            gpu_value = str(gpu_id).strip()
            if not gpu_value:
                continue
            nodes.append(
                OntologyNode(
                    id=gpu_value,
                    entity_type=EntityType.GPU,
                    name=gpu_value,
                    properties={"host": node_id, "source": raw.get("source", "lab_seed")},
                    status="online",
                    updated_at=now,
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=gpu_value,
                    target_id=node_id,
                    relation=RelationType.PART_OF,
                    properties={},
                )
            )

    return nodes, edges


async def _discover_topology_static(
    config: SREAgentConfig,
    config_path: Path,
    refresh_only: bool,
) -> dict[str, object]:
    graph = OntologyGraph(db_path=config.ontology.db_path)
    await graph.connect()
    try:
        scanner = SwitchScanner(channel=None)
        nodes, edges = await scanner.scan(_switch_payloads(config))
        lab_nodes, lab_edges = _lab_seed_topology(_load_lab_seed(config_path))
        nodes.extend(lab_nodes)
        edges.extend(lab_edges)
        if refresh_only:
            for node in graph.list_entities():
                await graph.remove_node(node.id)
        await graph.add_nodes(nodes)
        await graph.add_edges(edges)
        summary = graph.summarize()
        summary["refresh_only"] = refresh_only
        summary["discovery_mode"] = "static"
        return summary
    finally:
        await graph.close()


async def _discover_topology_live(config: SREAgentConfig, refresh_only: bool) -> dict[str, object]:
    if not refresh_only:
        raise click.ClickException("live discovery requires --refresh-only to avoid mixing stale topology")

    inventory = _load_live_inventory(_LIVE_INVENTORY_PATH)
    try:
        nodes, edges, scanner_counts = await _scan_live_sources(inventory)
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"live discovery failed: {exc}") from exc

    graph = OntologyGraph(db_path=config.ontology.db_path)
    await graph.connect()
    try:
        for node in graph.list_entities():
            await graph.remove_node(node.id)
        await graph.add_nodes(nodes)
        await graph.add_edges(edges)
        summary = graph.summarize()
    finally:
        await graph.close()

    summary["refresh_only"] = True
    summary["discovery_mode"] = "live"
    summary["scanner_counts"] = scanner_counts
    return summary


def _load_live_inventory(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise click.ClickException(f"missing live inventory config: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise click.ClickException(f"live inventory must be a mapping: {path}")

    workers = payload.get("inventory", {}).get("workers", [])
    if not isinstance(workers, list) or not workers:
        raise click.ClickException(
            f"live inventory is missing inventory.workers in {path}"
        )
    for worker in workers:
        if not isinstance(worker, dict):
            raise click.ClickException(f"worker entry must be a mapping in {path}")
        name = str(worker.get("name", "")).strip()
        if not name:
            raise click.ClickException(f"worker entry missing name in {path}")
        redfish = worker.get("redfish")
        if not isinstance(redfish, dict):
            raise click.ClickException(f"worker={name} missing redfish config in {path}")
        for key in ("bmc_host", "username", "password"):
            if not str(redfish.get(key, "")).strip():
                raise click.ClickException(f"worker={name} redfish.{key} is required in {path}")

    monitor = payload.get("monitor", {})
    if not isinstance(monitor, dict):
        raise click.ClickException(f"live inventory monitor section must be a mapping in {path}")
    prometheus_url = str(monitor.get("prometheus_url", "")).strip()
    if not prometheus_url:
        raise click.ClickException(f"monitor.prometheus_url is required in {path}")
    baseline_queries = monitor.get("baseline_queries", {})
    if not isinstance(baseline_queries, dict) or not baseline_queries:
        raise click.ClickException(f"monitor.baseline_queries is required in {path}")
    for required_query in ("cpu_util", "memory_util"):
        if not str(baseline_queries.get(required_query, "")).strip():
            raise click.ClickException(
                f"monitor.baseline_queries.{required_query} is required in {path}"
            )

    switches = payload.get("switches", {})
    if not isinstance(switches, dict) or not switches:
        raise click.ClickException(f"switches mapping is required in {path}")
    for switch_name, switch_cfg in switches.items():
        name = str(switch_name).strip()
        if not name:
            raise click.ClickException(f"switch key cannot be empty in {path}")
        if not isinstance(switch_cfg, dict):
            raise click.ClickException(f"switch={name} config must be a mapping in {path}")
        for key in ("host", "username", "password"):
            if not str(switch_cfg.get(key, "")).strip():
                raise click.ClickException(f"switch={name} missing {key} in {path}")

    return payload


def _summary_counts(nodes: list[OntologyNode], edges: list[OntologyEdge]) -> dict[str, int]:
    return {"nodes": len(nodes), "edges": len(edges)}


def _create_redfish_channel(timeout_sec: int) -> RedfishChannel:
    return RedfishChannel(timeout=timeout_sec)


def _create_k8s_live_channel(kubeconfig: str) -> "_LiveK8sDiscoveryChannel":
    return _LiveK8sDiscoveryChannel(kubeconfig=kubeconfig)


def _create_prometheus_query_channel(base_url: str, query_aliases: dict[str, str]) -> "_LivePrometheusQueryChannel":
    return _LivePrometheusQueryChannel(base_url=base_url, queries=query_aliases)


def _create_switch_channel(devices: dict[str, dict[str, Any]], timeout_sec: int) -> SwitchChannel:
    return SwitchChannel(devices=devices, dry_run=False, timeout=timeout_sec)


class _LiveK8sDiscoveryChannel:
    def __init__(self, kubeconfig: str = "~/.kube/config") -> None:
        self._kubeconfig = kubeconfig

    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            try:
                from kubernetes import client as k8s_client
                from kubernetes import config as k8s_config
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "python package 'kubernetes' is required for live K8s discovery"
                ) from exc

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


async def _scan_live_sources(raw_config: dict[str, Any]) -> tuple[list[OntologyNode], list[OntologyEdge], dict[str, dict[str, int]]]:
    workers: list[dict[str, Any]] = raw_config["inventory"]["workers"]
    switches: dict[str, dict[str, Any]] = raw_config["switches"]
    monitor: dict[str, Any] = raw_config["monitor"]
    worker_names = [str(worker["name"]).strip() for worker in workers]
    representative_worker = worker_names[0]
    worker_node_set = set(worker_names)

    # Seed worker nodes discovered from inventory so relation edges have stable endpoints.
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
        redfish = _create_redfish_channel(timeout_sec)
        try:
            auth = await redfish.authenticate(
                str(redfish_cfg["bmc_host"]),
                str(redfish_cfg["username"]),
                str(redfish_cfg["password"]),
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            if not auth.success:
                raise click.ClickException(
                    f"live BMC auth failed for worker={worker_name} host={redfish_cfg['bmc_host']}: {auth.error}"
                )
            info = await redfish.get_bmc_info(
                str(redfish_cfg["bmc_host"]),
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            if not info.success:
                raise click.ClickException(
                    f"live BMC info query failed for worker={worker_name} host={redfish_cfg['bmc_host']}: {info.error}"
                )
            try:
                info_json = yaml.safe_load(info.output) if info.output else {}
            except Exception as exc:  # noqa: BLE001
                raise click.ClickException(
                    f"live BMC info payload was not valid JSON for worker={worker_name}: {exc}"
                ) from exc
        finally:
            await redfish.close()

        bmc_payloads.append(
            {
                "node_id": worker_name,
                "id": f"bmc:{worker_name}",
                "name": str((info_json or {}).get("manager", {}).get("name") or f"BMC {worker_name}"),
                "ip": str(redfish_cfg["bmc_host"]),
                "firmware_version": (info_json or {}).get("manager", {}).get("firmware_version"),
                "capabilities": (info_json or {}).get("manager", {}).get("actions", []),
                "status": "online",
            }
        )

    bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan(bmc_payloads)

    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip() or "~/.kube/config"
    k8s_live_channel = _create_k8s_live_channel(kubeconfig)
    cluster_name = os.getenv("SRE_K8S_CLUSTER_NAME", "").strip() or "lab-cluster"
    k8s_nodes, k8s_edges = await K8sScanner(channel=k8s_live_channel).scan(
        namespace="default",
        label_selector=None,
        cluster_name=cluster_name,
    )
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
    prom_aliases = {
        "cpu_util": str(baseline_queries["cpu_util"]),
        "memory_util": str(baseline_queries["memory_util"]),
    }
    prom_targets = {
        "cpu_util": representative_worker,
        "memory_util": discovered_k8s_node_ids[0] if discovered_k8s_node_ids else representative_worker,
    }
    prom_channel = _create_prometheus_query_channel(str(monitor["prometheus_url"]), prom_aliases)
    try:
        prom_nodes, prom_edges = await PrometheusScanner(channel=prom_channel).scan(prom_targets)
    finally:
        await prom_channel.close()

    switch_timeout = max(int(item.get("timeout", 30)) for item in switches.values())
    switch_channel = _create_switch_channel(switches, switch_timeout)
    switch_payloads: list[dict[str, Any]] = []
    try:
        for switch_name, switch_cfg in sorted(switches.items()):
            interfaces = switch_channel.get_all_interfaces(switch_name)
            if not interfaces:
                raise click.ClickException(
                    f"live switch interface discovery returned no ports for switch={switch_name}"
                )
            ports: list[dict[str, Any]] = []
            for interface in interfaces[:1]:
                interface_name = str(
                    getattr(interface, "abbreviated_name", "")
                    or getattr(interface, "name", "")
                    or getattr(interface, "if_index", "unknown")
                )
                ports.append(
                    {
                        "id": f"{switch_name}:{interface_name}",
                        "name": interface_name,
                        "status": str(getattr(interface, "oper_status", "unknown")),
                        "connected_to": representative_worker,
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

    all_nodes = base_nodes + k8s_extra_nodes + bmc_nodes + k8s_nodes + prom_nodes + switch_nodes
    all_edges = bmc_edges + k8s_edges + prom_edges + switch_edges
    scanner_counts = {
        "bmc": _summary_counts(bmc_nodes, bmc_edges),
        "k8s": _summary_counts(k8s_nodes, k8s_edges),
        "prometheus": _summary_counts(prom_nodes, prom_edges),
        "switch": _summary_counts(switch_nodes, switch_edges),
    }
    return all_nodes, all_edges, scanner_counts


async def _discover_topology(
    config: SREAgentConfig,
    config_path: Path,
    refresh_only: bool,
    mode: Literal["static", "live"],
) -> dict[str, object]:
    if mode == "live":
        return await _discover_topology_live(config, refresh_only)
    return await _discover_topology_static(config, config_path, refresh_only)


def _format_discovery_summary(config: SREAgentConfig, summary: dict[str, object]) -> str:
    mode = "refresh" if summary.get("refresh_only") else "full"
    discovery_mode = summary.get("discovery_mode", "static")
    lines = [
        "Discovery Complete",
        f"AIDC: {config.global_.aidc_id}",
        f"Mode: {mode}",
        f"Source Mode: {discovery_mode}",
        f"DB: {config.ontology.db_path}",
        f"Nodes: {summary['node_count']}",
        f"Edges: {summary['edge_count']}",
    ]
    scanner_counts = summary.get("scanner_counts")
    if isinstance(scanner_counts, dict) and scanner_counts:
        lines.append("Scanner Counts:")
        for scanner_name, counts in scanner_counts.items():
            if isinstance(counts, dict):
                node_count = int(counts.get("nodes", 0) or 0)
                edge_count = int(counts.get("edges", 0) or 0)
                lines.append(f"- {scanner_name}: nodes={node_count}, edges={edge_count}")
    return "\n".join(lines)


async def _load_incidents(config: SREAgentConfig, last: int) -> list[IncidentRecord]:
    store = create_memory_store(
        config.global_.aidc_id,
        db_dir=config.memory.db_dir,
    )
    await store.connect()
    try:
        return await store.list_recent(last=last)
    finally:
        await store.close()


async def _load_patterns(config: SREAgentConfig) -> list[dict[str, object]]:
    store = create_memory_store(
        config.global_.aidc_id,
        db_dir=config.memory.db_dir,
    )
    await store.connect()
    try:
        patterns = await store.get_known_patterns(
            min_occurrence=config.memory.pattern_min_occurrences,
            min_effective_confidence=config.memory.pattern_min_confidence,
        )
    finally:
        await store.close()

    return [
        {
            "pattern_id": pattern.pattern_id,
            "last_seen": pattern.last_seen.isoformat(),
            "occurrence_count": pattern.occurrence_count,
            "effective_confidence": round(pattern.effective_confidence(), 3),
            "root_cause": pattern.root_cause,
            "symptom_signature": ",".join(pattern.symptom_signature),
        }
        for pattern in patterns
    ]


def _format_incident_history(config: SREAgentConfig, incidents: list[IncidentRecord], last: int) -> str:
    lines = [
        "Incident History",
        f"AIDC: {config.global_.aidc_id}",
        f"DB Dir: {config.memory.db_dir}",
        f"Last: {last}",
        f"Count: {len(incidents)}",
    ]
    if not incidents:
        lines.append("- none")
        return "\n".join(lines)

    for incident in incidents:
        lines.append(
            " | ".join(
                [
                    incident.timestamp.isoformat(),
                    incident.incident_id,
                    incident.alert.alert_name,
                    incident.outcome,
                    incident.root_cause,
                ]
            )
        )
    return "\n".join(lines)


def _format_pattern_history(config: SREAgentConfig, patterns: list[dict[str, object]]) -> str:
    lines = [
        "Learned Patterns",
        f"AIDC: {config.global_.aidc_id}",
        f"DB Dir: {config.memory.db_dir}",
        f"Min Occurrences: {config.memory.pattern_min_occurrences}",
        f"Min Confidence: {config.memory.pattern_min_confidence}",
        f"Count: {len(patterns)}",
    ]
    if not patterns:
        lines.append("- none")
        return "\n".join(lines)

    for pattern in patterns:
        lines.append(
            " | ".join(
                [
                    str(pattern["last_seen"]),
                    str(pattern["pattern_id"]),
                    f"occurrences={pattern['occurrence_count']}",
                    f"confidence={pattern['effective_confidence']}",
                    str(pattern["root_cause"]),
                    str(pattern["symptom_signature"]),
                ]
            )
        )
    return "\n".join(lines)


async def _ingest_knowledge_path(
    config: SREAgentConfig,
    path: Path,
    category: str,
    version: str,
) -> KnowledgeIngestSummary:
    store = KnowledgeStore(persist_dir=config.knowledge_base.persist_dir)
    ingestor = KnowledgeIngestor(store)
    return await ingestor.ingest_path(path=path, category=category, version=version)


def _format_knowledge_ingest_summary(summary: KnowledgeIngestSummary) -> str:
    lines = [
        "Knowledge Ingest Complete",
        f"Path: {summary.source_path}",
        f"Category: {summary.category}",
        f"Files Ingested: {summary.file_count}",
        f"Chunks Written: {summary.chunk_count}",
        f"Skipped Files: {summary.skipped_files}",
    ]
    return "\n".join(lines)


async def _search_knowledge(
    config: SREAgentConfig,
    query: str,
    category: str | None,
    top_k: int,
) -> list[dict[str, object]]:
    store = KnowledgeStore(persist_dir=config.knowledge_base.persist_dir)
    results = await store.search(query=query, category=category, top_k=top_k)
    return [
        {
            "score": round(item.score, 3),
            "source": item.source,
            "category": item.category,
            "content": item.content.replace("\n", " ").strip(),
        }
        for item in results
    ]


def _format_knowledge_search_results(
    config: SREAgentConfig,
    query: str,
    category: str | None,
    results: list[dict[str, object]],
) -> str:
    lines = [
        "Knowledge Search Results",
        f"Query: {query}",
        f"DB Dir: {config.knowledge_base.persist_dir}",
        f"Category: {category or 'all'}",
        f"Count: {len(results)}",
    ]
    if not results:
        lines.append("- none")
        return "\n".join(lines)

    for result in results:
        lines.append(
            " | ".join(
                [
                    f"score={result['score']}",
                    str(result["category"]),
                    str(result["source"]),
                    str(result["content"]),
                ]
            )
        )
    return "\n".join(lines)


@click.group()
def main() -> None:
    """AIDC Auto-SRE CLI."""


@main.command("topology")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def topology_command(config_path: Path) -> None:
    """Show current topology summary."""
    config = load_config(config_path)
    summary = asyncio.run(_load_topology_summary(config))
    click.echo(_format_topology_summary(config, summary))


@main.command("discover")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--mode",
    "discovery_mode",
    type=click.Choice(["static", "live"]),
    default="static",
    show_default=True,
    help="Use static config-defined topology seeds or live scanner discovery.",
)
@click.option("--refresh-only", is_flag=True, default=False, help="Replace existing topology data before discovery.")
def discover_command(config_path: Path, discovery_mode: str, refresh_only: bool) -> None:
    """Discover topology from configured sources."""
    config = load_config(config_path)
    summary = asyncio.run(_discover_topology(config, config_path, refresh_only, discovery_mode))
    click.echo(_format_discovery_summary(config, summary))


@main.command("serve")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--host", default="127.0.0.1", show_default=True, type=str)
@click.option("--port", default=8000, show_default=True, type=click.IntRange(1, 65535))
@click.option("--log-level", default="info", show_default=True, type=click.Choice(["critical", "error", "warning", "info", "debug", "trace"]))
def serve_command(config_path: Path, host: str, port: int, log_level: str) -> None:
    """Start the FastAPI API + WS server with default dependency wiring."""
    config = load_config(config_path)
    applied_env = apply_llm_env_from_config(config, os.environ, only_if_missing=True)
    if applied_env:
        LOGGER.info(
            "loaded llm settings from config into environment: keys=%s",
            sorted(applied_env.keys()),
        )
    app = create_app(config=config)
    click.echo(f"Starting sre-agent serve on {host}:{port} with config={config_path}")
    LOGGER.info("serve startup: aidc_id=%s host=%s port=%s", config.global_.aidc_id, host, port)
    uvicorn.run(app, host=host, port=port, log_level=log_level)


@main.group("memory")
def memory_group() -> None:
    """Inspect persisted incident and pattern memory."""


@memory_group.command("incidents")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--last", type=click.IntRange(1), default=10, show_default=True, help="Show the most recent N incidents.")
def memory_incidents_command(config_path: Path, last: int) -> None:
    """Show recent incident history."""
    config = load_config(config_path)
    incidents = asyncio.run(_load_incidents(config, last))
    click.echo(_format_incident_history(config, incidents, last))


@memory_group.command("patterns")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
def memory_patterns_command(config_path: Path) -> None:
    """Show learned patterns that are ready to suggest."""
    config = load_config(config_path)
    patterns = asyncio.run(_load_patterns(config))
    click.echo(_format_pattern_history(config, patterns))


@main.group("knowledge")
def knowledge_group() -> None:
    """Manage and inspect the knowledge base."""


@knowledge_group.command("ingest")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--path", "knowledge_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--category", required=True, type=str)
@click.option("--version", default="v1", show_default=True, type=str)
def knowledge_ingest_command(config_path: Path, knowledge_path: Path, category: str, version: str) -> None:
    """Ingest text knowledge documents from a file or directory."""
    config = load_config(config_path)
    summary = asyncio.run(_ingest_knowledge_path(config, knowledge_path, category, version))
    click.echo(_format_knowledge_ingest_summary(summary))


@knowledge_group.command("search")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--query", required=True, type=str)
@click.option("--category", default=None, type=str)
@click.option("--top-k", default=5, show_default=True, type=click.IntRange(1))
def knowledge_search_command(config_path: Path, query: str, category: str | None, top_k: int) -> None:
    """Search the knowledge base."""
    config = load_config(config_path)
    results = asyncio.run(_search_knowledge(config, query, category, top_k))
    click.echo(_format_knowledge_search_results(config, query, category, results))


if __name__ == "__main__":
    main()
