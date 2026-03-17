"""BMC-to-ontology scanner."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType


class BMCScanner:
    def __init__(self, channel: Any):
        self.channel = channel

    async def scan(self, systems: list[dict[str, Any]] | None = None) -> tuple[list[OntologyNode], list[OntologyEdge]]:
        records = systems or []
        nodes: list[OntologyNode] = []
        edges: list[OntologyEdge] = []
        for item in records:
            node_id = item["node_id"]
            bmc_id = item.get("id") or f"bmc:{node_id}"
            nodes.append(
                OntologyNode(
                    id=bmc_id,
                    entity_type=EntityType.BMC_ENDPOINT,
                    name=item.get("name"),
                    properties={
                        "ip": item.get("ip"),
                        "firmware_version": item.get("firmware_version"),
                        "capabilities": item.get("capabilities", []),
                    },
                    status=item.get("status", "online"),
                    updated_at=datetime.now(UTC),
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
        return nodes, edges
