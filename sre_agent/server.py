"""FastAPI application factory for Agent C."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Protocol

from fastapi import FastAPI

from sre_agent.agent import run_diagnosis
from sre_agent.api import build_api_router, build_websocket_router, install_middlewares
from sre_agent.api.routes import AuditLogger
from sre_agent.auth.jwt import CurrentUser, JWTSettings, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.concurrency import AlertCorrelator, AlertDeduplicator, ResourceLock
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.alert import Alert, AlertSeverity
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import WSEvent
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.remediation import ApprovalGate, IncidentHandler, LoopConfig, LoopOrchestrator, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.tools import ToolExecutionContext, build_default_registry

LOGGER = logging.getLogger(__name__)


class DiagnosisRunnerProtocol(Protocol):
    async def adiagnose(self, alert: Alert) -> DiagnosisSession: ...


class ReDiagnoseRunnerProtocol(Protocol):
    async def re_diagnose(self, session: DiagnosisSession, context: dict[str, Any]) -> DiagnosisSession: ...


class ChatHandlerProtocol(Protocol):
    async def __call__(self, message: str) -> str: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, DiagnosisSession] = {}

    def put(self, session: DiagnosisSession) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> DiagnosisSession | None:
        return self._sessions.get(session_id)


class InMemoryLoopStore:
    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def put(self, result: Any) -> None:
        self._results[result.session_id] = result

    def get(self, session_id: str) -> Any:
        return self._results.get(session_id)


class InMemoryTracePublisher:
    def __init__(self) -> None:
        self._events: dict[str, list[WSEvent]] = {}
        self._condition = asyncio.Condition()
        self._session_seq: dict[str, int] = {}

    async def publish(self, event: dict[str, Any] | WSEvent) -> None:
        ws_event = event if isinstance(event, WSEvent) else WSEvent.model_validate(event)
        data = dict(ws_event.data)
        if "event_id" not in data:
            next_seq = self._session_seq.get(ws_event.session_id, 0) + 1
            self._session_seq[ws_event.session_id] = next_seq
            data["event_id"] = str(next_seq)
            ws_event = ws_event.model_copy(update={"data": data})
        async with self._condition:
            self._events.setdefault(ws_event.session_id, []).append(ws_event)
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
                index = len(events)
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


class MissingDiagnosisRunner:
    async def adiagnose(self, alert: Alert) -> DiagnosisSession:
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

    async def adiagnose(self, alert: Alert) -> DiagnosisSession:
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

    async def re_diagnose(self, session: DiagnosisSession, context: dict[str, Any]) -> DiagnosisSession:
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
    from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause
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

    return DiagnosisSession(
        session_id=session_id,
        alert=alert,
        status=mapped_status,  # type: ignore[arg-type]
        diagnosis_result=diagnosis_result,
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
    publisher = InMemoryTracePublisher()
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
        chat_handler=chat_handler,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if created_ontology:
            await ontology_graph.connect()
        if created_memory and hasattr(memory_store, "connect"):
            await memory_store.connect()
        try:
            yield
        finally:
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
