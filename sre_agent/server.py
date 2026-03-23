"""FastAPI application factory for Agent C."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Protocol

from fastapi import FastAPI

from sre_agent.api import build_api_router, build_websocket_router, install_middlewares
from sre_agent.api.routes import AuditLogger
from sre_agent.auth.jwt import CurrentUser, JWTSettings, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.concurrency import AlertCorrelator, AlertDeduplicator, ResourceLock
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import WSEvent
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.remediation import ApprovalGate, IncidentHandler, LoopConfig, LoopOrchestrator, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.tools import ToolExecutionContext, build_default_registry


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

    async def publish(self, event: dict[str, Any] | WSEvent) -> None:
        ws_event = event if isinstance(event, WSEvent) else WSEvent.model_validate(event)
        self._events.setdefault(ws_event.session_id, []).append(ws_event)

    async def subscribe(self, session_id: str, after: str | None = None) -> AsyncIterator[WSEvent]:
        events = list(self._events.get(session_id, []))
        started = after is None
        for event in events:
            event_id = event.data.get("event_id")
            if after is not None and not started:
                started = str(event_id) == str(after)
                continue
            yield event


class MissingDiagnosisRunner:
    async def adiagnose(self, alert: Alert) -> DiagnosisSession:
        raise RuntimeError("diagnosis runner is not configured")


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
    memory_store = memory or create_memory_store(aidc_id=cfg.global_.aidc_id, db_dir=cfg.memory.db_dir)
    registry = tool_registry or build_default_registry()
    context = execution_context or ToolExecutionContext()
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
        re_diagnose_runner=re_diagnose_runner,
        trace_publisher=publisher,
    )
    handler = IncidentHandler(
        diagnosis_runner=diagnosis_runner or MissingDiagnosisRunner(),
        loop_orchestrator=loop,
        resource_lock=ResourceLock(),
        deduplicator=AlertDeduplicator(),
        correlator=AlertCorrelator(),
        session_store=session_store,
        loop_store=loop_store,
    )
    services = AgentCServices(
        diagnosis_runner=diagnosis_runner,
        remediation_engine=engine,
        incident_handler=handler,
        ontology=ontology_graph,
        memory=memory_store,
        knowledge=knowledge,
        session_store=session_store,
        loop_store=loop_store,
        trace_publisher=publisher,
        audit_logger=AuditLogger(),
        chat_handler=chat_handler,
    )

    app = FastAPI(title="AIDC Auto SRE Agent")
    app.state.config = cfg
    app.state.jwt_settings = jwt_settings
    app.state.services = services
    install_middlewares(app)
    app.include_router(build_api_router())
    app.include_router(build_websocket_router())
    return app
