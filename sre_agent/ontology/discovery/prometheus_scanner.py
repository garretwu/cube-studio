"""Prometheus metric-target scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


class PrometheusScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def scan(self, targets: dict[str, str]) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        for metric_name, entity_id in targets.items():
            value = await self.channel.query_instant(metric_name)
            metric_id = f"metric:{metric_name}:{entity_id}"
            nodes.append(
                OntologyNode(
                    id=metric_id,
                    entity_type=EntityType.METRIC_ENDPOINT,
                    name=metric_name,
                    properties={"metric": metric_name, "latest_value": value},
                    status="fresh",
                    updated_at=datetime.now(UTC),
                )
            )
            edges.append(
                OntologyEdge(
                    source_id=metric_id,
                    target_id=entity_id,
                    relation=RelationType.MONITORS,
                    properties={"metric": metric_name},
                )
            )
        return nodes, edges
