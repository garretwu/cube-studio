"""Click CLI entrypoint for the SRE agent."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import click

from sre_agent.config import SREAgentConfig, load_config
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


async def _discover_topology(config: SREAgentConfig, refresh_only: bool) -> dict[str, object]:
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
        return summary
    finally:
        await graph.close()


def _format_discovery_summary(config: SREAgentConfig, summary: dict[str, object]) -> str:
    mode = "refresh" if summary.get("refresh_only") else "full"
    lines = [
        "Discovery Complete",
        f"AIDC: {config.global_.aidc_id}",
        f"Mode: {mode}",
        f"DB: {config.ontology.db_path}",
        f"Nodes: {summary['node_count']}",
        f"Edges: {summary['edge_count']}",
    ]
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
@click.option("--refresh-only", is_flag=True, default=False, help="Replace existing topology data before discovery.")
def discover_command(config_path: Path, refresh_only: bool) -> None:
    """Discover topology from configured sources."""
    config = load_config(config_path)
    summary = asyncio.run(_discover_topology(config, refresh_only))
    click.echo(_format_discovery_summary(config, summary))


if __name__ == "__main__":
    main()
