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

    @router.get("/ontology")
    async def get_topology(
        request: Request,
        entity_type: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        entities = _services(request).ontology.find_entities(entity_type=entity_type)
        return SREResponse(success=True, data=[entity.model_dump(mode="json") for entity in entities], trace_id=_trace_id(request))

    @router.get("/ontology/{entity_id}/blast-radius")
    async def get_blast_radius(
        entity_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        radius = _services(request).ontology.get_blast_radius(entity_id)
        payload = {
            "root_entity_id": radius["root_entity_id"],
            "affected_count": radius["affected_count"],
            "affected_entities": [entity.model_dump(mode="json") for entity in radius["affected_entities"]],
        }
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

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
