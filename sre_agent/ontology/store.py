"""Higher-level ontology persistence facade."""
from __future__ import annotations

from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.graph import OntologyGraph


class OntologyStore:
    def __init__(self, graph: OntologyGraph | None = None, db_path: str = ":memory:") -> None:
        self.graph = graph or OntologyGraph(db_path=db_path)

    async def connect(self) -> None:
        await self.graph.connect()

    async def close(self) -> None:
        await self.graph.close()

    async def save_snapshot(self, nodes: list[OntologyNode], edges: list[OntologyEdge]) -> None:
        await self.graph.add_nodes(nodes)
        await self.graph.add_edges(edges)

    async def upsert_entity(self, node: OntologyNode) -> None:
        await self.graph.add_node(node)

    async def upsert_relationship(self, edge: OntologyEdge) -> None:
        await self.graph.add_edge(edge)

    def get_entity(self, entity_id: str) -> OntologyNode | None:
        return self.graph.get_entity(entity_id)

    def query(self, entity_type: EntityType | str | None = None, filters: dict[str, Any] | None = None) -> list[OntologyNode]:
        return self.graph.find_entities(entity_type=entity_type, filters=filters)

    def neighbors(self, entity_id: str, relation: RelationType | str | None = None) -> list[dict[str, Any]]:
        return self.graph.get_neighbors(entity_id, relation=relation)

    def path(self, from_id: str, to_id: str) -> list[str] | None:
        return self.graph.get_path(from_id, to_id)
