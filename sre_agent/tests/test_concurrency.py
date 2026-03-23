from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from sre_agent.concurrency import AlertCorrelator, AlertDeduplicator, ResourceLock, ResourceLockedError
from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause
from sre_agent.models.remediation import LoopResult
from sre_agent.remediation.incident_handler import IncidentHandler


def _alert(name: str, instance: str, node: str = "node-a") -> Alert:
    return Alert(
        alert_name=name,
        severity="critical",
        labels={"instance": instance, "node": node},
        annotations={},
        starts_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        fingerprint=f"fp-{name}-{instance}",
        status="firing",
    )


class _FakeDiagnosisRunner:
    async def adiagnose(self, alert: Alert) -> DiagnosisSession:
        diagnosis = DiagnosisResult(
            root_cause="gpu contention",
            root_cause_layer="service",
            confidence=0.9,
            impact_summary="latency",
            triage_priority="P1",
            diagnosis_certainty="confirmed",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause="gpu contention",
                    root_cause_layer="service",
                    confidence=0.9,
                    evidence_summary="util high",
                )
            ],
        )
        return DiagnosisSession(session_id=alert.fingerprint, alert=alert, status="diagnosed", diagnosis_result=diagnosis)


class _FakeLoop:
    async def execute(self, session: DiagnosisSession) -> LoopResult:
        return LoopResult(session_id=session.session_id, outcome="escalated", attempts=[], total_duration_seconds=0)


class _Store:
    def __init__(self) -> None:
        self.items = {}

    def put(self, value) -> None:  # noqa: ANN001
        self.items[getattr(value, "session_id")] = value

    def get(self, session_id: str):  # noqa: ANN001
        return self.items.get(session_id)


class TestConcurrencyUnit:
    def test_unit_generates_stable_fingerprint_when_same_alert_repeated(self) -> None:
        dedup = AlertDeduplicator()

        assert dedup.fingerprint(_alert("a", "pod-1")) == dedup.fingerprint(_alert("a", "pod-1"))

    @pytest.mark.asyncio
    async def test_unit_blocks_second_lock_when_same_resource_already_held(self) -> None:
        lock = ResourceLock()
        async with lock.acquire("node-a"):
            with pytest.raises(ResourceLockedError):
                async with lock.acquire("node-a"):
                    pass


class TestConcurrencyIntegration:
    def test_integration_correlates_alerts_when_same_service_in_window(self) -> None:
        correlator = AlertCorrelator(window_seconds=300)
        groups = correlator.correlate([_alert("a", "pod-1"), _alert("b", "pod-2")])

        assert len(groups) == 2

    @pytest.mark.asyncio
    async def test_integration_incident_handler_marks_duplicate_when_same_alert_repeated(self) -> None:
        handler = IncidentHandler(
            diagnosis_runner=_FakeDiagnosisRunner(),
            loop_orchestrator=_FakeLoop(),
            resource_lock=ResourceLock(),
            deduplicator=AlertDeduplicator(),
            correlator=AlertCorrelator(),
            session_store=_Store(),
            loop_store=_Store(),
        )

        first = await handler.handle(_alert("a", "pod-1"))
        duplicate = await handler.handle(_alert("a", "pod-1"))

        assert first.success is True
        assert duplicate.success is True
        assert duplicate.error is not None
        assert duplicate.error.code.value == "ALERT_DUPLICATE"


class TestConcurrencyE2E:
    @pytest.mark.asyncio
    async def test_e2e_returns_duplicate_response_when_same_alert_hits_handler_twice(self) -> None:
        handler = IncidentHandler(
            diagnosis_runner=_FakeDiagnosisRunner(),
            loop_orchestrator=_FakeLoop(),
            resource_lock=ResourceLock(),
            deduplicator=AlertDeduplicator(),
            correlator=AlertCorrelator(),
            session_store=_Store(),
            loop_store=_Store(),
        )

        await handler.handle(_alert("a", "pod-1"))
        response = await handler.handle(_alert("a", "pod-1"))

        assert response.error is not None
        assert "duplicate alert" in response.error.message

    @pytest.mark.asyncio
    async def test_e2e_blocks_parallel_remediation_when_alerts_target_same_resource(self) -> None:
        lock = ResourceLock()

        async def _hold_lock() -> None:
            async with lock.acquire("node-a"):
                await asyncio.sleep(0.05)

        task = asyncio.create_task(_hold_lock())
        await asyncio.sleep(0)

        with pytest.raises(ResourceLockedError):
            async with lock.acquire("node-a"):
                pass

        await task
