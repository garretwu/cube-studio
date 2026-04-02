"""Ontology entity/relationship models for shared SRE contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field, model_validator

from sre_agent.models.common import StrictFrozenModel


class EntityType(str, Enum):
    """Canonical ontology entity types."""

    RACK = "rack"
    NODE = "node"
    GPU = "gpu"
    NIC = "nic"
    SWITCH = "switch"
    SWITCH_PORT = "switch_port"
    BMC_ENDPOINT = "bmc_endpoint"
    K8S_CLUSTER = "k8s_cluster"
    K8S_POD = "k8s_pod"
    INFERENCE_SERVICE = "inference_service"
    TRAINING_PIPELINE = "training_pipeline"
    METRIC_ENDPOINT = "metric_endpoint"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> EntityType | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        aliases = {
            "network": "switch",
            "switches": "switch",
            "service": "inference_service",
            "services": "inference_service",
            "cluster": "k8s_cluster",
            "pod": "k8s_pod",
            "metric": "metric_endpoint",
            "metrics": "metric_endpoint",
            "bmc": "bmc_endpoint",
            "host": "node",
            "hardware": "node",
        }
        candidate = aliases.get(normalized, normalized)
        for member in cls:
            if member.value == candidate:
                return member
        return None


class RelationType(str, Enum):
    """Supported ontology relationship directions."""

    HOSTED_ON = "hosted_on"
    CONNECTED_TO = "connected_to"
    PART_OF = "part_of"
    SERVES = "serves"
    DEPENDS_ON = "depends_on"
    MANAGES = "manages"
    MONITORS = "monitors"


class OntologyNode(StrictFrozenModel):
    """Generic ontology node contract."""

    id: str = Field(min_length=1)
    entity_type: EntityType = Field(validation_alias=AliasChoices("entity_type", "type"))
    name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OntologyEdge(StrictFrozenModel):
    """Generic ontology edge contract."""

    source_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("source_id", "source", "from", "from_id"),
    )
    target_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("target_id", "target", "to", "to_id"),
    )
    relation: RelationType = Field(validation_alias=AliasChoices("relation", "type"))
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _different_endpoints(self) -> OntologyEdge:
        if self.source_id == self.target_id:
            raise ValueError("source_id and target_id must differ")
        return self


# Compatibility alias: legacy naming in design snippets.
Relationship = OntologyEdge


__all__ = [
    "EntityType",
    "RelationType",
    "OntologyNode",
    "OntologyEdge",
    "Relationship",
]

