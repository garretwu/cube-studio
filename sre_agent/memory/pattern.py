"""Pattern learning helpers."""
from __future__ import annotations

from sre_agent.models.memory import IncidentRecord, LearnedPattern


def merge_pattern_from_incident(existing: LearnedPattern | None, record: IncidentRecord) -> LearnedPattern:
    symptom_signature = sorted(record.symptoms)
    if existing is None:
        return LearnedPattern(
            pattern_id=record.incident_id,
            aidc_id=record.aidc_id,
            symptom_signature=symptom_signature,
            root_cause=record.root_cause,
            effective_fix=record.remediation_applied.description if record.remediation_applied else "",
            occurrence_count=1,
            confidence=0.3,
            first_seen=record.timestamp,
            last_seen=record.timestamp,
            example_incidents=[record.incident_id],
        )

    confidence = existing.confidence
    if record.outcome == "resolved":
        confidence = min(1.0, confidence + 0.15)
    elif record.outcome == "failed":
        confidence = max(0.0, confidence - 0.1)

    example_incidents = list(existing.example_incidents)
    if record.incident_id not in example_incidents and record.outcome == "resolved":
        example_incidents.append(record.incident_id)

    return existing.model_copy(
        update={
            "last_seen": record.timestamp,
            "occurrence_count": existing.occurrence_count + 1,
            "confidence": confidence,
            "effective_fix": record.remediation_applied.description if record.remediation_applied else existing.effective_fix,
            "example_incidents": example_incidents,
        }
    )
