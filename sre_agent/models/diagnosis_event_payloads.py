"""Helpers for diagnosis lifecycle event payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sre_agent.models.diagnosis import DiagnosisResult


def build_diagnosis_candidates_ready_payload(diagnosis_result: DiagnosisResult) -> dict[str, Any]:
    """Build payload for staged candidate publication before final diagnosis_result."""

    return {
        "ranked_candidates": [candidate.model_dump(mode="json") for candidate in diagnosis_result.ranked_candidates],
        "hypotheses": [hypothesis.model_dump(mode="json") for hypothesis in diagnosis_result.hypotheses],
        "confidence": diagnosis_result.confidence,
        "diagnosis_certainty": diagnosis_result.diagnosis_certainty,
        "impact_summary": diagnosis_result.impact_summary,
        "affected_services": list(diagnosis_result.affected_services),
        "triage_priority": diagnosis_result.triage_priority,
        "emitted_at": datetime.now(UTC).isoformat(),
    }

