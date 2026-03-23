"""Blast-radius policy checks for remediation validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lib.channels.base import SafetyViolationError


@dataclass(frozen=True)
class BlastRadiusPolicy:
    max_affected_entities: int = 3


def ensure_within_blast_radius(
    ontology: Any,
    entity_ids: list[str],
    policy: BlastRadiusPolicy,
) -> dict[str, Any]:
    affected_count = 0
    details: dict[str, Any] = {}
    for entity_id in entity_ids:
        result = ontology.get_blast_radius(entity_id)
        count = int(result.get("affected_count", 0))
        details[entity_id] = count
        affected_count = max(affected_count, count)
        if count > policy.max_affected_entities:
            raise SafetyViolationError(
                f"blast radius too large for {entity_id}: {count} > {policy.max_affected_entities}"
            )
    return {"affected_count": affected_count, "details": details}
