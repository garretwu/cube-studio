from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause
from sre_agent.models.remediation import CandidateAttempt, LoopResult, RemediationPlan, RemediationResult, RemediationStep, VerificationConfig
from sre_agent.remediation.loop_orchestrator import LoopConfig, LoopOrchestrator


def _plan(plan_id: str) -> RemediationPlan:
    return RemediationPlan(
        plan_id=plan_id,
        root_cause="gpu contention",
        description="delete pod",
        estimated_impact="minor",
        confidence=0.9,
        priority="P1",
        steps=[
            RemediationStep(
                step_id=1,
                description="delete pod",
                tool="k8s.delete_pod",
                params={"namespace": "infer", "pod_name": "vllm-0"},
                verification=VerificationConfig(method="wait", wait_seconds=1),
            )
        ],
    )


def _session(*, certainty: str = "probable", candidates: list[RankedRootCause]) -> DiagnosisSession:
    alert = Alert(
        alert_name="vllm_latency_high",
        severity="critical",
        labels={"instance": "vllm-0"},
        annotations={},
        starts_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
        fingerprint="fp-1",
        status="firing",
    )
    diagnosis = DiagnosisResult(
        root_cause=candidates[0].root_cause,
        root_cause_layer="service",
        confidence=0.9 if certainty == "confirmed" else 0.8,
        impact_summary="latency spike",
        triage_priority="P1",
        diagnosis_certainty=certainty,  # type: ignore[arg-type]
        ranked_candidates=candidates,
    )
    return DiagnosisSession(
        session_id="session-1",
        alert=alert,
        status="diagnosed",
        diagnosis_result=diagnosis,
    )


class _FakeEngine:
    def __init__(self, results: list[RemediationResult]) -> None:
        self.results = results
        self.calls = 0

    async def execute(self, plan: RemediationPlan, session_id: str | None = None) -> RemediationResult:
        _ = plan, session_id
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result


class _FakeReDiagnose:
    def __init__(self) -> None:
        self.called = False

    async def re_diagnose(self, session: DiagnosisSession, context: dict[str, Any]) -> DiagnosisSession:
        self.called = True
        _ = context
        return session


class _FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def publish(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class TestLoopOrchestratorUnit:
    def test_unit_builds_re_diagnosis_context_when_attempts_fail(self) -> None:
        attempt = CandidateAttempt.model_validate(
            {
                "candidate": {
                    "rank": 1,
                    "root_cause": "gpu contention",
                    "root_cause_layer": "service",
                    "confidence": 0.8,
                    "evidence_summary": "high util",
                },
                "remediation_result": {
                    "plan_id": "plan-1",
                    "success": False,
                    "steps_completed": 1,
                    "steps_total": 1,
                },
                "verification_passed": False,
                "rolled_back": True,
                "observations": {},
                "duration_seconds": 1,
            }
        )
        context = LoopOrchestrator._build_re_diagnosis_context([attempt])

        assert context["failed_candidates"] == ["gpu contention"]
        assert context["attempt_count"] == 1

    def test_unit_detects_partial_improvement_when_steps_completed_but_not_verified(self) -> None:
        attempt = CandidateAttempt.model_validate(
            {
                "candidate": {
                    "rank": 1,
                    "root_cause": "gpu contention",
                    "root_cause_layer": "service",
                    "confidence": 0.8,
                    "evidence_summary": "high util",
                },
                "remediation_result": {
                    "plan_id": "plan-1",
                    "success": False,
                    "steps_completed": 1,
                    "steps_total": 2,
                },
                "verification_passed": False,
                "rolled_back": True,
                "observations": {},
                "duration_seconds": 1,
            }
        )

        assert LoopOrchestrator._is_partially_improved([attempt]) is True


class TestLoopOrchestratorIntegration:
    @pytest.mark.asyncio
    async def test_integration_resolves_when_second_candidate_succeeds(self) -> None:
        candidate1 = RankedRootCause(rank=1, root_cause="kv cache", root_cause_layer="service", confidence=0.6, evidence_summary="maybe", recommended_fix=_plan("p1"))
        candidate2 = RankedRootCause(rank=2, root_cause="gpu contention", root_cause_layer="service", confidence=0.8, evidence_summary="likely", recommended_fix=_plan("p2"))
        session = _session(candidates=[candidate1, candidate2])
        engine = _FakeEngine(
            [
                RemediationResult(plan_id="p1", success=False, steps_completed=0, steps_total=1, error="verification failed", rolled_back=True),
                RemediationResult(plan_id="p2", success=True, steps_completed=1, steps_total=1),
            ]
        )
        publisher = _FakePublisher()
        orchestrator = LoopOrchestrator(engine, prometheus=None, memory=None, trace_publisher=publisher)

        result = await orchestrator.execute(session)

        assert result.outcome == "resolved"
        assert result.winning_candidate is not None
        assert result.winning_candidate.root_cause == "gpu contention"
        assert publisher.events
        assert publisher.events[0]["type"] == "loop_start"
        assert publisher.events[0]["data"]["candidate_count"] == 2
        assert any(event["type"] == "loop_progress" for event in publisher.events)

    @pytest.mark.asyncio
    async def test_integration_triggers_re_diagnosis_when_candidates_exhausted(self) -> None:
        candidate = RankedRootCause(rank=1, root_cause="gpu contention", root_cause_layer="service", confidence=0.8, evidence_summary="likely", recommended_fix=_plan("p1"))
        session = _session(candidates=[candidate])
        engine = _FakeEngine([RemediationResult(plan_id="p1", success=False, steps_completed=0, steps_total=1, error="failed", rolled_back=True)])
        rediagnose = _FakeReDiagnose()
        orchestrator = LoopOrchestrator(
            engine,
            prometheus=None,
            memory=None,
            config=LoopConfig(max_re_diagnosis_rounds=1, enable_re_diagnosis=True),
            re_diagnose_runner=rediagnose,
        )

        result = await orchestrator.execute(session)

        assert result.outcome == "re_diagnosed"
        assert rediagnose.called is True


class TestLoopOrchestratorE2E:
    @pytest.mark.asyncio
    async def test_e2e_returns_resolved_when_happy_path(self) -> None:
        candidate = RankedRootCause(rank=1, root_cause="gpu contention", root_cause_layer="service", confidence=0.9, evidence_summary="clear", recommended_fix=_plan("p1"))
        session = _session(certainty="confirmed", candidates=[candidate])
        orchestrator = LoopOrchestrator(
            _FakeEngine([RemediationResult(plan_id="p1", success=True, steps_completed=1, steps_total=1)]),
            prometheus=None,
            memory=None,
        )

        result = await orchestrator.execute(session)

        assert result.outcome == "resolved"

    @pytest.mark.asyncio
    async def test_e2e_returns_escalated_when_no_re_diagnosis_and_all_fail(self) -> None:
        candidate = RankedRootCause(rank=1, root_cause="gpu contention", root_cause_layer="service", confidence=0.8, evidence_summary="likely", recommended_fix=_plan("p1"))
        session = _session(candidates=[candidate])
        orchestrator = LoopOrchestrator(
            _FakeEngine([RemediationResult(plan_id="p1", success=False, steps_completed=0, steps_total=1, error="failed")]),
            prometheus=None,
            memory=None,
            config=LoopConfig(enable_re_diagnosis=False),
        )

        result = await orchestrator.execute(session)

        assert result.outcome == "escalated"
