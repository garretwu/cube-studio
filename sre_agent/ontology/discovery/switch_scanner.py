"""Switch topology scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


class SwitchScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def scan(self, switches: list[dict[str, Any]] | None = None) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        records = switches or []
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        for switch in records:
            switch_id = switch["id"]
            nodes.append(
                OntologyNode(
                    id=switch_id,
                    entity_type=EntityType.SWITCH,
                    name=switch.get("name", switch_id),
                    properties={k: v for k, v in switch.items() if k not in {"id", "ports"}},
                    status=switch.get("status", "online"),
                    updated_at=datetime.now(UTC),
                )
            )
            for port in switch.get("ports", []):
                port_id = port.get("id") or f"{switch_id}:{port['name']}"
                nodes.append(
                    OntologyNode(
                        id=port_id,
                        entity_type=EntityType.SWITCH_PORT,
                        name=port.get("name", port_id),
                        properties={k: v for k, v in port.items() if k not in {"id", "connected_to"}},
                        status=port.get("status", "up"),
                        updated_at=datetime.now(UTC),
                    )
                )
                edges.append(
                    OntologyEdge(source_id=port_id, target_id=switch_id, relation=RelationType.PART_OF, properties={})
                )
                if port.get("connected_to"):
                    edges.append(
                        OntologyEdge(
                            source_id=port_id,
                            target_id=port["connected_to"],
                            relation=RelationType.CONNECTED_TO,
                            properties={},
                        )
                    )
        return nodes, edges
