"""Click CLI entrypoint for the SRE agent."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

import click

from sre_agent.config import SREAgentConfig, load_config
from sre_agent.knowledge.ingest import KnowledgeIngestSummary, KnowledgeIngestor
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.memory import IncidentRecord
from sre_agent.ontology.discovery.switch_scanner import SwitchScanner
from sre_agent.ontology.graph import OntologyGraph


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
        payloads.append(
            {
                "id": switch.id or switch.name,
                "name": switch.name,
                "type": switch.type,
                "status": switch.status,
                "ports": [
                    {
                        "id": port.id,
                        "name": port.name,
                        "connected_to": port.connected_to,
                        "status": port.status,
                        "speed_gbps": port.speed_gbps,
                    }
                    for port in switch.ports
                ],
            }
        )
    return payloads


async def _discover_topology_static(config: SREAgentConfig, refresh_only: bool) -> dict[str, object]:
    graph = OntologyGraph(db_path=config.ontology.db_path)
    await graph.connect()
    try:
        scanner = SwitchScanner(channel=None)
        nodes, edges = await scanner.scan(_switch_payloads(config))
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
    _ = config, refresh_only
    raise click.ClickException(
        "live discovery is not wired yet; current discover only supports --mode static "
        "from config-defined topology seeds"
    )


async def _discover_topology(
    config: SREAgentConfig,
    refresh_only: bool,
    mode: Literal["static", "live"],
) -> dict[str, object]:
    if mode == "live":
        return await _discover_topology_live(config, refresh_only)
    return await _discover_topology_static(config, refresh_only)


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
    summary = asyncio.run(_discover_topology(config, refresh_only, discovery_mode))
    click.echo(_format_discovery_summary(config, summary))


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
