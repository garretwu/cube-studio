"""Diagnosis-remediation loop orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.remediation import CandidateAttempt, LoopResult, RemediationResult
from sre_agent.remediation.engine import RemediationEngine


class ReDiagnoseRunnerProtocol(Protocol):
    async def re_diagnose(self, session: DiagnosisSession, context: dict[str, Any]) -> DiagnosisSession: ...


class TracePublisherProtocol(Protocol):
    async def publish(self, event: dict[str, Any]) -> None: ...


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_candidates: int = 3
    cooldown_seconds: int = 30
    verification_window: int = 120
    enable_re_diagnosis: bool = True
    max_re_diagnosis_rounds: int = 1


class LoopOrchestrator:
    def __init__(
        self,
        remediation_engine: RemediationEngine,
        prometheus: Any | None,
        memory: Any | None,
        config: LoopConfig | None = None,
        re_diagnose_runner: ReDiagnoseRunnerProtocol | None = None,
        trace_publisher: TracePublisherProtocol | None = None,
    ) -> None:
        self.engine = remediation_engine
        self.prometheus = prometheus
        self.memory = memory
        self.config = config or LoopConfig()
        self.re_diagnose_runner = re_diagnose_runner
        self.trace_publisher = trace_publisher

    async def execute(self, session: DiagnosisSession) -> LoopResult:
        diagnosis = session.diagnosis_result
        assert diagnosis is not None
        attempts: list[CandidateAttempt] = []
        started = datetime.now(UTC)
        candidates = diagnosis.ranked_candidates[: self.config.max_candidates] or []

        for index, candidate in enumerate(candidates):
            if attempts:
                await asyncio.sleep(0)
            if self.trace_publisher is not None:
                await self.trace_publisher.publish(
                    {"type": "loop_progress", "session_id": session.session_id, "data": {"rank": candidate.rank}}
                )

            if candidate.recommended_fix is None:
                attempt = CandidateAttempt(
                    candidate=candidate,
                    remediation_result=RemediationResult(
                        plan_id=f"{session.session_id}-missing-plan",
                        success=False,
                        steps_completed=0,
                        steps_total=0,
                        error="No remediation plan",
                    ),
                    verification_passed=False,
                    rolled_back=False,
                    observations={"error": "no_plan"},
                    duration_seconds=0,
                )
                attempts.append(attempt)
                continue

            result = await self.engine.execute(candidate.recommended_fix, session_id=session.session_id)
            attempt = CandidateAttempt(
                candidate=candidate,
                remediation_result=result,
                verification_passed=result.success,
                rolled_back=result.rolled_back,
                observations={"post_verification": result.success},
                duration_seconds=result.duration_seconds,
            )
            attempts.append(attempt)
            if result.success:
                return LoopResult(
                    session_id=session.session_id,
                    outcome="resolved",
                    winning_candidate=candidate,
                    attempts=attempts,
                    total_duration_seconds=int((datetime.now(UTC) - started).total_seconds()),
                )
            if self._is_partially_improved(attempts):
                return LoopResult(
                    session_id=session.session_id,
                    outcome="partially_resolved",
                    winning_candidate=candidate,
                    attempts=attempts,
                    total_duration_seconds=int((datetime.now(UTC) - started).total_seconds()),
                )

        if self.config.enable_re_diagnosis and session.re_diagnosis_round < self.config.max_re_diagnosis_rounds:
            context = self._build_re_diagnosis_context(attempts)
            if self.re_diagnose_runner is not None:
                await self.re_diagnose_runner.re_diagnose(session, context)
            return LoopResult(
                session_id=session.session_id,
                outcome="re_diagnosed",
                attempts=attempts,
                total_duration_seconds=int((datetime.now(UTC) - started).total_seconds()),
                re_diagnosis_context=context,
            )

        return LoopResult(
            session_id=session.session_id,
            outcome="escalated",
            attempts=attempts,
            total_duration_seconds=int((datetime.now(UTC) - started).total_seconds()),
        )

    @staticmethod
    def _is_partially_improved(attempts: list[CandidateAttempt]) -> bool:
        return any(
            not attempt.verification_passed
            and attempt.remediation_result.steps_completed > 0
            for attempt in attempts
        )

    @staticmethod
    def _build_re_diagnosis_context(attempts: list[CandidateAttempt]) -> dict[str, Any]:
        return {
            "failed_candidates": [attempt.candidate.root_cause for attempt in attempts if not attempt.verification_passed],
            "attempt_count": len(attempts),
        }
