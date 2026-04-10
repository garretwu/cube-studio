"""Click CLI entrypoint for the SRE agent."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Literal

import click
import uvicorn
from sre_agent.config import SREAgentConfig, apply_llm_env_from_config, load_config
from sre_agent.knowledge.ingest import KnowledgeIngestSummary, KnowledgeIngestor
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.memory import IncidentRecord
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.server import create_app
from sre_agent.topology import discovery as topology_discovery

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


async def _discover_topology_static(
    config: SREAgentConfig,
    config_path: Path,
    refresh_only: bool,
) -> dict[str, object]:
    graph = OntologyGraph(db_path=config.ontology.db_path)
    await graph.connect()
    try:
        nodes, edges, scanner_counts = await topology_discovery.discover_static_snapshot(config)
        if refresh_only:
            for node in graph.list_entities():
                await graph.remove_node(node.id)
        await graph.add_nodes(nodes)
        await graph.add_edges(edges)
        summary = graph.summarize()
        summary["refresh_only"] = refresh_only
        summary["discovery_mode"] = "static"
        summary["scanner_counts"] = scanner_counts
        return summary
    finally:
        await graph.close()


def _resolve_cli_live_inventory_path(config: SREAgentConfig) -> Path:
    configured_path = str(config.ontology.discovery.live_inventory_path or "").strip()
    default_path = SREAgentConfig().ontology.discovery.live_inventory_path
    if not configured_path or configured_path == default_path:
        return _LIVE_INVENTORY_PATH
    return Path(configured_path)


def _load_live_inventory(path: Path) -> dict[str, Any]:
    try:
        payload = topology_discovery.load_live_inventory(path)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc

    baseline_queries = payload.get("monitor", {}).get("baseline_queries", {})
    if not isinstance(baseline_queries, dict) or not baseline_queries:
        raise click.ClickException(f"monitor.baseline_queries is required in {path}")
    for required_query in ("cpu_util", "memory_util"):
        if not str(baseline_queries.get(required_query, "")).strip():
            raise click.ClickException(
                f"monitor.baseline_queries.{required_query} is required in {path}"
            )
    return payload


async def _scan_live_sources(
    config: SREAgentConfig,
    raw_config: dict[str, Any],
) -> tuple[list[Any], list[Any], dict[str, dict[str, int]]]:
    discovery_config = config.ontology.discovery
    try:
        return await topology_discovery.scan_live_sources(
            raw_config,
            k8s_namespaces=list(discovery_config.k8s_namespaces),
            k8s_cluster_name=str(discovery_config.k8s_cluster_name).strip() or "lab-cluster",
            prometheus_targets=dict(discovery_config.prometheus_targets),
        )
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"live discovery failed: {exc}") from exc


async def _discover_topology_live(config: SREAgentConfig, refresh_only: bool) -> dict[str, object]:
    if not refresh_only:
        raise click.ClickException("live discovery requires --refresh-only to avoid mixing stale topology")

    inventory = _load_live_inventory(_resolve_cli_live_inventory_path(config))
    nodes, edges, scanner_counts = await _scan_live_sources(config, inventory)

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
