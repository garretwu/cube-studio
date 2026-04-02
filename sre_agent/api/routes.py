"""REST routes for Agent C."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import re
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
from sre_agent.concurrency.resource_lock import ResourceLockedError
from sre_agent.skills import SkillRegistry


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    session_id: str | None = None


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    meta: dict[str, Any] | None = None
    display: dict[str, Any] | None = None


class ChatHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    created_at: datetime
    tool_name: str | None = None
    metadata: dict[str, Any] | None = None
    display: dict[str, Any] | None = None


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
    snapshot_id: str | None = None
    last_synced_at: datetime | None = None
    sync_state: Literal["idle", "syncing", "ready", "degraded", "error"] = "idle"


class TopologyStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str | None = None
    sync_state: Literal["idle", "syncing", "ready", "degraded", "error"] = "idle"
    mode: str = "static"
    last_synced_at: datetime | None = None
    last_started_at: datetime | None = None
    last_error: str | None = None
    scanner_counts: dict[str, dict[str, int]] = Field(default_factory=dict)


class AlertSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[dict[str, Any]] = Field(default_factory=list)
    clusters: list[dict[str, Any]] = Field(default_factory=list)


class ToolChannelStatusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    health: Literal["ready", "degraded", "unavailable", "disabled"] = "unavailable"
    required_by_tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    mode: str = "unknown"
    last_error: str | None = None
    last_checked_at: datetime | None = None


class ToolChannelsStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_mode: Literal["strict", "degraded"] = "degraded"
    channels: list[ToolChannelStatusItem] = Field(default_factory=list)


class LLMRuntimeStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool = True
    ready: bool = False
    api_key_configured: bool = False
    api_key_source: str = "none"
    api_key_length: int = 0
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    reason: str | None = None


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

    def _compact_affected_entity(entity: Any) -> dict[str, Any]:
        if hasattr(entity, "model_dump"):
            payload = entity.model_dump(mode="json")
        elif isinstance(entity, dict):
            payload = dict(entity)
        else:
            payload = {"id": str(entity), "entity_type": "unknown", "properties": {}}
        properties = payload.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        key_attributes: dict[str, Any] = {}
        for key in ("namespace", "node", "service", "pod", "host", "ip", "job", "role", "port"):
            value = properties.get(key)
            if value is not None and str(value).strip():
                key_attributes[key] = value
        return {
            "id": str(payload.get("id", "")),
            "type": str(payload.get("entity_type", "unknown")),
            "name": payload.get("name"),
            "status": payload.get("status"),
            "key_attributes": key_attributes,
            "properties": properties,
        }

    def _normalize_blast_radius_payload(radius: dict[str, Any]) -> dict[str, Any]:
        affected_entities = radius.get("affected_entities", [])
        normalized_affected: list[dict[str, Any]] = []
        compact_entities: list[dict[str, Any]] = []
        for entity in affected_entities:
            model_dump = getattr(entity, "model_dump", None)
            if callable(model_dump):
                payload = model_dump(mode="json")
                normalized_affected.append(payload)
            elif isinstance(entity, dict):
                payload = entity
                normalized_affected.append(payload)
            else:
                payload = {"value": entity}
                normalized_affected.append(payload)
            compact_entities.append(_compact_affected_entity(payload))

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
            "entities": compact_entities,
        }
        if "affected" in radius:
            payload["affected"] = radius["affected"]
        return payload

    def _tool_channel_status_payload(services: Any) -> ToolChannelsStatusResponse:
        def _parse_iso(value: str) -> datetime | None:
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None

        raw = getattr(services, "tool_channel_status", {}) or {}
        rows: list[ToolChannelStatusItem] = []
        if isinstance(raw, dict):
            for name, payload in sorted(raw.items(), key=lambda item: str(item[0])):
                data = payload if isinstance(payload, dict) else {}
                health = str(data.get("health", "unavailable")).strip().lower()
                if health not in {"ready", "degraded", "unavailable", "disabled"}:
                    health = "unavailable"
                rows.append(
                    ToolChannelStatusItem(
                        name=str(data.get("name", name)),
                        health=health,  # type: ignore[arg-type]
                        required_by_tools=[str(item) for item in data.get("required_by_tools", []) if str(item).strip()],
                        enabled=bool(data.get("enabled", True)),
                        mode=str(data.get("mode", "unknown")),
                        last_error=str(data["last_error"]) if data.get("last_error") else None,
                        last_checked_at=_parse_iso(str(data.get("last_checked_at", "")))
                        if isinstance(data.get("last_checked_at"), str)
                        else None,
                    )
                )
        runtime_mode = str(getattr(services, "tool_runtime_mode", "degraded")).strip().lower()
        if runtime_mode != "strict":
            runtime_mode = "degraded"
        return ToolChannelsStatusResponse(runtime_mode=runtime_mode, channels=rows)

    def _llm_runtime_status_payload(services: Any) -> LLMRuntimeStatusResponse:
        payload = getattr(services, "llm_runtime_status", {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        return LLMRuntimeStatusResponse(
            required=bool(payload.get("required", True)),
            ready=bool(payload.get("ready", False)),
            api_key_configured=bool(payload.get("api_key_configured", False)),
            api_key_source=str(payload.get("api_key_source", "none")),
            api_key_length=int(payload.get("api_key_length", 0) or 0),
            model=str(payload.get("model", "gpt-4o-mini")),
            base_url=str(payload["base_url"]) if payload.get("base_url") else None,
            reason=str(payload["reason"]) if payload.get("reason") else None,
        )

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

    def _call_with_optional_kwargs(callable_obj: Any, *args: Any, optional_kwargs: dict[str, Any] | None = None) -> Any:
        kwargs = {
            key: value
            for key, value in (optional_kwargs or {}).items()
            if value is not None
        }
        try:
            signature = inspect.signature(callable_obj)
        except (TypeError, ValueError):
            return callable_obj(*args, **kwargs)
        accepted: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in signature.parameters:
                accepted[key] = value
        if accepted:
            return callable_obj(*args, **accepted)
        for parameter in signature.parameters.values():
            if parameter.kind == inspect.Parameter.VAR_KEYWORD:
                return callable_obj(*args, **kwargs)
        return callable_obj(*args)

    async def _invoke_chat_handler(
        chat_handler: Any,
        *,
        content: str,
        user_id: str,
        session_id: str | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> str:
        chat_method = getattr(chat_handler, "chat", None)
        if callable(chat_method):
            value = _call_with_optional_kwargs(
                chat_method,
                content,
                optional_kwargs={
                    "user_id": user_id,
                    "session_id": session_id,
                    "session_context": session_context,
                },
            )
        else:
            value = _call_with_optional_kwargs(
                chat_handler,
                content,
                optional_kwargs={
                    "user_id": user_id,
                    "session_id": session_id,
                    "session_context": session_context,
                },
            )
        response = await _maybe_await(value)
        return response if isinstance(response, str) else str(response)

    def _extract_chat_display(content: str) -> dict[str, Any]:
        text = str(content or "")
        think_pattern = re.compile(r"<think>(.*?)</think>", flags=re.IGNORECASE | re.DOTALL)
        matches = list(think_pattern.finditer(text))
        thinking_raw = "\n\n".join(match.group(0).strip() for match in matches if match.group(0).strip()) or None
        answer = think_pattern.sub("", text).strip()
        if not answer:
            answer = text.strip()
        return {
            "answer": answer,
            "thinking_raw": thinking_raw,
            "has_thinking": bool(thinking_raw),
        }

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
        display = payload.get("display")
        if not isinstance(display, dict) and role == "assistant":
            display = _extract_chat_display(content)
        return ChatHistoryMessage(
            id=message_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            created_at=created_at,
            tool_name=str(tool_name) if tool_name is not None else None,
            metadata=metadata if isinstance(metadata, dict) else None,
            display=display if isinstance(display, dict) else None,
        )

    async def _invoke_chat_history(
        chat_handler: Any,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> list[ChatHistoryMessage]:
        history_method = getattr(chat_handler, "get_history", None)
        if not callable(history_method):
            return []
        value = _call_with_optional_kwargs(
            history_method,
            optional_kwargs={"user_id": user_id, "session_id": session_id},
        )
        history_items = await _maybe_await(value)
        if not isinstance(history_items, list):
            return []
        normalized: list[ChatHistoryMessage] = []
        for idx, item in enumerate(history_items):
            converted = _normalize_chat_history_item(item, index=idx)
            if converted is not None:
                normalized.append(converted)
        return normalized

    def _estimate_token_count(text: str) -> int:
        compact = " ".join(str(text or "").split())
        if not compact:
            return 0
        return max(1, len(compact) // 4)

    def _extract_trace_items(session_payload: dict[str, Any], *, limit: int) -> tuple[list[str], int]:
        trace = session_payload.get("trace")
        if not isinstance(trace, dict):
            return [], 0
        raw_steps = trace.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            return [], 0
        selected = raw_steps[-limit:]
        lines: list[str] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            thought = str(item.get("thought", "")).strip()
            action_type = str(item.get("action_type", "")).strip()
            tool_name = str(item.get("tool_name", "")).strip()
            if thought:
                prefix = f"{action_type} {tool_name}".strip()
                if prefix:
                    lines.append(f"- {prefix}: {thought}")
                else:
                    lines.append(f"- {thought}")
                continue
            tool = str(item.get("tool", "")).strip()
            result = item.get("result")
            if tool:
                lines.append(f"- observation {tool}: {str(result)[:220]}")
        return lines, len(selected)

    def _build_chat_session_context(
        services: Any,
        *,
        session_id: str | None,
        trace_limit: int = 12,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        scoped_session_id = (session_id or "").strip()
        empty_meta = {
            "context_applied": False,
            "session_id": scoped_session_id or None,
            "trace_steps_used": 0,
            "context_tokens_estimate": 0,
        }
        if not scoped_session_id:
            return None, empty_meta

        get_session = getattr(services.session_store, "get", None)
        session = get_session(scoped_session_id) if callable(get_session) else None
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"diagnosis session not found: {scoped_session_id}",
            )

        if hasattr(session, "model_dump"):
            payload = session.model_dump(mode="json")
        elif isinstance(session, dict):
            payload = dict(session)
        else:
            payload = {"session_id": scoped_session_id, "value": str(session)}

        diagnosis = payload.get("diagnosis_result")
        if not isinstance(diagnosis, dict):
            diagnosis = payload.get("diagnosis")
        diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
        root_cause = str(diagnosis.get("root_cause", "")).strip()
        root_layer = str(diagnosis.get("root_cause_layer", "")).strip()
        confidence = diagnosis.get("confidence")
        impact_summary = str(diagnosis.get("impact_summary", "")).strip()
        affected_services = diagnosis.get("affected_services")
        if not isinstance(affected_services, list):
            affected_services = []

        alert_payload = payload.get("alert")
        if not isinstance(alert_payload, dict):
            alert_payload = {}
        topology_summary = str(
            (alert_payload.get("annotations") or {}).get("topology_blast_radius_summary", "")
        ).strip()

        trace_lines, trace_used = _extract_trace_items(payload, limit=max(1, int(trace_limit)))
        lines = [
            f"Diagnosis Session: {scoped_session_id}",
            f"Status: {payload.get('status', 'unknown')}",
        ]
        if root_cause:
            lines.append(f"Root cause: {root_cause}")
        if root_layer:
            lines.append(f"Root cause layer: {root_layer}")
        if confidence is not None:
            lines.append(f"Confidence: {confidence}")
        if impact_summary:
            lines.append(f"Impact summary: {impact_summary}")
        if topology_summary:
            lines.append(f"Blast radius: {topology_summary}")
        if affected_services:
            services_text = ", ".join(str(item) for item in affected_services[:8] if str(item).strip())
            if services_text:
                lines.append(f"Affected services: {services_text}")
        if trace_lines:
            lines.append("Recent trace:")
            lines.extend(trace_lines)

        context_text = "\n".join(line for line in lines if line).strip()
        meta = {
            "context_applied": bool(context_text),
            "session_id": scoped_session_id,
            "trace_steps_used": trace_used,
            "context_tokens_estimate": _estimate_token_count(context_text),
        }
        if not context_text:
            return None, meta
        return {"session_id": scoped_session_id, "context_text": context_text}, meta

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

    def _topology_status_payload(services: Any) -> TopologyStatusResponse:
        discovery = getattr(services, "topology_discovery", None)
        if discovery is None:
            return TopologyStatusResponse()
        status = discovery.status()
        return TopologyStatusResponse.model_validate(status.as_json())

    def _active_alert_count(services: Any) -> int:
        snapshot = services.alert_store.snapshot()
        alerts = snapshot.get("alerts", [])
        if not isinstance(alerts, list):
            return 0
        count = 0
        for alert in alerts:
            if isinstance(alert, dict):
                status = str(alert.get("status", "")).strip().lower()
                if status == "firing":
                    count += 1
        return count

    def _enrich_alert_with_topology_summary(services: Any, alert: Alert) -> Alert:
        try:
            ontology = services.ontology
            labels = alert.labels
            candidates = [
                str(labels.get(key, "")).strip()
                for key in ("node", "instance", "service", "pod", "switch", "host", "job")
            ]
            candidates = [item for item in candidates if item]
            resolved_roots: list[str] = []
            affected_total = 0
            for candidate in candidates:
                root_id = candidate
                if ontology.get_entity(root_id) is None:
                    by_name = ontology.find_entities(filters={"name": root_id})
                    if not by_name:
                        continue
                    root_id = by_name[0].id
                if root_id in resolved_roots:
                    continue
                resolved_roots.append(root_id)
                radius = ontology.get_blast_radius(root_id)
                affected = radius.get("affected_entities", [])
                if isinstance(affected, list):
                    affected_total += len(affected)
            if not resolved_roots:
                return alert
            summary = f"roots={resolved_roots}, affected={affected_total}"
            annotations = dict(alert.annotations)
            annotations["topology_blast_radius_summary"] = summary
            return alert.model_copy(update={"annotations": annotations})
        except Exception:
            return alert

    @router.post("/diagnose")
    async def diagnose(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[DiagnosisSession]:
        services = _services(request)
        if services.diagnosis_runner is None:
            raise HTTPException(status_code=500, detail="diagnosis runner is not configured")
        prepared_alert = _enrich_alert_with_topology_summary(services, alert)
        session = await services.diagnosis_runner.adiagnose(prepared_alert)
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
        prepared_alert = _enrich_alert_with_topology_summary(services, alert)
        try:
            response = await services.incident_handler.handle(prepared_alert)
            return response.model_copy(update={"trace_id": _trace_id(request)})
        except ResourceLockedError as exc:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.RESOURCE_LOCKED, message=str(exc)),
                trace_id=_trace_id(request),
            )

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
        status_payload = _topology_status_payload(services)
        discovery = getattr(services, "topology_discovery", None)
        recent_events = discovery.recent_events(limit=20) if discovery is not None else []
        payload = TopologySnapshotResponse(
            nodes=_normalize_ontology_entities(services.ontology.list_entities()),
            edges=_normalize_ontology_edges(services.ontology.list_edges()),
            active_alerts=_active_alert_count(services),
            recent_events=recent_events,
            snapshot_id=status_payload.snapshot_id,
            last_synced_at=status_payload.last_synced_at,
            sync_state=status_payload.sync_state,
        )
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/topology/status")
    async def get_topology_status(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[TopologyStatusResponse]:
        _ = user
        services = _services(request)
        payload = _topology_status_payload(services)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.post("/topology/discover")
    async def trigger_topology_discover(
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[TopologyStatusResponse]:
        _ = user
        services = _services(request)
        discovery = getattr(services, "topology_discovery", None)
        if discovery is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.INTERNAL_ERROR, message="topology discovery service is unavailable"),
                trace_id=_trace_id(request),
            )
        status = await discovery.trigger_discovery(reason="manual")
        payload = TopologyStatusResponse.model_validate(status.as_json())
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
        session_context, chat_meta = _build_chat_session_context(
            services,
            session_id=message.session_id,
        )
        if services.chat_handler is None:
            reply = "chat handler is not configured"
        else:
            reply = await _invoke_chat_handler(
                services.chat_handler,
                content=message.content,
                user_id=user.user_id,
                session_id=message.session_id,
                session_context=session_context,
            )
        display = _extract_chat_display(reply)
        return SREResponse(
            success=True,
            data=ChatResponse(reply=reply, meta=chat_meta, display=display),
            trace_id=_trace_id(request),
        )

    @router.get("/chat/history")
    async def chat_history(
        request: Request,
        session_id: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[ChatHistoryMessage]]:
        services = _services(request)
        if services.chat_handler is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        history = await _invoke_chat_history(
            services.chat_handler,
            user_id=user.user_id,
            session_id=session_id,
        )
        if session_id:
            scoped = [item for item in history if (item.metadata or {}).get("session_id") == session_id]
            if scoped:
                history = scoped
            elif any((item.metadata or {}).get("session_id") is not None for item in history):
                history = []
        return SREResponse(success=True, data=history, trace_id=_trace_id(request))

    @router.get("/runtime/llm/status")
    async def get_llm_runtime_status(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[LLMRuntimeStatusResponse]:
        _ = user
        services = _services(request)
        payload = _llm_runtime_status_payload(services)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

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

    @router.get("/tools/channels/status")
    async def get_tool_channels_status(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[ToolChannelsStatusResponse]:
        _ = user
        services = _services(request)
        payload = _tool_channel_status_payload(services)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    return router
