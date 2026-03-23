"""Alert to diagnosis/remediation orchestration."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Protocol

from sre_agent.concurrency.alert_correlator import AlertCorrelator
from sre_agent.concurrency.alert_dedup import AlertDeduplicator
from sre_agent.concurrency.resource_lock import ResourceLock
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.remediation import LoopResult
from sre_agent.remediation.loop_orchestrator import LoopOrchestrator


class DiagnosisRunnerProtocol(Protocol):
    async def adiagnose(self, alert: Alert) -> DiagnosisSession: ...


class SessionStoreProtocol(Protocol):
    def put(self, session: DiagnosisSession) -> None: ...
    def get(self, session_id: str) -> DiagnosisSession | None: ...


class LoopStoreProtocol(Protocol):
    def put(self, loop_result: LoopResult) -> None: ...
    def get(self, session_id: str) -> LoopResult | None: ...


class IncidentHandler:
    def __init__(
        self,
        diagnosis_runner: DiagnosisRunnerProtocol,
        loop_orchestrator: LoopOrchestrator,
        resource_lock: ResourceLock,
        deduplicator: AlertDeduplicator,
        correlator: AlertCorrelator,
        session_store: SessionStoreProtocol,
        loop_store: LoopStoreProtocol,
    ) -> None:
        self.diagnosis_runner = diagnosis_runner
        self.loop = loop_orchestrator
        self.lock = resource_lock
        self.dedup = deduplicator
        self.correlator = correlator
        self.session_store = session_store
        self.loop_store = loop_store

    async def handle(self, alert: Alert) -> SREResponse[LoopResult]:
        provisional_session_id = alert.fingerprint
        is_dup, existing_sid = self.dedup.check_and_register(alert, provisional_session_id)
        if is_dup:
            return SREResponse(
                success=True,
                data=None,
                error=SREError(
                    code=ErrorCode.ALERT_DUPLICATE,
                    message=f"duplicate alert, see session {existing_sid}",
                ),
            )

        entity_ids = self._extract_target_entities(alert)
        async with AsyncExitStack() as stack:
            for entity_id in entity_ids:
                await stack.enter_async_context(self.lock.acquire(entity_id, holder=provisional_session_id))
            session = await self.diagnosis_runner.adiagnose(alert)
            self.session_store.put(session)
            loop_result = await self.loop.execute(session)
            self.loop_store.put(loop_result)
            return SREResponse(success=True, data=loop_result)

    @staticmethod
    def _extract_target_entities(alert: Alert) -> list[str]:
        entity_keys = ("node", "instance", "service", "pod", "switch")
        return [alert.labels[key] for key in entity_keys if key in alert.labels and alert.labels[key].strip()]
