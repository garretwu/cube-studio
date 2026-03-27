"""Alert to diagnosis/remediation orchestration."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Protocol

from sre_agent.concurrency.alert_correlator import AlertCorrelator
from sre_agent.concurrency.alert_dedup import AlertDeduplicator
from sre_agent.concurrency.resource_lock import ResourceLock
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.events import EventType
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


class TracePublisherProtocol(Protocol):
    async def publish(self, event: dict[str, Any]) -> None: ...


class AlertStoreProtocol(Protocol):
    def add(self, alert: Alert) -> None: ...


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
        trace_publisher: TracePublisherProtocol | None = None,
        alert_store: AlertStoreProtocol | None = None,
    ) -> None:
        self.diagnosis_runner = diagnosis_runner
        self.loop = loop_orchestrator
        self.lock = resource_lock
        self.dedup = deduplicator
        self.correlator = correlator
        self.session_store = session_store
        self.loop_store = loop_store
        self.trace_publisher = trace_publisher
        self.alert_store = alert_store

    async def handle(self, alert: Alert) -> SREResponse[LoopResult]:
        provisional_session_id = alert.fingerprint
        if self.alert_store is not None:
            self.alert_store.add(alert)
        if self.trace_publisher is not None:
            await self.trace_publisher.publish(
                {
                    "type": EventType.ALERT.value,
                    "session_id": "alerts",
                    "data": {"alert": alert.model_dump(mode="json")},
                }
            )
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
            await self._publish_diagnosis_events(session)
            loop_result = await self.loop.execute(session)
            self.loop_store.put(loop_result)
            await self._publish_completion_event(loop_result)
            return SREResponse(success=True, data=loop_result)

    async def _publish_diagnosis_events(self, session: DiagnosisSession) -> None:
        if self.trace_publisher is None:
            return
        await self.trace_publisher.publish(
            {
                "type": EventType.ALERT.value,
                "session_id": session.session_id,
                "data": {"alert": session.alert.model_dump(mode="json")},
            }
        )
        if session.trace is not None:
            for item in session.trace.steps:
                if hasattr(item, "tool"):
                    payload = item.model_dump(mode="json")
                    await self.trace_publisher.publish(
                        {
                            "type": EventType.TOOL_RESULT.value,
                            "session_id": session.session_id,
                            "data": payload,
                        }
                    )
                else:
                    payload = item.model_dump(mode="json")
                    event_type = EventType.THINKING_STEP.value
                    if payload.get("action_type") == "tool_call":
                        event_type = EventType.TOOL_CALL.value
                    await self.trace_publisher.publish(
                        {
                            "type": event_type,
                            "session_id": session.session_id,
                            "data": payload,
                        }
                    )
        if session.diagnosis_result is not None:
            diagnosis_payload = session.diagnosis_result.model_dump(mode="json")
            await self.trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_RESULT.value,
                    "session_id": session.session_id,
                    "data": diagnosis_payload,
                }
            )
            if session.diagnosis_result.recommended_fix is not None:
                await self.trace_publisher.publish(
                    {
                        "type": EventType.APPROVAL_REQUIRED.value,
                        "session_id": session.session_id,
                        "data": {"plan_id": session.diagnosis_result.recommended_fix.plan_id},
                    }
                )

    async def _publish_completion_event(self, result: LoopResult) -> None:
        if self.trace_publisher is None:
            return
        event_type = EventType.DONE.value
        if result.outcome in {"escalated", "exhausted"}:
            event_type = EventType.ERROR.value
        await self.trace_publisher.publish(
            {
                "type": event_type,
                "session_id": result.session_id,
                "data": {"outcome": result.outcome, "attempts": len(result.attempts)},
            }
        )

    @staticmethod
    def _extract_target_entities(alert: Alert) -> list[str]:
        entity_keys = ("node", "instance", "service", "pod", "switch")
        return [alert.labels[key] for key in entity_keys if key in alert.labels and alert.labels[key].strip()]
