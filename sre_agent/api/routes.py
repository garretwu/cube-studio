"""REST routes for Agent C."""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.auth.jwt import CurrentUser, get_current_user
from sre_agent.auth.rbac import require_role
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import WSEvent
from sre_agent.models.memory import IncidentRecord, LearnedPattern
from sre_agent.models.remediation import LoopResult, RemediationResult
from sre_agent.remediation.approval import ApprovalInput
from sre_agent.remediation.engine import RollbackResult


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str


class OntologyQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class OntologyPathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)


class OntologyBlastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)


class OntologyRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)


class TopologySnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    active_alerts: int = 0
    recent_events: list[str] = Field(default_factory=list)


class AlertSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[dict[str, Any]] = Field(default_factory=list)
    clusters: list[dict[str, Any]] = Field(default_factory=list)


class AuditLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str = ""
    trace_id: str = ""
    action: str
    tool: str | None = None
    params: dict[str, Any] | None = None
    user: str
    role: str
    result: str = "success"
    details: str = ""
    prev_hash: str
    record_hash: str


class AuditLogger:
    def __init__(self, log_path: str = "./data/audit.jsonl") -> None:
        self.log_path = Path(log_path)
        self._prev_hash = "genesis"
        if self.log_path.exists():
            lines = [line for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                self._prev_hash = AuditLog.model_validate_json(lines[-1]).record_hash

    def record(
        self,
        user: CurrentUser,
        action: str,
        *,
        session_id: str = "",
        trace_id: str = "",
        tool: str | None = None,
        params: dict[str, Any] | None = None,
        result: str = "success",
        details: str = "",
    ) -> AuditLog:
        provisional = AuditLog(
            session_id=session_id,
            trace_id=trace_id,
            action=action,
            tool=tool,
            params=params,
            user=user.username,
            role=user.role,
            result=result,
            details=details,
            prev_hash=self._prev_hash,
            record_hash="",
        )
        content = provisional.model_dump_json(exclude={"record_hash"})
        record_hash = hashlib.sha256(f"{self._prev_hash}|{content}".encode("utf-8")).hexdigest()
        entry = provisional.model_copy(update={"record_hash": record_hash})
        self._prev_hash = record_hash
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    def _trace_id(request: Request) -> str:
        return getattr(request.state, "trace_id", "")

    def _services(request: Request) -> Any:
        return request.app.state.services

    def _normalize_ontology_entities(entities: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for entity in entities:
            model_dump = getattr(entity, "model_dump", None)
            if callable(model_dump):
                normalized.append(model_dump(mode="json"))
            elif isinstance(entity, dict):
                normalized.append(entity)
            else:
                normalized.append({"value": entity})
        return normalized

    def _normalize_ontology_edges(edges: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for edge in edges:
            model_dump = getattr(edge, "model_dump", None)
            if callable(model_dump):
                normalized.append(model_dump(mode="json"))
            elif isinstance(edge, dict):
                normalized.append(edge)
            else:
                normalized.append({"value": edge})
        return normalized

    def _normalize_blast_radius_payload(radius: dict[str, Any]) -> dict[str, Any]:
        affected_entities = radius.get("affected_entities", [])
        normalized_affected: list[dict[str, Any]] = []
        for entity in affected_entities:
            model_dump = getattr(entity, "model_dump", None)
            if callable(model_dump):
                normalized_affected.append(model_dump(mode="json"))
            elif isinstance(entity, dict):
                normalized_affected.append(entity)
            else:
                normalized_affected.append({"value": entity})

        root_entity_id = radius.get("root_entity_id", radius.get("root"))
        affected_count = radius.get("affected_count")
        if affected_count is None:
            affected = radius.get("affected")
            if isinstance(affected, dict):
                affected_count = len(affected)
            elif isinstance(affected_entities, list):
                affected_count = len(affected_entities)
            else:
                affected_count = 0

        payload: dict[str, Any] = {
            "root_entity_id": root_entity_id,
            "affected_count": affected_count,
            "affected_entities": normalized_affected,
        }
        if "affected" in radius:
            payload["affected"] = radius["affected"]
        return payload

    async def _refresh_ontology_entity(ontology: Any, entity_id: str) -> dict[str, Any]:
        refresh_entity = getattr(ontology, "refresh_entity", None)
        if not callable(refresh_entity):
            return {
                "entity_id": entity_id,
                "refreshed": False,
                "reason": "refresh_entity is not implemented on ontology dependency",
            }
        value = refresh_entity(entity_id)
        if asyncio.iscoroutine(value):
            value = await value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(value, dict):
            return value
        return {"value": value}

    @router.post("/diagnose")
    async def diagnose(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[DiagnosisSession]:
        services = _services(request)
        if services.diagnosis_runner is None:
            raise HTTPException(status_code=500, detail="diagnosis runner is not configured")
        session = await services.diagnosis_runner.adiagnose(alert)
        services.session_store.put(session)
        return SREResponse(success=True, data=session, trace_id=_trace_id(request))

    @router.post("/handle")
    async def handle_alert(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[LoopResult]:
        _ = user
        services = _services(request)
        response = await services.incident_handler.handle(alert)
        return response.model_copy(update={"trace_id": _trace_id(request)})

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[DiagnosisSession]:
        _ = user
        services = _services(request)
        session = services.session_store.get(session_id)
        if session is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="session not found"),
                trace_id=_trace_id(request),
            )
        return SREResponse(success=True, data=session, trace_id=_trace_id(request))

    @router.get("/sessions/{session_id}/loop")
    async def get_loop_result(
        session_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[LoopResult]:
        _ = user
        services = _services(request)
        loop_result = services.loop_store.get(session_id)
        if loop_result is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="loop result not found"),
                trace_id=_trace_id(request),
            )
        return SREResponse(success=True, data=loop_result, trace_id=_trace_id(request))

    @router.post("/remediate/{session_id}/approve")
    async def approve_remediation(
        session_id: str,
        approval: ApprovalInput,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[RemediationResult]:
        services = _services(request)
        services.audit_logger.record(user, "approve", session_id=session_id, trace_id=_trace_id(request))
        result = await services.remediation_engine.approve_and_execute(session_id, approval)
        return SREResponse(success=result.success, data=result if result.success else None, error=None if result.success else SREError(code=ErrorCode.REMEDIATION_APPROVAL_DENIED, message=result.error or "approval denied"), trace_id=_trace_id(request))

    @router.post("/remediate/{session_id}/rollback")
    async def rollback(
        session_id: str,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[RollbackResult]:
        services = _services(request)
        services.audit_logger.record(user, "rollback", session_id=session_id, trace_id=_trace_id(request))
        result = await services.remediation_engine.rollback(session_id)
        return SREResponse(success=result.success, data=result if result.success else None, error=None if result.success else SREError(code=ErrorCode.REMEDIATION_ROLLBACK_FAILED, message=result.error or "rollback failed"), trace_id=_trace_id(request))

    @router.get("/topology")
    async def get_topology_snapshot(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[TopologySnapshotResponse]:
        _ = user
        services = _services(request)
        payload = TopologySnapshotResponse(
            nodes=_normalize_ontology_entities(services.ontology.list_entities()),
            edges=_normalize_ontology_edges(services.ontology.list_edges()),
            active_alerts=0,
            recent_events=[],
        )
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/alerts")
    async def get_alerts_snapshot(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[AlertSnapshotResponse]:
        _ = user
        services = _services(request)
        snapshot = services.alert_store.snapshot()
        payload = AlertSnapshotResponse(alerts=snapshot.get("alerts", []), clusters=snapshot.get("clusters", []))
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/ontology")
    async def get_topology(
        request: Request,
        entity_type: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        entities = _services(request).ontology.find_entities(entity_type=entity_type)
        return SREResponse(
            success=True,
            data=_normalize_ontology_entities(entities),
            trace_id=_trace_id(request),
        )

    @router.post("/ontology/query")
    async def query_ontology(
        payload: OntologyQueryRequest,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        entities = _services(request).ontology.find_entities(payload.entity_type, payload.filters)
        return SREResponse(
            success=True,
            data=_normalize_ontology_entities(entities),
            trace_id=_trace_id(request),
        )

    @router.get("/ontology/path")
    async def get_ontology_path(
        from_id: str,
        to_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[str] | None]:
        _ = user
        path = _services(request).ontology.get_path(from_id, to_id)
        return SREResponse(success=True, data=path, trace_id=_trace_id(request))

    @router.post("/ontology/path")
    async def get_ontology_path_post(
        payload: OntologyPathRequest,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[str] | None]:
        _ = user
        path = _services(request).ontology.get_path(payload.from_id, payload.to_id)
        return SREResponse(success=True, data=path, trace_id=_trace_id(request))

    @router.get("/ontology/{entity_id}/blast-radius")
    async def get_blast_radius(
        entity_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        radius = _services(request).ontology.get_blast_radius(entity_id)
        payload = _normalize_blast_radius_payload(radius)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.post("/ontology/blast")
    async def post_blast_radius(
        payload: OntologyBlastRequest,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        radius = _services(request).ontology.get_blast_radius(payload.entity_id)
        return SREResponse(
            success=True,
            data=_normalize_blast_radius_payload(radius),
            trace_id=_trace_id(request),
        )

    @router.post("/ontology/refresh")
    async def refresh_ontology_entity(
        payload: OntologyRefreshRequest,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        result = await _refresh_ontology_entity(_services(request).ontology, payload.entity_id)
        return SREResponse(success=True, data=result, trace_id=_trace_id(request))

    @router.get("/memory/incidents")
    async def list_incidents(
        request: Request,
        last: int = 10,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[IncidentRecord]]:
        _ = user
        incidents = await _services(request).memory.list_recent(last)
        return SREResponse(success=True, data=incidents, trace_id=_trace_id(request))

    @router.get("/memory/patterns")
    async def list_patterns(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[LearnedPattern]]:
        _ = user
        patterns = await _services(request).memory.get_known_patterns()
        return SREResponse(success=True, data=patterns, trace_id=_trace_id(request))

    @router.post("/chat")
    async def chat(
        message: ChatMessage,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[ChatResponse]:
        _ = user
        services = _services(request)
        if services.chat_handler is None:
            reply = "chat handler is not configured"
        else:
            response = await services.chat_handler(message.content)
            reply = response if isinstance(response, str) else str(response)
        return SREResponse(success=True, data=ChatResponse(reply=reply), trace_id=_trace_id(request))

    @router.get("/knowledge/search")
    async def knowledge_search(
        query: str,
        request: Request,
        category: str | None = None,
        top_k: int = 5,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        results = await _services(request).knowledge.search(query=query, category=category, top_k=top_k)
        payload = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in results]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    return router
