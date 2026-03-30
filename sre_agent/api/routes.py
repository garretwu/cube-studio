"""REST routes for Agent C."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.auth.jwt import CurrentUser, get_current_user
from sre_agent.auth.rbac import require_role
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.memory import ConfigBaseline, IncidentRecord, LearnedPattern
from sre_agent.models.remediation import LoopResult, RemediationPlan, RemediationResult
from sre_agent.remediation.approval import ApprovalInput
from sre_agent.remediation.engine import RollbackResult
from sre_agent.skills import SkillRegistry


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    created_at: datetime
    tool_name: str | None = None
    metadata: dict[str, Any] | None = None


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


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: str
    alert_name: str
    severity: str
    fingerprint: str
    outcome: str | None = None
    duration_seconds: int = 0
    updated_at: datetime


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

    async def _maybe_await(value: Any) -> Any:
        if asyncio.iscoroutine(value):
            return await value
        return value

    def _call_with_optional_user_id(callable_obj: Any, *args: Any, user_id: str) -> Any:
        try:
            signature = inspect.signature(callable_obj)
        except (TypeError, ValueError):
            return callable_obj(*args)
        if "user_id" in signature.parameters:
            return callable_obj(*args, user_id=user_id)
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return callable_obj(*args, user_id=user_id)
        return callable_obj(*args)

    async def _invoke_chat_handler(chat_handler: Any, *, content: str, user_id: str) -> str:
        chat_method = getattr(chat_handler, "chat", None)
        if callable(chat_method):
            value = _call_with_optional_user_id(chat_method, content, user_id=user_id)
        else:
            value = _call_with_optional_user_id(chat_handler, content, user_id=user_id)
        response = await _maybe_await(value)
        return response if isinstance(response, str) else str(response)

    def _normalize_chat_history_item(item: Any, *, index: int) -> ChatHistoryMessage | None:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = item
        else:
            payload = {"content": str(item)}

        role = str(payload.get("role", "assistant")).strip().lower() or "assistant"
        if role not in {"user", "assistant", "tool"}:
            role = "assistant"
        message_id = str(payload.get("id") or f"chat-history-{index + 1}")
        content = str(payload.get("content", "")).strip()
        if not content:
            return None

        created_raw = payload.get("created_at")
        created_at = None
        if isinstance(created_raw, datetime):
            created_at = created_raw
        elif isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            except ValueError:
                created_at = None
        if created_at is None:
            created_at = datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        tool_name = payload.get("tool_name")
        metadata = payload.get("metadata")
        return ChatHistoryMessage(
            id=message_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=created_at,
            tool_name=str(tool_name) if tool_name is not None else None,
            metadata=metadata if isinstance(metadata, dict) else None,
        )

    async def _invoke_chat_history(chat_handler: Any, *, user_id: str) -> list[ChatHistoryMessage]:
        history_method = getattr(chat_handler, "get_history", None)
        if not callable(history_method):
            return []
        value = _call_with_optional_user_id(history_method, user_id=user_id)
        history_items = await _maybe_await(value)
        if not isinstance(history_items, list):
            return []
        normalized: list[ChatHistoryMessage] = []
        for idx, item in enumerate(history_items):
            converted = _normalize_chat_history_item(item, index=idx)
            if converted is not None:
                normalized.append(converted)
        return normalized

    def _extract_recommended_fix(session: DiagnosisSession) -> RemediationPlan | None:
        if session.diagnosis_result is None:
            return None
        if session.diagnosis_result.recommended_fix is not None:
            return session.diagnosis_result.recommended_fix
        for candidate in session.diagnosis_result.ranked_candidates:
            if candidate.recommended_fix is not None:
                return candidate.recommended_fix
        return None

    def _update_session_status(
        services: Any,
        *,
        session: DiagnosisSession,
        status: str,
        outcome: str | None = None,
    ) -> DiagnosisSession:
        update_status = getattr(services.session_store, "update_status", None)
        if callable(update_status):
            updated = update_status(session.session_id, status=status, outcome=outcome)
            if updated is not None:
                return updated
        updates: dict[str, Any] = {"status": status}
        if outcome is not None:
            updates["outcome"] = outcome
        updated = session.model_copy(update=updates)
        services.session_store.put(updated)
        return updated

    async def _publish_remediation_progress(
        services: Any,
        *,
        session_id: str,
        stage: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"stage": stage}
        if details:
            payload.update(details)
        await services.trace_publisher.publish(
            {
                "type": EventType.REMEDIATION_PROGRESS.value,
                "session_id": session_id,
                "data": payload,
            }
        )

    def _normalize_knowledge_document(item: Any, *, index: int) -> dict[str, Any]:
        source = ""
        category = "general"
        score: float | None = None
        title = ""
        excerpt = ""
        tags: list[str] = []
        metadata: dict[str, Any] = {}

        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = item
        else:
            payload = {"content": str(item)}

        if isinstance(payload.get("metadata"), dict):
            metadata = payload["metadata"]

        source = str(payload.get("source") or metadata.get("source") or "")
        category = str(payload.get("category") or metadata.get("category") or "general")
        title = str(payload.get("title") or metadata.get("title") or source or f"Document {index + 1}")
        excerpt = str(payload.get("excerpt") or payload.get("content") or metadata.get("excerpt") or "")

        raw_tags = payload.get("tags", metadata.get("tags", []))
        if isinstance(raw_tags, list):
            tags = [str(tag) for tag in raw_tags if str(tag).strip()]
        elif isinstance(raw_tags, str):
            tags = [segment.strip() for segment in raw_tags.split(",") if segment.strip()]

        raw_score = payload.get("score")
        if raw_score is not None:
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = None

        doc_id = str(payload.get("id") or metadata.get("id") or f"{category}-{index + 1}")
        result = {
            "id": doc_id,
            "title": title,
            "source": source,
            "category": category,
            "excerpt": excerpt,
            "tags": tags,
        }
        if score is not None:
            result["score"] = score
        return result

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
        plan = _extract_recommended_fix(session)
        if plan is not None:
            services.remediation_engine.register_plan(session.session_id, plan)
            session = session.model_copy(update={"status": "approval_required"})
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

    @router.get("/sessions")
    async def list_sessions(
        request: Request,
        limit: int = 50,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[SessionSummary]]:
        _ = user
        services = _services(request)
        records = []
        if hasattr(services.session_store, "list_recent"):
            records = services.session_store.list_recent(limit=limit)
        payload = [
            SessionSummary(
                session_id=session.session_id,
                status=session.status,
                alert_name=session.alert.alert_name,
                severity=session.alert.severity.value,
                fingerprint=session.alert.fingerprint,
                outcome=session.outcome,
                duration_seconds=session.duration_seconds,
                updated_at=updated_at,
            )
            for session, updated_at in records
        ]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

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

    @router.get("/sessions/{session_id}/trace")
    async def get_session_trace(
        session_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        services = _services(request)
        session = services.session_store.get(session_id)
        if session is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="session not found"),
                trace_id=_trace_id(request),
            )
        if session.trace is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        payload = [item.model_dump(mode="json") for item in session.trace.steps]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

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
        trace_id = _trace_id(request)
        services.audit_logger.record(user, "approve", session_id=session_id, trace_id=trace_id)
        transition_status = getattr(services.session_store, "transition_status", None)
        if callable(transition_status):
            target_status = "remediating" if approval.approved else "rejected"
            target_outcome = None if approval.approved else "rejected"
            session = transition_status(
                session_id,
                expected_statuses={"approval_required"},
                status=target_status,
                outcome=target_outcome,
            )
        else:
            session = services.session_store.get(session_id)
            if session is not None and session.status == "approval_required":
                session = _update_session_status(
                    services,
                    session=session,
                    status="remediating" if approval.approved else "rejected",
                    outcome=None if approval.approved else "rejected",
                )
            else:
                session = None
        if session is None:
            current = services.session_store.get(session_id)
            if current is None:
                return SREResponse(
                    success=False,
                    error=SREError(code=ErrorCode.VALIDATION_ERROR, message="session not found"),
                    trace_id=trace_id,
                )
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.VALIDATION_ERROR,
                    message=f"session {session_id} is not waiting for approval (status={current.status})",
                ),
                trace_id=trace_id,
            )
        if not approval.approved:
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="approval_rejected",
                details={"user": approval.user, "reason": approval.reason or ""},
            )
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_APPROVAL_DENIED,
                    message=approval.reason or "approval denied",
                ),
                trace_id=trace_id,
            )

        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="execution_started",
            details={"user": approval.user},
        )
        try:
            result = await services.remediation_engine.approve_and_execute(session_id, approval)
        except KeyError:
            _update_session_status(services, session=session, status="failed", outcome="failed")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_failed",
                details={"error": "remediation plan not found"},
            )
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_PLAN_INVALID,
                    message=f"remediation plan not found for session {session_id}",
                ),
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            _update_session_status(services, session=session, status="failed", outcome="failed")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_failed",
                details={"error": str(exc)},
            )
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.REMEDIATION_EXECUTION_FAILED, message=str(exc)),
                trace_id=trace_id,
            )

        if result.success:
            _update_session_status(services, session=session, status="resolved", outcome="resolved")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_succeeded",
                details={"plan_id": result.plan_id, "steps_completed": result.steps_completed},
            )
            return SREResponse(success=True, data=result, trace_id=trace_id)

        _update_session_status(services, session=session, status="failed", outcome="failed")
        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="execution_failed",
            details={
                "plan_id": result.plan_id,
                "error": result.error or "execution failed",
                "rolled_back": result.rolled_back,
            },
        )
        return SREResponse(
            success=False,
            error=SREError(code=ErrorCode.REMEDIATION_EXECUTION_FAILED, message=result.error or "execution failed"),
            trace_id=trace_id,
        )

    @router.post("/remediate/{session_id}/rollback")
    async def rollback(
        session_id: str,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[RollbackResult]:
        services = _services(request)
        trace_id = _trace_id(request)
        services.audit_logger.record(user, "rollback", session_id=session_id, trace_id=trace_id)
        session = services.session_store.get(session_id)
        if session is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="session not found"),
                trace_id=trace_id,
            )
        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="rollback_started",
            details={"user": user.username},
        )
        try:
            result = await services.remediation_engine.rollback(session_id)
        except Exception as exc:  # noqa: BLE001
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="rollback_failed",
                details={"error": str(exc)},
            )
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.REMEDIATION_ROLLBACK_FAILED, message=str(exc)),
                trace_id=trace_id,
            )
        if result.success:
            _update_session_status(services, session=session, status="failed", outcome="rolled_back")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="rollback_succeeded",
                details={"recovered_actions": len(result.recovered_actions)},
            )
            return SREResponse(success=True, data=result, trace_id=trace_id)

        _update_session_status(services, session=session, status="failed", outcome="rollback_failed")
        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="rollback_failed",
            details={"error": result.error or "rollback failed"},
        )
        return SREResponse(
            success=False,
            error=SREError(code=ErrorCode.REMEDIATION_ROLLBACK_FAILED, message=result.error or "rollback failed"),
            trace_id=trace_id,
        )

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

    @router.get("/memory/baseline")
    async def get_memory_baseline(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        services = _services(request)
        memory = services.memory
        baseline = None
        if hasattr(memory, "get_config_baseline"):
            baseline = await _maybe_await(memory.get_config_baseline(getattr(request.app.state.config.global_, "aidc_id", None)))
        elif hasattr(memory, "get_baseline"):
            baseline = await _maybe_await(memory.get_baseline(getattr(request.app.state.config.global_, "aidc_id", None)))

        if baseline is None:
            baseline = ConfigBaseline(aidc_id=request.app.state.config.global_.aidc_id)
        payload = baseline.model_dump(mode="json") if hasattr(baseline, "model_dump") else baseline
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

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
            reply = await _invoke_chat_handler(
                services.chat_handler,
                content=message.content,
                user_id=user.user_id,
            )
        return SREResponse(success=True, data=ChatResponse(reply=reply), trace_id=_trace_id(request))

    @router.get("/chat/history")
    async def chat_history(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[ChatHistoryMessage]]:
        services = _services(request)
        if services.chat_handler is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        history = await _invoke_chat_history(
            services.chat_handler,
            user_id=user.user_id,
        )
        return SREResponse(success=True, data=history, trace_id=_trace_id(request))

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

    @router.get("/knowledge/documents")
    async def knowledge_documents(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))

        documents: list[Any] = []
        if hasattr(knowledge, "list_documents"):
            documents = await _maybe_await(knowledge.list_documents())
        elif hasattr(knowledge, "search"):
            documents = await _maybe_await(knowledge.search(query="", top_k=50))

        if not isinstance(documents, list):
            documents = []
        payload = [_normalize_knowledge_document(item, index=idx) for idx, item in enumerate(documents)]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/skills")
    async def list_skills(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        registry = SkillRegistry()
        skills = registry.discover()
        payload = [
            {
                "id": item.id,
                "name": item.name,
                "scope": item.scope,
                "summary": item.summary,
                "source": item.source,
                "permissions": item.permissions,
                "match_score": item.match_score,
            }
            for item in skills
        ]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    return router
