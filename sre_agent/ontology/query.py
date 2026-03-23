"""Human-friendly query helpers for ontology results."""
from __future__ import annotations

from sre_agent.models.ontology import OntologyNode


def summarize_neighbors(neighbors: list[dict]) -> list[str]:
    summaries: list[str] = []
    for item in neighbors:
        node: OntologyNode = item["entity"]
        edge = item["relation"]
        summaries.append(f"{item['direction']}:{edge.relation.value}:{node.id}")
    return summaries


def summarize_path(path: list[str] | None) -> str:
    if not path:
        return ""
    return " -> ".join(path)
