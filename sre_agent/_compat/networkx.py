"""Small subset of the NetworkX API used by the ontology graph."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator


class NetworkXNoPath(Exception):
    """Raised when no path exists between two nodes."""


@dataclass
class _NodeView:
    _graph: "DiGraph"

    def get(self, node_id: str, default: Any = None) -> Any:
        return self._graph._nodes.get(node_id, default)

    def __call__(self, data: bool = False) -> list[Any]:
        if data:
            return [(node_id, dict(attrs)) for node_id, attrs in self._graph._nodes.items()]
        return list(self._graph._nodes)

    def __iter__(self) -> Iterator[str]:
        return iter(self._graph._nodes)

    def __len__(self) -> int:
        return len(self._graph._nodes)


class DiGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._out_edges: dict[str, dict[str, dict[str, Any]]] = {}
        self._in_edges: dict[str, dict[str, dict[str, Any]]] = {}
        self.nodes = _NodeView(self)

    def add_node(self, node_id: str, **attrs: Any) -> None:
        self._nodes[node_id] = dict(attrs)

    def add_edge(self, source_id: str, target_id: str, **attrs: Any) -> None:
        self._out_edges.setdefault(source_id, {})[target_id] = dict(attrs)
        self._in_edges.setdefault(target_id, {})[source_id] = dict(attrs)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def remove_node(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)
        for target_id in list(self._out_edges.get(node_id, {})):
            self._in_edges.get(target_id, {}).pop(node_id, None)
        for source_id in list(self._in_edges.get(node_id, {})):
            self._out_edges.get(source_id, {}).pop(node_id, None)
        self._out_edges.pop(node_id, None)
        self._in_edges.pop(node_id, None)

    def remove_edge(self, source_id: str, target_id: str) -> None:
        self._out_edges.get(source_id, {}).pop(target_id, None)
        self._in_edges.get(target_id, {}).pop(source_id, None)

    def out_edges(self, node_id: str, data: bool = False) -> list[Any]:
        edges = []
        for target_id, attrs in self._out_edges.get(node_id, {}).items():
            edges.append((node_id, target_id, dict(attrs)) if data else (node_id, target_id))
        return edges

    def in_edges(self, node_id: str, data: bool = False) -> list[Any]:
        edges = []
        for source_id, attrs in self._in_edges.get(node_id, {}).items():
            edges.append((source_id, node_id, dict(attrs)) if data else (source_id, node_id))
        return edges

    def edges(self, data: bool = False) -> list[Any]:
        items: list[Any] = []
        for source_id in self._out_edges:
            items.extend(self.out_edges(source_id, data=data))
        return items


def shortest_path(graph: DiGraph, source: str, target: str) -> list[str]:
    if source == target and graph.has_node(source):
        return [source]
    if not graph.has_node(source) or not graph.has_node(target):
        raise NetworkXNoPath(f"either {source!r} or {target!r} does not exist")

    visited = {source}
    queue: deque[tuple[str, list[str]]] = deque([(source, [source])])
    while queue:
        current, path = queue.popleft()
        next_ids = [next_id for _, next_id in graph.out_edges(current)]
        next_ids.extend(prev_id for prev_id, _ in graph.in_edges(current))
        for next_id in next_ids:
            if next_id in visited:
                continue
            next_path = path + [next_id]
            if next_id == target:
                return next_path
            visited.add(next_id)
            queue.append((next_id, next_path))
    raise NetworkXNoPath(f"no path between {source!r} and {target!r}")
