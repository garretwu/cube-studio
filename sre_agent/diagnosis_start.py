"""Shared coordinator for async diagnosis session bootstrap."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any

from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisSession
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
                    live_event_types.add(EventType.DIAGNOSIS_RESULT.value)
            elif completed.diagnosis_result is not None and EventType.DIAGNOSIS_RESULT.value not in live_event_types:
                await self._trace_publisher.publish(
                    {
                        "type": EventType.DIAGNOSIS_RESULT.value,
                        "session_id": completed.session_id,
                        "data": completed.diagnosis_result.model_dump(mode="json"),
                    }
                )

            plans = self._extract_root_cause_plans(completed)
            plan_summaries = self._register_root_cause_plans(completed.session_id, plans)
            if plan_summaries:
                completed = completed.model_copy(update={"status": "approval_required"})
            elif completed.status == "diagnosing":
                completed = completed.model_copy(update={"status": "diagnosed"})
            self._session_store.put(completed)

            if plan_summaries:
                primary = plan_summaries[0]
                await self._publish_session_event(
                    event_type=EventType.APPROVAL_REQUIRED,
                    session_id=completed.session_id,
                    data={
                        "plan_count": len(plan_summaries),
                        "plans": plan_summaries,
                        "plan_key": primary["plan_key"],
                        "plan_id": primary["plan_id"],
                        "plan_version": primary["plan_version"],
                    },
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
    def _extract_root_cause_plans(session: DiagnosisSession) -> list[tuple[str, RemediationPlan]]:
        """Extract all remediation plans from `root_cause[]` in diagnosis order.

        Purpose:
        - materialize multi-plan approval candidates directly from root-cause items.
        Input/Output:
        - input: diagnosis session;
        - output: ordered `(plan_key, plan)` pairs.
        Compatibility rationale:
        - no longer reads legacy top-level remediation plan fields.
        Why:
        - root-cause list is now the only plan source.
        """
        if session.diagnosis_result is None:
            return []
        plans: list[tuple[str, RemediationPlan]] = []
        for index, item in enumerate(session.diagnosis_result.root_cause):
            if item.recommended_fix is None:
                continue
            plan_key = DiagnosisStartCoordinator._build_root_cause_plan_key(index=index, root_cause_id=item.id)
            plans.append((plan_key, item.recommended_fix))
        return plans

    @staticmethod
    def _build_root_cause_plan_key(*, index: int, root_cause_id: str | None) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._:-]+", "-", str(root_cause_id or "").strip()).strip("-")
        if cleaned:
            return f"rc:{cleaned}"
        return f"rc:{index + 1}"

    def _register_root_cause_plans(
        self,
        session_id: str,
        plans: list[tuple[str, RemediationPlan]],
    ) -> list[dict[str, Any]]:
        if not plans:
            clear = getattr(self._remediation_engine, "clear_session_plans", None)
            if callable(clear):
                clear(session_id)
            return []
        register_plans = getattr(self._remediation_engine, "register_plans", None)
        if callable(register_plans):
            register_plans(session_id, plans, clear_existing=True)
        else:
            clear = getattr(self._remediation_engine, "clear_session_plans", None)
            if callable(clear):
                clear(session_id)
            for idx, (plan_key, plan) in enumerate(plans):
                try:
                    self._remediation_engine.register_plan(
                        session_id,
                        plan,
                        plan_key=plan_key,
                        set_default=idx == 0,
                    )
                except TypeError:
                    self._remediation_engine.register_plan(session_id, plan)
        summaries: list[dict[str, Any]] = []
        for idx, (plan_key, plan) in enumerate(plans):
            get_version = getattr(self._remediation_engine, "get_latest_plan_version", None)
            if callable(get_version):
                try:
                    plan_version = int(get_version(session_id, plan_key=plan_key))
                except TypeError:
                    plan_version = int(get_version(session_id))
            else:
                plan_version = 0
            summaries.append(
                {
                    "rank": idx + 1,
                    "plan_key": plan_key,
                    "plan_id": plan.plan_id,
                    "plan_version": plan_version,
                }
            )
        return summaries

    @staticmethod
    def _extract_recommended_fix(session: DiagnosisSession) -> RemediationPlan | None:
        """Deprecated compatibility helper; returns the first root-cause plan if present."""
        if session.diagnosis_result is None:
            return None
        for item in session.diagnosis_result.root_cause:
            if item.recommended_fix is not None:
                return item.recommended_fix
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
