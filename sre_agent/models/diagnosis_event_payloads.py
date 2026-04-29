"""Helpers for diagnosis lifecycle event payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.diagnosis import DiagnosisResult


def build_diagnosis_candidates_ready_payload(diagnosis_result: DiagnosisResult) -> dict[str, Any]:
    """Build payload for staged candidate publication before final diagnosis_result."""
    root_causes: list[dict[str, Any]] = []
    for candidate in diagnosis_result.root_cause:
        payload = candidate.model_dump(mode="json")
        payload["recommended_fix"] = None
        root_causes.append(payload)
    return {
        "root_cause": root_causes,
        "ranked_candidates": [
            {
                "rank": index + 1,
                "root_cause": str(candidate.get("title") or ""),
                "root_cause_layer": candidate.get("layer"),
                "root_cause_entities": list(candidate.get("entities") or []),
                "confidence": candidate.get("confidence"),
                "evidence_summary": candidate.get("evidence_summary"),
                "impact_summary": candidate.get("impact_summary"),
            }
            for index, candidate in enumerate(root_causes)
        ],
        "hypotheses": [hypothesis.model_dump(mode="json") for hypothesis in diagnosis_result.hypotheses],
        "confidence": diagnosis_result.confidence,
        "diagnosis_certainty": diagnosis_result.diagnosis_certainty,
        "impact_summary": diagnosis_result.impact_summary,
        "affected_services": list(diagnosis_result.affected_services),
        "triage_priority": diagnosis_result.triage_priority,
        "emitted_at": datetime.now(UTC).isoformat(),
    }
