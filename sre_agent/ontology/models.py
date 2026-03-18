"""Local ontology module exports backed by the shared contracts."""

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType, Relationship

__all__ = ["EntityType", "RelationType", "OntologyNode", "OntologyEdge", "Relationship"]
