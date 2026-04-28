"""Shared coordinator for async diagnosis session bootstrap."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Any

from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.diagnosis_event_payloads import build_diagnosis_candidates_ready_payload
from sre_agent.models.events import EventType
from sre_agent.models.remediation import RemediationPlan

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiagnosisStartHandle:
    """Result of scheduling an async diagnosis start flow."""

    initial_session: DiagnosisSession
    task: asyncio.Task[None]


class DiagnosisStartCoordinator:
    """Reusable async diagnose-start workflow for API and background triggers."""

    def __init__(
        self,
        *,
        diagnosis_runner: Any,
        remediation_engine: Any,
        session_store: Any,
        trace_publisher: Any,
    ) -> None:
        self._diagnosis_runner = diagnosis_runner
        self._remediation_engine = remediation_engine
        self._session_store = session_store
        self._trace_publisher = trace_publisher

    def start(
        self,
        *,
        alert: Alert,
        extra_alerts: list[Alert] | None = None,
        task_name_prefix: str = "diagnose-start",
        propagate_failure: bool = False,
    ) -> DiagnosisStartHandle:
        initial_session = DiagnosisSession.create(alert)
        self._session_store.put(initial_session)
        task = asyncio.create_task(
            self._background_run(
                initial_session=initial_session,
                alert=alert,
                extra_alerts=extra_alerts or [],
                propagate_failure=propagate_failure,
            ),
            name=f"{task_name_prefix}-{initial_session.session_id}",
        )
        return DiagnosisStartHandle(initial_session=initial_session, task=task)

    async def _background_run(
        self,
        *,
        initial_session: DiagnosisSession,
        alert: Alert,
        extra_alerts: list[Alert],
        propagate_failure: bool,
    ) -> None:
        live_event_count = 0
        live_event_types: set[str] = set()

        async def _trace_callback(event: dict[str, Any]) -> None:
            nonlocal live_event_count
            payload = dict(event) if isinstance(event, dict) else {}
            payload["session_id"] = initial_session.session_id
            event_type = str(payload.get("type", "")).strip().lower()
            await self._trace_publisher.publish(payload)
            live_event_count += 1
            if event_type:
                live_event_types.add(event_type)

        try:
            completed = await self._run_diagnose_with_optional_trace_callback(
                alert=alert,
                trace_callback=_trace_callback,
                extra_alerts=extra_alerts,
            )
            completed = completed.model_copy(update={"session_id": initial_session.session_id})

            if live_event_count == 0:
                await self._replay_trace_and_diagnosis_events(session=completed)
                if completed.diagnosis_result is not None:
                    live_event_types.add(EventType.DIAGNOSIS_CANDIDATES_READY.value)
                if completed.diagnosis_result is not None:
                    live_event_types.add(EventType.DIAGNOSIS_RESULT.value)
            elif completed.diagnosis_result is not None:
                if EventType.DIAGNOSIS_CANDIDATES_READY.value not in live_event_types:
                    await self._trace_publisher.publish(
                        {
                            "type": EventType.DIAGNOSIS_CANDIDATES_READY.value,
                            "session_id": completed.session_id,
                            "data": build_diagnosis_candidates_ready_payload(completed.diagnosis_result),
                        }
                    )
                if EventType.DIAGNOSIS_RESULT.value not in live_event_types:
                    await self._trace_publisher.publish(
                        {
                            "type": EventType.DIAGNOSIS_RESULT.value,
                            "session_id": completed.session_id,
                            "data": completed.diagnosis_result.model_dump(mode="json"),
                        }
                    )

            plan = self._extract_recommended_fix(completed)
            if plan is not None:
                self._remediation_engine.register_plan(completed.session_id, plan)
                completed = completed.model_copy(update={"status": "approval_required"})
            elif completed.status == "diagnosing":
                completed = completed.model_copy(update={"status": "diagnosed"})
            self._session_store.put(completed)

            if plan is not None:
                plan_version = self._remediation_engine.get_latest_plan_version(completed.session_id)
                await self._publish_session_event(
                    event_type=EventType.APPROVAL_REQUIRED,
                    session_id=completed.session_id,
                    data={"plan_id": plan.plan_id, "plan_version": plan_version},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("async diagnosis start failed for %s: %s", alert.fingerprint, exc)
            failed_session = initial_session.model_copy(update={"status": "failed", "outcome": "failed"})
            self._session_store.put(failed_session)
            await self._publish_session_event(
                event_type=EventType.ERROR,
                session_id=initial_session.session_id,
                data={"message": str(exc)},
            )
            if propagate_failure:
                raise

    async def _run_diagnose_with_optional_trace_callback(
        self,
        *,
        alert: Alert,
        trace_callback: Any | None,
        extra_alerts: list[Alert] | None = None,
    ) -> DiagnosisSession:
        method = self._diagnosis_runner.adiagnose
        kwargs: dict[str, Any] = {}
        if extra_alerts and self._supports_extra_alerts(method):
            kwargs["extra_alerts"] = extra_alerts
        if trace_callback is not None and self._supports_trace_callback(method):
            return await method(alert, trace_callback=trace_callback, **kwargs)
        return await method(alert, **kwargs)

    async def _replay_trace_and_diagnosis_events(
        self,
        *,
        session: DiagnosisSession,
    ) -> None:
        if session.trace is not None:
            for item in session.trace.steps:
                payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
                if hasattr(item, "tool"):
                    await self._trace_publisher.publish(
                        {
                            "type": EventType.TOOL_RESULT.value,
                            "session_id": session.session_id,
                            "data": payload,
                        }
                    )
                    continue
                event_type = EventType.THINKING_STEP.value
                if str(payload.get("action_type", "")).strip().lower() == "tool_call":
                    event_type = EventType.TOOL_CALL.value
                await self._trace_publisher.publish(
                    {
                        "type": event_type,
                        "session_id": session.session_id,
                        "data": payload,
                    }
                )
        if session.diagnosis_result is not None:
            await self._trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_CANDIDATES_READY.value,
                    "session_id": session.session_id,
                    "data": build_diagnosis_candidates_ready_payload(session.diagnosis_result),
                }
            )
            await self._trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_RESULT.value,
                    "session_id": session.session_id,
                    "data": session.diagnosis_result.model_dump(mode="json"),
                }
            )

    async def _publish_session_event(
        self,
        *,
        event_type: EventType,
        session_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        await self._trace_publisher.publish(
            {
                "type": event_type.value,
                "session_id": session_id,
                "data": data or {},
            }
        )

    @staticmethod
    def _extract_recommended_fix(session: DiagnosisSession) -> RemediationPlan | None:
        if session.diagnosis_result is None:
            return None
        if session.diagnosis_result.recommended_fix is not None:
            return session.diagnosis_result.recommended_fix
        for candidate in session.diagnosis_result.ranked_candidates:
            if candidate.recommended_fix is not None:
                return candidate.recommended_fix
        return None

    @staticmethod
    def _supports_trace_callback(method: Any) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        if "trace_callback" in signature.parameters:
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())

    @staticmethod
    def _supports_extra_alerts(method: Any) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        if "extra_alerts" in signature.parameters:
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())


__all__ = ["DiagnosisStartCoordinator", "DiagnosisStartHandle"]
