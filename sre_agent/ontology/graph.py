"""Ontology graph storage backed by a NetworkX-style in-memory graph and sqlite persistence."""
from __future__ import annotations

import json
import os
import sqlite3
from collections import deque
from datetime import UTC, datetime
from typing import Any

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType

try:
    import aiosqlite  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import aiosqlite

try:
    import networkx as nx  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import networkx as nx


class OntologyGraph:
    """Digital twin graph with NetworkX-style traversal and sqlite persistence."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._db: Any | None = None
        self.graph = nx.DiGraph()

    async def connect(self) -> None:
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = sqlite3.Row
        await self._init_tables()
        await self._load_from_db()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _init_tables(self) -> None:
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS ontology_nodes (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT,
                properties_json TEXT NOT NULL,
                status TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ontology_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                properties_json TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id, relation)
            );
            """
        )
        await self._db.commit()

    async def _load_from_db(self) -> None:
        assert self._db is not None
        self.graph = nx.DiGraph()

        node_rows = await (await self._db.execute("SELECT * FROM ontology_nodes")).fetchall()
        for row in node_rows:
            node = OntologyNode(
                id=row["id"],
                entity_type=EntityType(row["entity_type"]),
                name=row["name"],
                properties=json.loads(row["properties_json"] or "{}"),
                status=row["status"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            self._add_node_to_graph(node)

        edge_rows = await (await self._db.execute("SELECT * FROM ontology_edges")).fetchall()
        for row in edge_rows:
            edge = OntologyEdge(
                source_id=row["source_id"],
                target_id=row["target_id"],
                relation=RelationType(row["relation"]),
                properties=json.loads(row["properties_json"] or "{}"),
            )
            self._add_edge_to_graph(edge)

    def _add_node_to_graph(self, node: OntologyNode) -> None:
        self.graph.add_node(
            node.id,
            entity=node,
            type=node.entity_type.value,
            data=node.model_dump(mode="json"),
        )

    def _add_edge_to_graph(self, edge: OntologyEdge) -> None:
        self.graph.add_edge(
            edge.source_id,
            edge.target_id,
            edge=edge,
            relation=edge.relation,
            properties=dict(edge.properties),
        )

    async def add_node(self, node: OntologyNode) -> None:
        self._add_node_to_graph(node)
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO ontology_nodes(id, entity_type, name, properties_json, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entity_type=excluded.entity_type,
                name=excluded.name,
                properties_json=excluded.properties_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                node.id,
                node.entity_type.value,
                node.name,
                json.dumps(node.properties, sort_keys=True),
                node.status,
                node.updated_at.isoformat(),
            ),
        )
        await self._db.commit()

    async def add_entity(self, entity: OntologyNode) -> None:
        await self.add_node(entity)

    async def add_nodes(self, nodes: list[OntologyNode]) -> None:
        for node in nodes:
            await self.add_node(node)

    async def add_edge(self, edge: OntologyEdge) -> None:
        self._add_edge_to_graph(edge)
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO ontology_edges(source_id, target_id, relation, properties_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                properties_json=excluded.properties_json
            """,
            (edge.source_id, edge.target_id, edge.relation.value, json.dumps(edge.properties, sort_keys=True)),
        )
        await self._db.commit()

    async def add_relationship(self, relationship: OntologyEdge) -> None:
        await self.add_edge(relationship)

    async def add_edges(self, edges: list[OntologyEdge]) -> None:
        for edge in edges:
            await self.add_edge(edge)

    async def remove_node(self, entity_id: str) -> None:
        if hasattr(self.graph, "has_node") and self.graph.has_node(entity_id):
            self.graph.remove_node(entity_id)
        assert self._db is not None
        await self._db.execute("DELETE FROM ontology_nodes WHERE id = ?", (entity_id,))
        await self._db.execute("DELETE FROM ontology_edges WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
        await self._db.commit()

    async def remove_edge(self, source_id: str, target_id: str, relation: RelationType) -> None:
        current = self._edge_between(source_id, target_id)
        if current is not None and current.relation == relation:
            self.graph.remove_edge(source_id, target_id)
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM ontology_edges WHERE source_id = ? AND target_id = ? AND relation = ?",
            (source_id, target_id, relation.value),
        )
        await self._db.commit()

    def _node_entity(self, entity_id: str) -> OntologyNode | None:
        payload = self.graph.nodes.get(entity_id)
        if not payload:
            return None
        entity = payload.get("entity")
        return entity if isinstance(entity, OntologyNode) else None

    def _edge_between(self, source_id: str, target_id: str) -> OntologyEdge | None:
        for _, edge_target, data in self.graph.out_edges(source_id, data=True):
            if edge_target != target_id:
                continue
            edge = data.get("edge")
            if isinstance(edge, OntologyEdge):
                return edge
        return None

    def get_entity(self, entity_id: str) -> OntologyNode | None:
        return self._node_entity(entity_id)

    def list_entities(self) -> list[OntologyNode]:
        entities = [self._node_entity(node_id) for node_id in self.graph.nodes]
        return sorted([entity for entity in entities if entity is not None], key=lambda item: item.id)

    def list_edges(self) -> list[OntologyEdge]:
        edges: list[OntologyEdge] = []
        for source_id, target_id, data in self.graph.edges(data=True):
            edge = data.get("edge")
            if isinstance(edge, OntologyEdge):
                edges.append(edge)
            else:
                edges.append(
                    OntologyEdge(
                        source_id=source_id,
                        target_id=target_id,
                        relation=data["relation"],
                        properties=data.get("properties", {}),
                    )
                )
        return sorted(edges, key=lambda item: (item.source_id, item.target_id, item.relation.value))

    def summarize(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entity in self.list_entities():
            counts[entity.entity_type.value] = counts.get(entity.entity_type.value, 0) + 1
        return {
            "node_count": len(self.list_entities()),
            "edge_count": len(self.list_edges()),
            "entity_type_counts": dict(sorted(counts.items())),
        }

    def find_entities(
        self,
        entity_type: EntityType | str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[OntologyNode]:
        expected_type = EntityType(entity_type) if isinstance(entity_type, str) else entity_type
        matches: list[OntologyNode] = []
        for entity in self.list_entities():
            if expected_type is not None and entity.entity_type != expected_type:
                continue
            if filters and not self._matches_filters(entity, filters):
                continue
            matches.append(entity)
        return sorted(matches, key=lambda item: item.id)

    def _matches_filters(self, node: OntologyNode, filters: dict[str, Any]) -> bool:
        for key, expected in filters.items():
            if key == "id" and node.id != expected:
                return False
            if key == "name" and node.name != expected:
                return False
            if key == "status" and node.status != expected:
                return False
            if key not in {"id", "name", "status"} and node.properties.get(key) != expected:
                return False
        return True

    def get_neighbors(
        self,
        entity_id: str,
        relation: RelationType | str | None = None,
    ) -> list[dict[str, Any]]:
        relation_filter = RelationType(relation) if isinstance(relation, str) else relation
        neighbors: list[dict[str, Any]] = []
        for _, target_id, data in self.graph.out_edges(entity_id, data=True):
            edge = data.get("edge")
            if not isinstance(edge, OntologyEdge):
                continue
            if relation_filter is not None and edge.relation != relation_filter:
                continue
            entity = self._node_entity(target_id)
            if entity is not None:
                neighbors.append({"direction": "out", "entity": entity, "relation": edge})
        for source_id, _, data in self.graph.in_edges(entity_id, data=True):
            edge = data.get("edge")
            if not isinstance(edge, OntologyEdge):
                continue
            if relation_filter is not None and edge.relation != relation_filter:
                continue
            entity = self._node_entity(source_id)
            if entity is not None:
                neighbors.append({"direction": "in", "entity": entity, "relation": edge})
        return neighbors

    def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        try:
            return list(nx.shortest_path(self.graph, from_id, to_id))
        except (nx.NetworkXNoPath, getattr(nx, "NodeNotFound", nx.NetworkXNoPath)):
            return None

    def get_blast_radius(self, entity_id: str, max_depth: int = 3) -> dict[str, Any]:
        propagate_via_out = {RelationType.HOSTED_ON, RelationType.PART_OF}
        propagate_via_in = {RelationType.SERVES, RelationType.DEPENDS_ON, RelationType.HOSTED_ON}

        affected: list[OntologyNode] = []
        visited = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue

            for _, target_id, data in self.graph.out_edges(current, data=True):
                edge = data.get("edge")
                if not isinstance(edge, OntologyEdge) or edge.relation not in propagate_via_out:
                    continue
                if target_id in visited:
                    continue
                visited.add(target_id)
                entity = self._node_entity(target_id)
                if entity is not None:
                    affected.append(entity)
                queue.append((target_id, depth + 1))

            for source_id, _, data in self.graph.in_edges(current, data=True):
                edge = data.get("edge")
                if not isinstance(edge, OntologyEdge) or edge.relation not in propagate_via_in:
                    continue
                if source_id in visited:
                    continue
                visited.add(source_id)
                entity = self._node_entity(source_id)
                if entity is not None:
                    affected.append(entity)
                queue.append((source_id, depth + 1))

        return {
            "root_entity_id": entity_id,
            "affected_entities": affected,
            "affected_count": len(affected),
        }

    async def refresh_entity(self, node: OntologyNode) -> OntologyNode:
        refreshed = node.model_copy(update={"updated_at": datetime.now(UTC)})
        await self.add_node(refreshed)
        return refreshed
