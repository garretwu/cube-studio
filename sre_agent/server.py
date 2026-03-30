"""FastAPI application factory for Agent C."""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from fastapi import FastAPI

from lib.channels.alert import AlertChannel
from sre_agent.agent import ConversationalAgent, run_diagnosis
from sre_agent.api import build_api_router, build_websocket_router, install_middlewares
from sre_agent.api.routes import AuditLogger
from sre_agent.auth.jwt import CurrentUser, JWTSettings, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.concurrency import AlertCorrelator, AlertDeduplicator, ResourceLock
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.alert import Alert, AlertSeverity
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import EventType, WSEvent
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.remediation import ApprovalGate, IncidentHandler, LoopConfig, LoopOrchestrator, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.tools import ToolExecutionContext, build_default_registry

LOGGER = logging.getLogger(__name__)


class DiagnosisRunnerProtocol(Protocol):
    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession: ...


class ReDiagnoseRunnerProtocol(Protocol):
    async def re_diagnose(
        self,
        session: DiagnosisSession,
        context: dict[str, Any],
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession: ...


class ChatHandlerProtocol(Protocol):
    async def __call__(self, message: str) -> str: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, DiagnosisSession] = {}
        self._updated_at: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def put(self, session: DiagnosisSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._updated_at[session.session_id] = datetime.now(UTC)

    def get(self, session_id: str) -> DiagnosisSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def update_status(self, session_id: str, *, status: str, outcome: str | None = None) -> DiagnosisSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            updates: dict[str, Any] = {"status": status}
            if outcome is not None:
                updates["outcome"] = outcome
            updated = session.model_copy(update=updates)
            self._sessions[session_id] = updated
            self._updated_at[session_id] = datetime.now(UTC)
            return updated

    def transition_status(
        self,
        session_id: str,
        *,
        expected_statuses: set[str],
        status: str,
        outcome: str | None = None,
    ) -> DiagnosisSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.status not in expected_statuses:
                return None
            updates: dict[str, Any] = {"status": status}
            if outcome is not None:
                updates["outcome"] = outcome
            updated = session.model_copy(update=updates)
            self._sessions[session_id] = updated
            self._updated_at[session_id] = datetime.now(UTC)
            return updated

    def list_recent(self, *, limit: int = 50) -> list[tuple[DiagnosisSession, datetime]]:
        with self._lock:
            safe_limit = max(1, int(limit))
            ordered = sorted(self._updated_at.items(), key=lambda item: item[1], reverse=True)
            payload: list[tuple[DiagnosisSession, datetime]] = []
            for session_id, updated_at in ordered[:safe_limit]:
                session = self._sessions.get(session_id)
                if session is None:
                    continue
                payload.append((session, updated_at))
            return payload


class InMemoryLoopStore:
    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def put(self, result: Any) -> None:
        self._results[result.session_id] = result

    def get(self, session_id: str) -> Any:
        return self._results.get(session_id)


class InMemoryTracePublisher:
    def __init__(self, *, max_events_per_session: int = 1000) -> None:
        self._events: dict[str, list[WSEvent]] = {}
        self._condition = asyncio.Condition()
        self._session_seq: dict[str, int] = {}
        self._max_events_per_session = max(1, int(max_events_per_session))

    async def publish(self, event: dict[str, Any] | WSEvent) -> None:
        ws_event = event if isinstance(event, WSEvent) else WSEvent.model_validate(event)
        data = dict(ws_event.data)
        if "event_id" not in data:
            next_seq = self._session_seq.get(ws_event.session_id, 0) + 1
            self._session_seq[ws_event.session_id] = next_seq
            data["event_id"] = str(next_seq)
            ws_event = ws_event.model_copy(update={"data": data})
        async with self._condition:
            events = self._events.setdefault(ws_event.session_id, [])
            events.append(ws_event)
            if len(events) > self._max_events_per_session:
                overflow = len(events) - self._max_events_per_session
                del events[:overflow]
            self._condition.notify_all()

    async def subscribe(self, session_id: str, after: str | None = None) -> AsyncIterator[WSEvent]:
        index = 0
        if after is not None:
            events = self._events.get(session_id, [])
            for pos, event in enumerate(events):
                if str(event.data.get("event_id")) == str(after):
                    index = pos + 1
                    break
            else:
                # Resume id may be evicted by retention/backpressure; replay available history.
                index = 0
        while True:
            events = self._events.get(session_id, [])
            while index < len(events):
                yield events[index]
                index += 1
            async with self._condition:
                await self._condition.wait()


class InMemoryAlertStore:
    def __init__(self, *, max_items: int = 200) -> None:
        self._alerts: list[Alert] = []
        self._max_items = max_items

    def add(self, alert: Alert) -> None:
        self._alerts = [existing for existing in self._alerts if existing.fingerprint != alert.fingerprint]
        self._alerts.insert(0, alert)
        if len(self._alerts) > self._max_items:
            self._alerts = self._alerts[: self._max_items]

    def replace(self, alerts: list[Alert]) -> None:
        seen: set[str] = set()
        ordered: list[Alert] = []
        for alert in sorted(alerts, key=lambda item: item.starts_at, reverse=True):
            if alert.fingerprint in seen:
                continue
            seen.add(alert.fingerprint)
            ordered.append(alert)
            if len(ordered) >= self._max_items:
                break
        self._alerts = ordered

    def snapshot(self) -> dict[str, Any]:
        severity_rank = {
            AlertSeverity.CRITICAL.value: 3,
            AlertSeverity.WARNING.value: 2,
            AlertSeverity.INFO.value: 1,
        }
        alerts_payload = [alert.model_dump(mode="json") for alert in self._alerts]
        clusters: list[dict[str, Any]] = []
        buckets: dict[tuple[str, str], list[Alert]] = {}
        for alert in self._alerts:
            namespace = alert.labels.get("exported_namespace") or alert.labels.get("namespace") or ""
            service = (
                alert.labels.get("exported_container")
                or alert.labels.get("service")
                or alert.labels.get("job")
                or alert.alert_name
            )
            key = (namespace, service)
            buckets.setdefault(key, []).append(alert)
        for idx, grouped in enumerate(buckets.values(), start=1):
            summary = grouped[0].summary or grouped[0].alert_name
            top = max(grouped, key=lambda item: severity_rank.get(item.severity.value, 0))
            clusters.append(
                {
                    "cluster_id": f"cluster-{idx}",
                    "summary": summary,
                    "severity": top.severity.value,
                    "alerts": [item.fingerprint for item in grouped],
                }
            )
        return {"alerts": alerts_payload, "clusters": clusters}


class AlertChannelProtocol(Protocol):
    async def connect(self) -> bool: ...
    async def disconnect(self) -> bool: ...
    async def get_firing_alerts(self, filter_labels: dict[str, str] | None = None) -> list[Alert]: ...


class AlertPollingService:
    def __init__(
        self,
        *,
        channel: AlertChannelProtocol,
        alert_store: InMemoryAlertStore,
        trace_publisher: InMemoryTracePublisher,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._channel = channel
        self._alert_store = alert_store
        self._trace_publisher = trace_publisher
        self._poll_interval_seconds = max(0.2, float(poll_interval_seconds))
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_versions: dict[str, str] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="sre-alert-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        try:
            try:
                await self._channel.connect()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("alert poller failed to connect to alert source: %s", exc)
            while not self._stop_event.is_set():
                try:
                    alerts = await self._channel.get_firing_alerts()
                    self._alert_store.replace(alerts)
                    await self._publish_changes(alerts)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("alert poller cycle failed: %s", exc)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
                except TimeoutError:
                    continue
        finally:
            try:
                await self._channel.disconnect()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("alert poller disconnect failed: %s", exc)

    async def _publish_changes(self, alerts: list[Alert]) -> None:
        current_versions = {alert.fingerprint: self._version(alert) for alert in alerts}
        alerts_by_fingerprint = {alert.fingerprint: alert for alert in alerts}

        for fingerprint, version in current_versions.items():
            if self._last_versions.get(fingerprint) == version:
                continue
            alert = alerts_by_fingerprint[fingerprint]
            await self._trace_publisher.publish(
                {
                    "type": EventType.ALERT.value,
                    "session_id": "alerts",
                    "data": {
                        "action": "upsert",
                        "fingerprint": alert.fingerprint,
                        "status": alert.status.value,
                        "alert": alert.model_dump(mode="json"),
                    },
                }
            )

        removed = set(self._last_versions) - set(current_versions)
        for fingerprint in removed:
            await self._trace_publisher.publish(
                {
                    "type": EventType.ALERT.value,
                    "session_id": "alerts",
                    "data": {
                        "action": "remove",
                        "fingerprint": fingerprint,
                        "status": "resolved",
                    },
                }
            )
        self._last_versions = current_versions

    @staticmethod
    def _version(alert: Alert) -> str:
        return alert.model_dump_json()


class MissingDiagnosisRunner:
    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        _ = (alert, trace_callback)
        raise RuntimeError("diagnosis runner is not configured")


class DefaultDiagnosisRunner:
    def __init__(
        self,
        *,
        execution_context: ToolExecutionContext,
        tool_registry: Any,
        config: SREAgentConfig,
    ) -> None:
        self._execution_context = execution_context
        self._tool_registry = tool_registry
        self._config = config

    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        query = _build_default_query(alert)
        result = await run_diagnosis(
            query=query,
            context=self._execution_context,
            variables={
                "alert_name": alert.alert_name,
                "severity": alert.severity.value,
                "labels": alert.labels,
                "annotations": alert.annotations,
                "aidc_id": self._config.global_.aidc_id,
            },
            tool_registry=self._tool_registry,
            checkpoint_dir=None,
            trace_callback=trace_callback,
        )
        return _diagnosis_session_from_state(alert=alert, state=result)


class DefaultReDiagnoseRunner:
    def __init__(
        self,
        *,
        execution_context: ToolExecutionContext,
        tool_registry: Any,
        config: SREAgentConfig,
    ) -> None:
        self._execution_context = execution_context
        self._tool_registry = tool_registry
        self._config = config

    async def re_diagnose(
        self,
        session: DiagnosisSession,
        context: dict[str, Any],
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        alert = session.alert
        query = (
            f"{_build_default_query(alert)}\n\n"
            f"Previous diagnosis session failed remediation attempts. "
            f"Failed candidates: {context.get('failed_candidates', [])}. "
            f"Re-diagnose and provide updated ranked candidates."
        )
        result = await run_diagnosis(
            query=query,
            context=self._execution_context,
            variables={
                "alert_name": alert.alert_name,
                "severity": alert.severity.value,
                "labels": alert.labels,
                "annotations": alert.annotations,
                "aidc_id": self._config.global_.aidc_id,
                "re_diagnosis_context": context,
            },
            tool_registry=self._tool_registry,
            session_id=session.session_id,
            checkpoint_dir=None,
            trace_callback=trace_callback,
        )
        updated = _diagnosis_session_from_state(alert=alert, state=result)
        return updated.model_copy(update={"re_diagnosis_round": session.re_diagnosis_round + 1})


def _build_default_query(alert: Alert) -> str:
    return (
        f"Diagnose alert '{alert.alert_name}' with severity '{alert.severity.value}'. "
        f"Summary: {alert.summary or 'n/a'}. Description: {alert.description or 'n/a'}. "
        "Use available tools to identify root cause and produce ranked candidates."
    )


def _diagnosis_session_from_state(*, alert: Alert, state: dict[str, Any]) -> DiagnosisSession:
    from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause, ThinkingTrace
    from sre_agent.models.remediation import RemediationPlan

    session_id = str(state.get("session_id") or alert.fingerprint or f"default-{datetime.now(UTC).timestamp()}").strip()
    summary = str(state.get("summary") or "diagnosis completed").strip()
    status = str(state.get("status") or "diagnosed").strip().lower()
    mapped_status = "diagnosed" if status in {"diagnosed", "resolved"} else "failed"

    remediation_plan = None
    raw_plan = state.get("remediation_plan")
    if isinstance(raw_plan, dict):
        try:
            remediation_plan = RemediationPlan.model_validate(raw_plan)
        except Exception:  # noqa: BLE001
            remediation_plan = None

    raw_diag = state.get("diagnosis_result")
    diagnosis_result = None
    if isinstance(raw_diag, dict):
        try:
            diagnosis_result = DiagnosisResult.model_validate(raw_diag)
        except Exception:  # noqa: BLE001
            diagnosis_result = None

    if diagnosis_result is None:
        diagnosis_result = DiagnosisResult(
            root_cause=summary,
            root_cause_layer="service",
            confidence=0.6 if mapped_status == "diagnosed" else 0.3,
            impact_summary=summary,
            triage_priority="P2",
            diagnosis_certainty="probable" if mapped_status == "diagnosed" else "ambiguous",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause=summary,
                    root_cause_layer="service",
                    confidence=0.6 if mapped_status == "diagnosed" else 0.3,
                    evidence_summary="default diagnosis runner synthesized candidate",
                    recommended_fix=remediation_plan,
                )
            ],
            recommended_fix=remediation_plan,
        )
    trace = None
    raw_trace_items = state.get("trace_items")
    if isinstance(raw_trace_items, list):
        try:
            converted = ThinkingTrace.from_langraph_state(raw_trace_items)
            if converted.steps:
                trace = converted
        except Exception:  # noqa: BLE001
            trace = None

    return DiagnosisSession(
        session_id=session_id,
        alert=alert,
        status=mapped_status,  # type: ignore[arg-type]
        diagnosis_result=diagnosis_result,
        trace=trace,
    )


def _log_dependency_summary(
    *,
    cfg: SREAgentConfig,
    services: AgentCServices,
    context: ToolExecutionContext,
    used_default_diagnosis_runner: bool,
    used_default_re_diagnose_runner: bool,
) -> None:
    channels = sorted(context.channels.keys())
    alert_mode = "degraded"
    if "alert" in channels:
        alert_mode = "real(injected_channel)"
    elif (cfg.global_.alertmanager_url or "").strip():
        alert_mode = "real(alertmanager_url)"
    memory_mode = "real" if services.memory is not None else "degraded"
    knowledge_mode = "real" if services.knowledge is not None else "degraded"
    ws_retention = _resolve_ws_max_events_per_session(cfg)
    LOGGER.warning(
        "startup dependencies: aidc_id=%s auth_audience=%s diagnosis_runner=%s re_diagnose_runner=%s channels=%s memory_db=%s knowledge_db=%s",
        cfg.global_.aidc_id,
        cfg.auth.audience,
        "default" if used_default_diagnosis_runner else "injected",
        "default" if used_default_re_diagnose_runner else "injected",
        channels if channels else ["none"],
        cfg.memory.db_dir,
        cfg.knowledge_base.persist_dir,
    )
    LOGGER.warning(
        "runtime modes: alert=%s memory=%s knowledge=%s ws_max_events_per_session=%s",
        alert_mode,
        memory_mode,
        knowledge_mode,
        ws_retention,
    )
    LOGGER.debug("service wiring: diagnosis_runner=%s memory=%s knowledge=%s", bool(services.diagnosis_runner), bool(services.memory), bool(services.knowledge))


@dataclass
class AgentCServices:
    diagnosis_runner: DiagnosisRunnerProtocol | None
    remediation_engine: RemediationEngine
    incident_handler: IncidentHandler
    ontology: OntologyGraph
    memory: Any
    knowledge: Any
    session_store: InMemorySessionStore
    loop_store: InMemoryLoopStore
    trace_publisher: InMemoryTracePublisher
    alert_store: InMemoryAlertStore
    audit_logger: AuditLogger
    chat_handler: ChatHandlerProtocol | None = None


def _resolve_alert_poll_interval_seconds(cfg: SREAgentConfig) -> float:
    default_value = 5.0
    candidate = getattr(cfg.global_, "alert_poll_interval_seconds", None)
    if candidate is None:
        extras = getattr(cfg.global_, "model_extra", None)
        if isinstance(extras, dict):
            candidate = extras.get("alert_poll_interval_seconds")
    try:
        value = float(candidate)
    except (TypeError, ValueError):
        return default_value
    return value if value > 0 else default_value


def _resolve_ws_max_events_per_session(cfg: SREAgentConfig) -> int:
    default_value = 1000
    candidate = getattr(cfg.global_, "ws_max_events_per_session", None)
    if candidate is None:
        extras = getattr(cfg.global_, "model_extra", None)
        if isinstance(extras, dict):
            candidate = extras.get("ws_max_events_per_session")
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return default_value
    return value if value > 0 else default_value


def _build_alert_polling_service(
    *,
    cfg: SREAgentConfig,
    execution_context: ToolExecutionContext,
    alert_store: InMemoryAlertStore,
    trace_publisher: InMemoryTracePublisher,
) -> AlertPollingService | None:
    channel = execution_context.channels.get("alert")
    if channel is None:
        alertmanager_url = (cfg.global_.alertmanager_url or "").strip()
        if not alertmanager_url:
            LOGGER.info("alert poller disabled: no alert channel injected and global.alertmanager_url is empty")
            return None
        channel = AlertChannel(
            alertmanager_url=alertmanager_url,
            prometheus_url=(cfg.global_.prometheus_url or "").strip(),
        )
    if not all(hasattr(channel, method) for method in ("connect", "disconnect", "get_firing_alerts")):
        LOGGER.warning("alert poller disabled: alert channel does not implement required methods")
        return None
    interval = _resolve_alert_poll_interval_seconds(cfg)
    return AlertPollingService(
        channel=channel,
        alert_store=alert_store,
        trace_publisher=trace_publisher,
        poll_interval_seconds=interval,
    )


def create_app(
    *,
    config: SREAgentConfig | None = None,
    diagnosis_runner: DiagnosisRunnerProtocol | None = None,
    re_diagnose_runner: ReDiagnoseRunnerProtocol | None = None,
    ontology: OntologyGraph | None = None,
    memory: Any | None = None,
    knowledge: Any | None = None,
    prometheus: Any | None = None,
    tool_registry=None,
    execution_context: ToolExecutionContext | None = None,
    chat_handler: ChatHandlerProtocol | None = None,
) -> FastAPI:
    cfg = config or SREAgentConfig()
    jwt_settings = resolve_jwt_settings(cfg.auth)

    ontology_graph = ontology or OntologyGraph(cfg.ontology.db_path)
    created_ontology = ontology is None
    memory_store = memory or create_memory_store(aidc_id=cfg.global_.aidc_id, db_dir=cfg.memory.db_dir)
    created_memory = memory is None
    registry = tool_registry or build_default_registry()
    context = execution_context or ToolExecutionContext()
    default_diagnosis_runner: DiagnosisRunnerProtocol | None = None
    default_re_diagnose_runner: ReDiagnoseRunnerProtocol | None = None
    if diagnosis_runner is None or re_diagnose_runner is None:
        try:
            if diagnosis_runner is None:
                default_diagnosis_runner = DefaultDiagnosisRunner(
                    execution_context=context,
                    tool_registry=registry,
                    config=cfg,
                )
            if re_diagnose_runner is None:
                default_re_diagnose_runner = DefaultReDiagnoseRunner(
                    execution_context=context,
                    tool_registry=registry,
                    config=cfg,
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"failed to assemble default diagnosis runtime: {exc}") from exc

    final_diagnosis_runner = diagnosis_runner or default_diagnosis_runner
    final_re_diagnose_runner = re_diagnose_runner or default_re_diagnose_runner
    if final_diagnosis_runner is None:
        raise RuntimeError("failed to initialize diagnosis runner")
    if final_re_diagnose_runner is None:
        raise RuntimeError("failed to initialize re_diagnose runner")

    approval_gate = ApprovalGate(
        timeout_seconds=cfg.remediation.approval_timeout,
        default_policy=cfg.remediation.default_policy,
    )
    wal = RollbackJournal(f"{cfg.remediation.wal_dir.rstrip('/')}/remediation.jsonl")
    validator = PlanValidator(registry, ontology=ontology_graph)
    engine = RemediationEngine(
        registry,
        approval_gate,
        wal,
        prometheus=prometheus,
        validator=validator,
        execution_context=context,
    )
    session_store = InMemorySessionStore()
    loop_store = InMemoryLoopStore()
    publisher = InMemoryTracePublisher(
        max_events_per_session=_resolve_ws_max_events_per_session(cfg),
    )
    alert_store = InMemoryAlertStore()
    loop = LoopOrchestrator(
        engine,
        prometheus=prometheus,
        memory=memory_store,
        config=LoopConfig(
            max_candidates=cfg.loop_orchestrator.max_candidates,
            cooldown_seconds=cfg.loop_orchestrator.cooldown_seconds,
            verification_window=cfg.loop_orchestrator.verification_window,
            enable_re_diagnosis=cfg.loop_orchestrator.enable_re_diagnosis,
            max_re_diagnosis_rounds=cfg.loop_orchestrator.max_re_diagnosis_rounds,
        ),
        re_diagnose_runner=final_re_diagnose_runner,
        trace_publisher=publisher,
    )
    handler = IncidentHandler(
        diagnosis_runner=final_diagnosis_runner,
        loop_orchestrator=loop,
        resource_lock=ResourceLock(),
        deduplicator=AlertDeduplicator(),
        correlator=AlertCorrelator(),
        session_store=session_store,
        loop_store=loop_store,
        trace_publisher=publisher,
        alert_store=alert_store,
    )
    runtime_chat_handler = chat_handler
    if runtime_chat_handler is None:
        try:
            runtime_chat_handler = ConversationalAgent(
                tool_registry=registry,
                tool_context=context,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("chat runtime is not available: %s", exc)
            runtime_chat_handler = None

    services = AgentCServices(
        diagnosis_runner=final_diagnosis_runner,
        remediation_engine=engine,
        incident_handler=handler,
        ontology=ontology_graph,
        memory=memory_store,
        knowledge=knowledge,
        session_store=session_store,
        loop_store=loop_store,
        trace_publisher=publisher,
        alert_store=alert_store,
        audit_logger=AuditLogger(),
        chat_handler=runtime_chat_handler,
    )
    alert_polling_service = _build_alert_polling_service(
        cfg=cfg,
        execution_context=context,
        alert_store=alert_store,
        trace_publisher=publisher,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if created_ontology:
            await ontology_graph.connect()
        if created_memory and hasattr(memory_store, "connect"):
            await memory_store.connect()
        if alert_polling_service is not None:
            await alert_polling_service.start()
        try:
            yield
        finally:
            if alert_polling_service is not None:
                await alert_polling_service.stop()
            if created_memory and hasattr(memory_store, "close"):
                await memory_store.close()
            if created_ontology:
                await ontology_graph.close()

    app = FastAPI(title="AIDC Auto SRE Agent", lifespan=lifespan)
    app.state.config = cfg
    app.state.jwt_settings = jwt_settings
    app.state.services = services
    _log_dependency_summary(
        cfg=cfg,
        services=services,
        context=context,
        used_default_diagnosis_runner=diagnosis_runner is None,
        used_default_re_diagnose_runner=re_diagnose_runner is None,
    )
    install_middlewares(app)
    app.include_router(build_api_router())
    app.include_router(build_websocket_router())
    return app
