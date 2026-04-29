"""Alert to diagnosis/remediation orchestration."""

from __future__ import annotations

import inspect
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Protocol

from sre_agent.concurrency.alert_correlator import AlertCorrelator
from sre_agent.concurrency.alert_dedup import AlertDeduplicator
from sre_agent.concurrency.resource_lock import ResourceLock
from sre_agent.alerts_identity import build_incident_identity
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.diagnosis_event_payloads import build_diagnosis_candidates_ready_payload
from sre_agent.models.events import EventType
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.remediation import LoopResult
from sre_agent.remediation.loop_orchestrator import LoopOrchestrator


class DiagnosisRunnerProtocol(Protocol):
    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession: ...


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
        identity = build_incident_identity(alert)
        is_dup, existing_sid, incident_key = self.dedup.check_and_register(alert, provisional_session_id)
        if is_dup:
            return SREResponse(
                success=True,
                data=None,
                error=SREError(
                    code=ErrorCode.ALERT_DUPLICATE,
                    message=f"duplicate alert, see session {existing_sid}",
                    details={
                        "session_id": existing_sid,
                        "incident_key": incident_key,
                        "dedup_reason": "same_incident",
                        "identity_source": identity.identity_source,
                    },
                ),
            )

        entity_ids = self._extract_target_entities(alert)
        live_event_count = 0
        live_trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        if self.trace_publisher is not None:
            async def _live_trace_callback(event: dict[str, Any]) -> None:
                nonlocal live_event_count
                live_event_count += 1
                await self.trace_publisher.publish(event)

            live_trace_callback = _live_trace_callback
        async with AsyncExitStack() as stack:
            for entity_id in entity_ids:
                await stack.enter_async_context(self.lock.acquire(entity_id, holder=provisional_session_id))
            session = await self._run_diagnose(alert, trace_callback=live_trace_callback)
            self.dedup.bind_session(incident_key, session.session_id)
            self.session_store.put(session)
            await self._publish_diagnosis_events(
                session,
                include_trace_replay=live_event_count == 0,
                include_diagnosis_replay=live_event_count == 0,
            )
            loop_result = await self.loop.execute(session)
            self.loop_store.put(loop_result)
            await self._publish_completion_event(loop_result)
            return SREResponse(success=True, data=loop_result)

    async def _publish_diagnosis_events(
        self,
        session: DiagnosisSession,
        *,
        include_trace_replay: bool = True,
        include_diagnosis_replay: bool = True,
    ) -> None:
        if self.trace_publisher is None:
            return
        await self.trace_publisher.publish(
            {
                "type": EventType.ALERT.value,
                "session_id": session.session_id,
                "data": {"alert": session.alert.model_dump(mode="json")},
            }
        )
        if include_trace_replay and session.trace is not None:
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
        if include_diagnosis_replay and session.diagnosis_result is not None:
            diagnosis_payload = session.diagnosis_result.model_dump(mode="json")
            await self.trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_CANDIDATES_READY.value,
                    "session_id": session.session_id,
                    "data": build_diagnosis_candidates_ready_payload(session.diagnosis_result),
                }
            )
            await self.trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_RESULT.value,
                    "session_id": session.session_id,
                    "data": diagnosis_payload,
                }
            )
        if session.diagnosis_result is not None:
            plan_summaries: list[dict[str, Any]] = []
            for index, root in enumerate(session.diagnosis_result.root_cause):
                if root.recommended_fix is None:
                    continue
                plan_summaries.append(
                    {
                        "rank": index + 1,
                        "plan_key": f"rc:{root.id}" if str(root.id).strip() else f"rc:{index + 1}",
                        "plan_id": root.recommended_fix.plan_id,
                        "root_cause_id": root.id,
                        "root_cause_title": root.title,
                    }
                )
            if plan_summaries:
                primary = plan_summaries[0]
                await self.trace_publisher.publish(
                    {
                        "type": EventType.APPROVAL_REQUIRED.value,
                        "session_id": session.session_id,
                        "data": {
                            "plan_count": len(plan_summaries),
                            "plans": plan_summaries,
                            "plan_key": primary["plan_key"],
                            "plan_id": primary["plan_id"],
                        },
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

    async def _run_diagnose(
        self,
        alert: Alert,
        *,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> DiagnosisSession:
        method = self.diagnosis_runner.adiagnose
        if trace_callback is None:
            return await method(alert)
        if self._supports_trace_callback(method):
            return await method(alert, trace_callback=trace_callback)
        return await method(alert)

    @staticmethod
    def _supports_trace_callback(method: Callable[..., Any]) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        if "trace_callback" in signature.parameters:
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
