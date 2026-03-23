"""Ontology graph storage backed by in-memory adjacency and sqlite persistence."""
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


class OntologyGraph:
    """In-memory graph with sqlite persistence and query helpers."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._db: Any | None = None
        self._nodes: dict[str, OntologyNode] = {}
        self._out_edges: dict[str, list[OntologyEdge]] = {}
        self._in_edges: dict[str, list[OntologyEdge]] = {}

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
        self._nodes.clear()
        self._out_edges.clear()
        self._in_edges.clear()

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
            self._nodes[node.id] = node

        edge_rows = await (await self._db.execute("SELECT * FROM ontology_edges")).fetchall()
        for row in edge_rows:
            self._index_edge(
                OntologyEdge(
                    source_id=row["source_id"],
                    target_id=row["target_id"],
                    relation=RelationType(row["relation"]),
                    properties=json.loads(row["properties_json"] or "{}"),
                )
            )

    def _index_edge(self, edge: OntologyEdge) -> None:
        self._out_edges.setdefault(edge.source_id, [])
        self._out_edges[edge.source_id] = [
            existing for existing in self._out_edges[edge.source_id]
            if not (
                existing.source_id == edge.source_id
                and existing.target_id == edge.target_id
                and existing.relation == edge.relation
            )
        ]
        self._out_edges[edge.source_id].append(edge)

        self._in_edges.setdefault(edge.target_id, [])
        self._in_edges[edge.target_id] = [
            existing for existing in self._in_edges[edge.target_id]
            if not (
                existing.source_id == edge.source_id
                and existing.target_id == edge.target_id
                and existing.relation == edge.relation
            )
        ]
        self._in_edges[edge.target_id].append(edge)

    async def add_node(self, node: OntologyNode) -> None:
        self._nodes[node.id] = node
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

    async def add_nodes(self, nodes: list[OntologyNode]) -> None:
        for node in nodes:
            await self.add_node(node)

    async def add_edge(self, edge: OntologyEdge) -> None:
        self._index_edge(edge)
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

    async def add_edges(self, edges: list[OntologyEdge]) -> None:
        for edge in edges:
            await self.add_edge(edge)

    async def remove_node(self, entity_id: str) -> None:
        self._nodes.pop(entity_id, None)
        self._out_edges.pop(entity_id, None)
        self._in_edges.pop(entity_id, None)
        for bucket in self._out_edges.values():
            bucket[:] = [edge for edge in bucket if edge.target_id != entity_id]
        for bucket in self._in_edges.values():
            bucket[:] = [edge for edge in bucket if edge.source_id != entity_id]
        assert self._db is not None
        await self._db.execute("DELETE FROM ontology_nodes WHERE id = ?", (entity_id,))
        await self._db.execute("DELETE FROM ontology_edges WHERE source_id = ? OR target_id = ?", (entity_id, entity_id))
        await self._db.commit()

    async def remove_edge(self, source_id: str, target_id: str, relation: RelationType) -> None:
        if source_id in self._out_edges:
            self._out_edges[source_id] = [
                edge for edge in self._out_edges[source_id]
                if not (edge.target_id == target_id and edge.relation == relation)
            ]
        if target_id in self._in_edges:
            self._in_edges[target_id] = [
                edge for edge in self._in_edges[target_id]
                if not (edge.source_id == source_id and edge.relation == relation)
            ]
        assert self._db is not None
        await self._db.execute(
            "DELETE FROM ontology_edges WHERE source_id = ? AND target_id = ? AND relation = ?",
            (source_id, target_id, relation.value),
        )
        await self._db.commit()

    def get_entity(self, entity_id: str) -> OntologyNode | None:
        return self._nodes.get(entity_id)

    def list_entities(self) -> list[OntologyNode]:
        return sorted(self._nodes.values(), key=lambda item: item.id)

    def list_edges(self) -> list[OntologyEdge]:
        edges: list[OntologyEdge] = []
        for source_id in sorted(self._out_edges):
            edges.extend(
                sorted(
                    self._out_edges[source_id],
                    key=lambda item: (item.source_id, item.target_id, item.relation.value),
                )
            )
        return edges

    def summarize(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self._nodes.values():
            counts[node.entity_type.value] = counts.get(node.entity_type.value, 0) + 1
        return {
            "node_count": len(self._nodes),
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
        for node in self._nodes.values():
            if expected_type is not None and node.entity_type != expected_type:
                continue
            if filters and not self._matches_filters(node, filters):
                continue
            matches.append(node)
        return sorted(matches, key=lambda item: item.id)

    def _matches_filters(self, node: OntologyNode, filters: dict[str, Any]) -> bool:
        for key, expected in filters.items():
            if key == "id" and node.id != expected:
                return False
            elif key == "name" and node.name != expected:
                return False
            elif key == "status" and node.status != expected:
                return False
            elif node.properties.get(key) != expected:
                return False
        return True

    def get_neighbors(
        self,
        entity_id: str,
        relation: RelationType | str | None = None,
    ) -> list[dict[str, Any]]:
        relation_filter = RelationType(relation) if isinstance(relation, str) else relation
        neighbors: list[dict[str, Any]] = []
        for edge in self._out_edges.get(entity_id, []):
            if relation_filter is not None and edge.relation != relation_filter:
                continue
            target = self._nodes.get(edge.target_id)
            if target is not None:
                neighbors.append({"direction": "out", "entity": target, "relation": edge})
        for edge in self._in_edges.get(entity_id, []):
            if relation_filter is not None and edge.relation != relation_filter:
                continue
            source = self._nodes.get(edge.source_id)
            if source is not None:
                neighbors.append({"direction": "in", "entity": source, "relation": edge})
        return neighbors

    def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        if from_id == to_id and from_id in self._nodes:
            return [from_id]
        visited = {from_id}
        queue: deque[tuple[str, list[str]]] = deque([(from_id, [from_id])])
        while queue:
            current, path = queue.popleft()
            next_ids = [edge.target_id for edge in self._out_edges.get(current, [])]
            next_ids.extend(edge.source_id for edge in self._in_edges.get(current, []))
            for next_id in next_ids:
                if next_id in visited:
                    continue
                next_path = path + [next_id]
                if next_id == to_id:
                    return next_path
                visited.add(next_id)
                queue.append((next_id, next_path))
        return None

    def get_blast_radius(self, entity_id: str, max_depth: int = 3) -> dict[str, Any]:
        affected: list[OntologyNode] = []
        visited = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for neighbor in self.get_neighbors(current):
                node = neighbor["entity"]
                if node.id in visited:
                    continue
                visited.add(node.id)
                affected.append(node)
                queue.append((node.id, depth + 1))
        return {
            "root_entity_id": entity_id,
            "affected_entities": affected,
            "affected_count": len(affected),
        }

    async def refresh_entity(self, node: OntologyNode) -> OntologyNode:
        refreshed = node.model_copy(update={"updated_at": datetime.now(UTC)})
        await self.add_node(refreshed)
        return refreshed
