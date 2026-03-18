"""Incident record helpers."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.memory import IncidentRecord


def build_incident_record(session: DiagnosisSession, aidc_id: str) -> IncidentRecord:
    diagnosis = session.diagnosis_result
    if diagnosis is None:
        raise ValueError("diagnosis session must include diagnosis_result")
    trace = []
    if session.trace is not None:
        trace = [step.to_dict() for step in session.trace.steps]
    symptoms = sorted({*session.alert.labels.keys(), session.alert.alert_name})
    return IncidentRecord(
        incident_id=uuid4().hex,
        aidc_id=aidc_id,
        timestamp=datetime.now(UTC),
        alert=session.alert,
        symptoms=symptoms,
        diagnosis_trace=trace,
        root_cause=diagnosis.root_cause,
        root_cause_layer=diagnosis.root_cause_layer,
        root_cause_entities=diagnosis.root_cause_entities,
        hypotheses_tested=diagnosis.hypotheses,
        remediation_applied=session.proposed_plan,
        outcome=session.outcome or "failed",
        resolution_time_seconds=session.duration_seconds,
        engineer_feedback=None,
        tags=[],
    )
