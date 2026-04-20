"""REST routes for Agent C."""

from __future__ import annotations

import asyncio
import hashlib
import json
import inspect
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.alerts_filter import build_blocked_alert_name_set, is_blocked_alert
from sre_agent.auth.jwt import (
    CurrentUser,
    TokenDecodeError,
    decode_token,
    decode_refresh_token,
    encode_access_token,
    encode_refresh_token,
    extract_expiry_datetime,
    get_current_user,
)
from sre_agent.auth.rbac import require_role
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode, SREError, SREResponse
from sre_agent.models.diagnosis import (
    DiagnosisSession,
    RemediationAlertReview,
    RemediationAlertSnapshot,
    RemediationCheckSnapshot,
    RemediationEvidence,
    RemediationMetricReview,
    RemediationMetricSnapshot,
)
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.memory import ConfigBaseline, IncidentRecord, LearnedPattern
from sre_agent.models.remediation import LoopResult, RemediationPlan, RemediationResult
from sre_agent.remediation.approval import ApprovalInput
from sre_agent.remediation.engine import RollbackResult
from sre_agent.runtime.token_estimation import estimate_token_count
from sre_agent.concurrency.resource_lock import ResourceLockedError
from sre_agent.skills import SkillRegistry
from sre_agent.agent.nodes import log_stream_lifecycle_event

LOGGER = logging.getLogger(__name__)

LOGGER = logging.getLogger(__name__)
auth_security = HTTPBearer(auto_error=False)


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


class AuthTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    refresh_expires_at: datetime
    server_boot_id: str
    auth_error_kind: Literal["expired", "invalid_signature", "missing", "unknown"] = "unknown"


class AuthRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class AuthStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_boot_id: str
    access_token_expires_at: datetime | None = None
    skew_hint_seconds: int = 30
    auth_error_kind: Literal["expired", "invalid_signature", "missing", "unknown"] = "unknown"


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


class TopologyExplorerSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    region: str
    zone: str
    domain: str
    summary: str


class TopologyExplorerObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: Literal["rack", "node", "gpu", "switch", "port", "service", "pod", "cluster", "bmc"]
    status: Literal["healthy", "abnormal", "impacted", "maintenance"]
    layer: Literal["physical", "network", "compute", "service"]
    domain: str
    region: str
    zone: str
    cluster: str | None = None
    rack: str | None = None
    slot: str | None = None
    summary: str
    tags: list[str] = Field(default_factory=list)
    updatedAt: datetime
    metrics: dict[str, str | int | float | bool | None] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class TopologyExplorerRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    relationType: Literal["contains", "runs_on", "connects_to", "depends_on", "uplink_to", "aggregated"]
    status: Literal["healthy", "abnormal", "impacted", "maintenance"]
    isCritical: bool = False
    impactLevel: Literal["low", "medium", "high"] = "low"
    label: str | None = None
    isAggregated: bool | None = None


class TopologyExplorerPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    entryNodeId: str
    rootCauseNodeId: str
    affectedNodeIds: list[str] = Field(default_factory=list)
    edgeIds: list[str] = Field(default_factory=list)
    impactLevel: Literal["low", "medium", "high"] = "low"
    status: Literal["active", "inactive"] = "inactive"
    summary: str


class TopologyExplorerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: TopologyExplorerSite
    nodes: list[TopologyExplorerObject] = Field(default_factory=list)
    edges: list[TopologyExplorerRelation] = Field(default_factory=list)
    paths: list[TopologyExplorerPath] = Field(default_factory=list)
    lastUpdated: datetime
    sync_state: Literal["idle", "syncing", "ready", "degraded", "error"] = "idle"
    last_error: str | None = None


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
    model: str = "MiniMax-M2.7"
    base_url: str | None = None
    provider: str = "openai_compatible"
    fallback_models: list[str] = Field(default_factory=list)
    active_model: str | None = None
    retry_count: int = 0
    fallback_used: bool = False
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_attempt_at: str | None = None
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


class RevisePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = Field(min_length=1)
    base_plan_version: int | None = Field(default=None, ge=1)


class RevisePlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    plan_version: int = Field(ge=1)
    plan: dict[str, Any]
    session: dict[str, Any]


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

    def _server_boot_id(request: Request) -> str:
        return str(getattr(request.app.state, "server_boot_id", "") or "").strip()

    def _access_token_ttl_seconds() -> int:
        raw = str(os.getenv("SRE_DEMO_ACCESS_TOKEN_EXPIRE_SECONDS", "1800")).strip()
        try:
            return max(60, int(raw))
        except ValueError:
            return 1800

    def _refresh_token_ttl_seconds() -> int:
        raw = str(os.getenv("SRE_DEMO_REFRESH_TOKEN_EXPIRE_SECONDS", "86400")).strip()
        try:
            return max(300, int(raw))
        except ValueError:
            return 86400

    def _demo_auto_bootstrap_enabled() -> bool:
        raw = str(os.getenv("SRE_DEMO_AUTO_BOOTSTRAP_ENABLED", "false")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _demo_bootstrap_user() -> CurrentUser:
        username = str(os.getenv("SRE_DEMO_BOOTSTRAP_USERNAME", "container-ui")).strip() or "container-ui"
        role = str(os.getenv("SRE_DEMO_BOOTSTRAP_ROLE", "operator")).strip().lower() or "operator"
        if role not in {"viewer", "operator", "admin"}:
            role = "operator"
        return CurrentUser(user_id="demo-bootstrap", username=username, role=role)

    def _build_auth_token_response(request: Request, user: CurrentUser) -> AuthTokenResponse:
        settings = getattr(request.app.state, "jwt_settings", None)
        if settings is None:
            raise HTTPException(status_code=500, detail="jwt settings not configured")
        access_ttl = _access_token_ttl_seconds()
        refresh_ttl = _refresh_token_ttl_seconds()
        access_token = encode_access_token(user, settings, expire_seconds=access_ttl)
        refresh_token = encode_refresh_token(user, settings, expire_seconds=refresh_ttl)
        expires_at = extract_expiry_datetime(access_token, settings)
        refresh_expires_at = extract_expiry_datetime(refresh_token, settings)
        return AuthTokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            refresh_expires_at=refresh_expires_at,
            server_boot_id=_server_boot_id(request),
            auth_error_kind="unknown",
        )

    def _optional_current_user(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None,
    ) -> tuple[CurrentUser | None, Literal["expired", "invalid_signature", "missing", "unknown"]]:
        if credentials is None:
            return None, "missing"
        settings = getattr(request.app.state, "jwt_settings", None)
        if settings is None:
            return None, "unknown"
        try:
            return decode_token(credentials.credentials, settings), "unknown"
        except TokenDecodeError as exc:
            return None, exc.kind

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

    def _map_topology_entity_type(entity_type: str) -> Literal["rack", "node", "gpu", "switch", "port", "service", "pod", "cluster", "bmc"]:
        value = str(entity_type or "").strip().lower()
        if "gpu" in value:
            return "gpu"
        if "switch_port" in value:
            return "port"
        if "switch" in value or "network" in value:
            return "switch"
        if "cluster" in value:
            return "cluster"
        if "pod" in value:
            return "pod"
        if "service" in value or "inference" in value:
            return "service"
        if "rack" in value:
            return "rack"
        if "bmc" in value:
            return "bmc"
        return "node"

    def _map_topology_status(status: str | None) -> Literal["healthy", "abnormal", "impacted", "maintenance"]:
        value = str(status or "").strip().lower()
        if any(token in value for token in ("degraded", "warning", "error", "down")):
            return "abnormal"
        if "impact" in value:
            return "impacted"
        if "maint" in value:
            return "maintenance"
        return "healthy"

    def _map_topology_layer(
        entity_type: Literal["rack", "node", "gpu", "switch", "port", "service", "pod", "cluster", "bmc"],
    ) -> Literal["physical", "network", "compute", "service"]:
        if entity_type in {"switch", "port", "rack"}:
            return "network"
        if entity_type in {"gpu", "node"}:
            return "compute"
        if entity_type in {"cluster", "bmc"}:
            return "physical"
        return "service"

    def _map_topology_relation_type(
        relation: str,
    ) -> Literal["contains", "runs_on", "connects_to", "depends_on", "uplink_to", "aggregated"]:
        value = str(relation or "").strip().lower()
        if "contain" in value or "part_of" in value:
            return "contains"
        if "hosted" in value or "runs_on" in value or "run_on" in value:
            return "runs_on"
        if "serve" in value:
            return "depends_on"
        if "uplink" in value:
            return "uplink_to"
        if "connect" in value:
            return "connects_to"
        if "aggreg" in value:
            return "aggregated"
        return "depends_on"

    def _topology_explorer_payload(services: Any) -> TopologyExplorerResponse:
        status_payload = _topology_status_payload(services)
        ontology_nodes = _normalize_ontology_entities(services.ontology.list_entities())
        ontology_edges = _normalize_ontology_edges(services.ontology.list_edges())

        def _parse_datetime(value: Any) -> datetime | None:
            if isinstance(value, datetime):
                return value
            if not isinstance(value, str):
                return None
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None

        explorer_nodes: list[TopologyExplorerObject] = []
        for node in ontology_nodes:
            attributes = node.get("properties")
            if not isinstance(attributes, dict):
                attributes = {}
            attrs = attributes
            entity_type = _map_topology_entity_type(str(node.get("entity_type", "")))
            region = str(attrs.get("region") or "AIDC-CN")
            zone = str(attrs.get("zone") or "zone-a")
            domain = str(attrs.get("domain") or "aidc")
            namespace = str(attrs.get("namespace") or "").strip()
            cluster_id = str(attrs.get("cluster") or attrs.get("cluster_id") or "").strip()
            raw_name = str(node.get("name") or node.get("id") or "")
            node_kind = str(attrs.get("kind") or "").strip().lower()
            display_name = raw_name
            if entity_type == "service" and node_kind == "namespace_group":
                # Namespace group is the canonical "service group" object.
                # Show namespace only (not namespace/namespace) to avoid visual duplicates.
                display_name = namespace or raw_name
            elif entity_type in {"pod", "service"} and namespace:
                display_name = f"{namespace}/{raw_name}"

            pod_phase_raw = attrs.get("phase", node.get("status", "Unknown"))
            pod_phase = str(pod_phase_raw)
            pod_restart_raw = attrs.get("restart_count", attrs.get("restartCount"))
            pod_restart_count: int | None = None
            if pod_restart_raw is not None:
                try:
                    pod_restart_numeric = int(pod_restart_raw)
                except (TypeError, ValueError):
                    pod_restart_numeric = None
                if pod_restart_numeric is not None:
                    pod_restart_count = pod_restart_numeric
            metrics: dict[str, str | int | float | bool | None] | None = None
            if entity_type == "pod":
                metrics = {
                    "phase": pod_phase,
                    "restartCount": pod_restart_count,
                }

            updated_at = _parse_datetime(node.get("updated_at")) or datetime.now(UTC)
            explorer_nodes.append(
                TopologyExplorerObject(
                    id=str(node.get("id", "")),
                    name=display_name,
                    type=entity_type,
                    status=_map_topology_status(node.get("status")),
                    layer=_map_topology_layer(entity_type),
                    domain=domain,
                    region=region,
                    zone=zone,
                    cluster=cluster_id or None,
                    rack=str(attrs.get("rack") or "") or None,
                    slot=str(attrs.get("slot") or "") or None,
                    summary=" ".join(
                        item
                        for item in [
                            display_name,
                            f"({node.get('entity_type', '')})",
                            f"namespace={namespace}" if namespace else "",
                            f"cluster={cluster_id}" if cluster_id else "",
                        ]
                        if item
                    ),
                    tags=[item for item in [namespace, str(attrs.get("source") or "").strip(), cluster_id] if item],
                    updatedAt=updated_at,
                    metrics=metrics,
                    attributes=attrs,
                )
            )

        explorer_edges = [
            TopologyExplorerRelation(
                id=f"edge-{index}-{edge.get('source_id', '')}-{edge.get('target_id', '')}",
                source=str(edge.get("source_id", "")),
                target=str(edge.get("target_id", "")),
                relationType=_map_topology_relation_type(str(edge.get("relation", ""))),
                status="healthy",
                isCritical=False,
                impactLevel="low",
                label=str(edge.get("relation", "")) or None,
                isAggregated=False,
            )
            for index, edge in enumerate(ontology_edges)
        ]

        last_updated = status_payload.last_synced_at or datetime.now(UTC)
        return TopologyExplorerResponse(
            site=TopologyExplorerSite(
                id="aidc-site",
                name="AIDC Site",
                region="AIDC-CN",
                zone="zone-a",
                domain="aidc",
                summary="Topology explorer snapshot mapped from /api/topology",
            ),
            nodes=explorer_nodes,
            edges=explorer_edges,
            paths=[],
            lastUpdated=last_updated,
            sync_state=status_payload.sync_state,
            last_error=status_payload.last_error,
        )

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

    def _tool_channel_status_map(services: Any) -> dict[str, ToolChannelStatusItem]:
        payload = _tool_channel_status_payload(services)
        return {
            item.name.strip().lower(): item
            for item in payload.channels
            if item.name.strip()
        }

    def _required_channels_for_real_execution(plan: RemediationPlan | None) -> list[str]:
        required = {"prometheus"}
        if plan is None:
            return sorted(required)
        for step in plan.steps:
            tool_name = str(step.tool or "").strip().lower()
            if tool_name.startswith("k8s."):
                required.add("k8s")
            if tool_name in {"kill_process", "network.clear_tc_qdisc"} or tool_name.startswith("ssh."):
                required.add("ssh")
        return sorted(required)

    def _validate_plan_param_values(plan: RemediationPlan | None) -> list[str]:
        if plan is None:
            return []
        errors: list[str] = []
        _placeholders = {"unknown", "n/a", "none", "-", "--", "null", ""}
        for step in plan.steps:
            for key, value in step.params.items():
                if isinstance(value, str) and value.strip().lower() in _placeholders:
                    errors.append(f"step {step.step_id}: param '{key}' has placeholder value '{value}'")
        return errors

    def _validate_real_execution_readiness(
        services: Any,
        *,
        plan: RemediationPlan | None,
    ) -> dict[str, Any] | None:
        execution_mode = str(getattr(services.remediation_engine, "execution_mode", "real") or "real").strip().lower()
        if execution_mode != "real":
            return None

        status_map = _tool_channel_status_map(services)
        required_channels = _required_channels_for_real_execution(plan)
        unready_channels: list[dict[str, Any]] = []
        for channel_name in required_channels:
            status = status_map.get(channel_name)
            health = status.health if status is not None else "unavailable"
            if health == "ready":
                continue
            unready_channels.append(
                {
                    "name": channel_name,
                    "health": health,
                    "mode": status.mode if status is not None else "unknown",
                    "last_error": status.last_error if status is not None else "channel status missing",
                }
            )

        if not unready_channels:
            return None

        summary = ", ".join(f"{item['name']}={item['health']}" for item in unready_channels)
        return {
            "execution_mode": "real",
            "required_channels": required_channels,
            "unready_channels": unready_channels,
            "message": f"real remediation execution blocked: required channels not ready ({summary})",
        }

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
            model=str(payload.get("model", "MiniMax-M2.7")),
            base_url=str(payload["base_url"]) if payload.get("base_url") else None,
            provider=str(payload.get("provider", "openai_compatible")),
            fallback_models=[
                str(item).strip()
                for item in (payload.get("fallback_models") or [])
                if str(item).strip()
            ],
            active_model=str(payload["active_model"]) if payload.get("active_model") else None,
            retry_count=int(payload.get("retry_count", 0) or 0),
            fallback_used=bool(payload.get("fallback_used", False)),
            last_error_code=str(payload["last_error_code"]) if payload.get("last_error_code") else None,
            last_error_message=str(payload["last_error_message"]) if payload.get("last_error_message") else None,
            last_attempt_at=str(payload["last_attempt_at"]) if payload.get("last_attempt_at") else None,
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
            "context_tokens_estimate": estimate_token_count(context_text),
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

    def _supports_trace_callback(method: Any) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        if "trace_callback" in signature.parameters:
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())

    def _supports_extra_alerts(method: Any) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        if "extra_alerts" in signature.parameters:
            return True
        return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())

    def _lookup_extra_alerts(alert_store: Any, fingerprints: list[str]) -> list[Alert]:
        """Look up extra alerts from the alert store by fingerprint."""
        if not fingerprints:
            return []
        snapshot = alert_store.snapshot()
        raw_alerts = snapshot.get("alerts", [])
        fingerprint_set = set(fingerprints)
        results: list[Alert] = []
        for item in raw_alerts:
            if not isinstance(item, dict):
                continue
            fp = str(item.get("fingerprint", "")).strip()
            if fp in fingerprint_set:
                try:
                    results.append(Alert.model_validate(item))
                except Exception:
                    continue
        return results

    async def _run_diagnose_with_optional_trace_callback(
        services: Any,
        *,
        alert: Alert,
        trace_callback: Any | None,
        extra_alerts: list[Alert] | None = None,
    ) -> DiagnosisSession:
        method = services.diagnosis_runner.adiagnose
        kwargs: dict[str, Any] = {"extra_alerts": extra_alerts} if extra_alerts else {}
        if trace_callback is not None and _supports_trace_callback(method):
            return await method(alert, trace_callback=trace_callback, **kwargs)
        return await method(alert, **kwargs)

    async def _replay_trace_and_diagnosis_events(
        services: Any,
        *,
        session: DiagnosisSession,
    ) -> None:
        if session.trace is not None:
            for item in session.trace.steps:
                payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else {}
                if hasattr(item, "tool"):
                    await services.trace_publisher.publish(
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
                await services.trace_publisher.publish(
                    {
                        "type": event_type,
                        "session_id": session.session_id,
                        "data": payload,
                    }
                )
        if session.diagnosis_result is not None:
            await services.trace_publisher.publish(
                {
                    "type": EventType.DIAGNOSIS_RESULT.value,
                    "session_id": session.session_id,
                    "data": session.diagnosis_result.model_dump(mode="json"),
                }
            )

    def _track_background_task(app: Any, task: asyncio.Task[Any]) -> None:
        holder = getattr(app.state, "background_tasks", None)
        if not isinstance(holder, set):
            holder = set()
            app.state.background_tasks = holder
        holder.add(task)
        task.add_done_callback(holder.discard)

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
        stage_to_event_type: dict[str, EventType] = {
            "execution_mocked": EventType.EXECUTION_MOCKED,
        }
        mapped_event_type = stage_to_event_type.get(stage)
        if mapped_event_type is not None:
            await _publish_session_event(
                services,
                event_type=mapped_event_type,
                session_id=session_id,
                data=payload,
            )

    async def _publish_session_event(
        services: Any,
        *,
        event_type: EventType,
        session_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = data or {}
        await services.trace_publisher.publish(
            {
                "type": event_type.value,
                "session_id": session_id,
                "data": payload,
            }
        )

    def _compare_scalar(actual: Any, operator: str, expected: Any) -> bool:
        try:
            actual_num = float(actual)
            expected_num = float(expected)
        except (TypeError, ValueError):
            actual_num = actual
            expected_num = expected
        if operator == "<":
            return actual_num < expected_num
        if operator == "<=":
            return actual_num <= expected_num
        if operator == ">":
            return actual_num > expected_num
        if operator == ">=":
            return actual_num >= expected_num
        if operator == "==":
            return actual_num == expected_num
        if operator == "!=":
            return actual_num != expected_num
        return False

    def _update_session_evidence(
        services: Any,
        *,
        session: DiagnosisSession,
        evidence: RemediationEvidence,
    ) -> DiagnosisSession:
        updated = session.model_copy(update={"remediation_evidence": evidence})
        services.session_store.put(updated)
        return updated

    def _normalize_metric_key(raw_key: str, *, fallback_index: int) -> str:
        value = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw_key or "").strip()).strip("_").lower()
        return value or f"metric_{fallback_index}"

    def _default_llm_metric_specs(session: DiagnosisSession) -> list[dict[str, Any]]:
        labels = session.alert.labels
        service = str(labels.get("service") or labels.get("app") or "vllm").strip() or "vllm"
        namespace = str(labels.get("namespace") or "service").strip() or "service"
        specs = [
            {
                "metric_key": "llm_latency_p95",
                "query": f'vllm_request_latency_p95{{service="{service}",namespace="{namespace}"}}',
            },
            {
                "metric_key": "llm_ttft_p99",
                "query": (
                    'histogram_quantile(0.99, sum by (le) '
                    f'(rate(vllm:time_to_first_token_seconds_bucket{{service="{service}",namespace="{namespace}"}}[5m])))'
                ),
            },
            {
                "metric_key": "llm_inter_token_latency_p95",
                "query": f'vllm_inter_token_latency_p95{{service="{service}",namespace="{namespace}"}}',
            },
        ]
        return specs

    def _build_metric_specs(plan: RemediationPlan | None, session: DiagnosisSession) -> list[dict[str, Any]]:
        _ = session
        specs: list[dict[str, Any]] = []
        if plan is not None:
            for index, step in enumerate(plan.steps, start=1):
                verification = step.verification
                if verification.method != "promql" or not str(verification.query or "").strip():
                    continue
                metric_key = _normalize_metric_key(
                    step.description or verification.query or f"metric_{index}",
                    fallback_index=index,
                )
                specs.append(
                    {
                        "metric_key": metric_key,
                        "query": str(verification.query or "").strip(),
                        "condition": verification.condition.model_dump(mode="json") if verification.condition is not None else None,
                    }
                )
        # Do NOT fallback to default LLM (TTFT/latency) metrics.
        # If plan doesn't provide promql verification, treat metrics validation as skipped.
        return specs

    async def _capture_alert_snapshot(session: DiagnosisSession, services: Any) -> RemediationAlertSnapshot:
        snapshot = services.alert_store.snapshot()
        alerts = snapshot.get("alerts", [])
        if not isinstance(alerts, list):
            return RemediationAlertSnapshot(
                fingerprint=session.alert.fingerprint,
                alert_name=session.alert.alert_name,
                status="unknown",
                is_firing=False,
                available=False,
                error="alert snapshot unavailable",
            )

        for alert_item in alerts:
            if not isinstance(alert_item, dict):
                continue
            if str(alert_item.get("fingerprint") or "").strip() != session.alert.fingerprint:
                continue
            status = str(alert_item.get("status") or "unknown").strip().lower() or "unknown"
            return RemediationAlertSnapshot(
                fingerprint=session.alert.fingerprint,
                alert_name=str(alert_item.get("alert_name") or session.alert.alert_name).strip() or session.alert.alert_name,
                status=status,
                is_firing=status == "firing",
            )

        return RemediationAlertSnapshot(
            fingerprint=session.alert.fingerprint,
            alert_name=session.alert.alert_name,
            status="resolved",
            is_firing=False,
        )

    async def _capture_metric_snapshots(
        *,
        services: Any,
        session: DiagnosisSession,
        plan: RemediationPlan | None,
    ) -> list[RemediationMetricSnapshot]:
        specs = _build_metric_specs(plan, session)
        prometheus = getattr(services.remediation_engine, "prometheus", None)
        execution_mode = str(getattr(services.remediation_engine, "execution_mode", "real") or "real").strip().lower()
        snapshots: list[RemediationMetricSnapshot] = []
        for index, spec in enumerate(specs, start=1):
            metric_key = str(spec.get("metric_key") or f"metric_{index}")
            query = str(spec.get("query") or "").strip()
            condition = spec.get("condition")
            if not query:
                snapshots.append(
                    RemediationMetricSnapshot(
                        metric_key=metric_key,
                        query="<missing-query>",
                        available=False,
                        error="query missing",
                        condition=condition if isinstance(condition, dict) else None,
                    )
                )
                continue
            if prometheus is None or not hasattr(prometheus, "query_instant"):
                if execution_mode == "mock":
                    snapshots.append(
                        RemediationMetricSnapshot(
                            metric_key=metric_key,
                            query=query,
                            value=0,
                            condition=condition if isinstance(condition, dict) else None,
                        )
                    )
                    continue
                snapshots.append(
                    RemediationMetricSnapshot(
                        metric_key=metric_key,
                        query=query,
                        available=False,
                        error="prometheus unavailable",
                        condition=condition if isinstance(condition, dict) else None,
                    )
                )
                continue
            try:
                value = await prometheus.query_instant(query)
                snapshots.append(
                    RemediationMetricSnapshot(
                        metric_key=metric_key,
                        query=query,
                        value=value,
                        available=value is not None,
                        condition=condition if isinstance(condition, dict) else None,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                snapshots.append(
                    RemediationMetricSnapshot(
                        metric_key=metric_key,
                        query=query,
                        available=False,
                        error=str(exc),
                        condition=condition if isinstance(condition, dict) else None,
                    )
                )
        return snapshots

    async def _capture_check_snapshot(
        services: Any,
        *,
        session: DiagnosisSession,
        plan: RemediationPlan | None,
    ) -> RemediationCheckSnapshot:
        alert_snapshot = await _capture_alert_snapshot(session, services)
        metric_snapshots = await _capture_metric_snapshots(services=services, session=session, plan=plan)
        latest_collected_at = max(
            [alert_snapshot.collected_at, *[item.collected_at for item in metric_snapshots]],
            default=datetime.now(UTC),
        )
        return RemediationCheckSnapshot(alert=alert_snapshot, metrics=metric_snapshots, collected_at=latest_collected_at)

    def _build_metric_reviews(
        pre_check: RemediationCheckSnapshot | None,
        post_check: RemediationCheckSnapshot | None,
    ) -> tuple[list[RemediationMetricReview], bool]:
        before_metrics = {
            item.metric_key: item for item in (pre_check.metrics if pre_check is not None else [])
        }
        after_metrics = {
            item.metric_key: item for item in (post_check.metrics if post_check is not None else [])
        }
        metric_keys = list(dict.fromkeys([*before_metrics.keys(), *after_metrics.keys()]))
        reviews: list[RemediationMetricReview] = []
        all_improved = True
        for metric_key in metric_keys:
            before = before_metrics.get(metric_key)
            after = after_metrics.get(metric_key)
            available = bool(before and before.available) and bool(after and after.available)
            condition = (after.condition if after is not None else None) or (before.condition if before is not None else None)
            improved = False
            error: str | None = None
            if not available:
                all_improved = False
                error = (after.error if after is not None else None) or (before.error if before is not None else None)
            elif isinstance(condition, dict):
                operator = str(condition.get("operator") or "").strip()
                expected = condition.get("value")
                improved = bool(operator) and _compare_scalar(after.value, operator, expected)
                all_improved = all_improved and improved
            else:
                after_val = after.value if after is not None else None
                before_val = before.value if before is not None else None
                if after_val is None and isinstance(before_val, (int, float)) and float(before_val) > 0:
                    improved = True  # 基线异常高、修复后无数据 → 视为改善
                elif after_val is not None and before_val is not None:
                    try:
                        improved = float(after_val) <= float(before_val)
                    except (TypeError, ValueError):
                        improved = False
                all_improved = all_improved and improved
            reviews.append(
                RemediationMetricReview(
                    metric_key=metric_key,
                    query=(after.query if after is not None else before.query if before is not None else metric_key),
                    before_value=before.value if before is not None else None,
                    after_value=after.value if after is not None else None,
                    condition=condition,
                    improved=improved,
                    available=available,
                    error=error,
                )
            )
        return reviews, all_improved

    _ALERT_STATUS_ONLY_WHEN_METRICS_UNAVAILABLE = {"aiservicettftp99high"}

    def _normalize_alert_name_for_policy(value: str | None) -> str:
        return str(value or "").strip().lower()

    def _should_use_alert_status_only_policy(alert_name: str | None) -> bool:
        return _normalize_alert_name_for_policy(alert_name) in _ALERT_STATUS_ONLY_WHEN_METRICS_UNAVAILABLE

    async def _observe_post_remediation(
        services: Any,
        *,
        session: DiagnosisSession,
        pre_check: RemediationCheckSnapshot | None,
        observation_seconds: int,
        observation_poll_seconds: int,
    ) -> tuple[bool, dict[str, Any], RemediationEvidence]:
        total_observation_seconds = max(0, int(observation_seconds))
        poll_interval_seconds = max(1, int(observation_poll_seconds))
        await _publish_remediation_progress(
            services,
            session_id=session.session_id,
            stage="observation_started",
            details={
                "seconds": total_observation_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            },
        )
        plan = services.remediation_engine.get_plan(session.session_id)
        observation_started_at = time.monotonic()
        poll_count = 0
        post_check = await _capture_check_snapshot(services, session=session, plan=plan)
        while True:
            poll_count += 1
            post_alert = post_check.alert
            if post_alert is not None and post_alert.available and not post_alert.is_firing:
                break
            elapsed_seconds = time.monotonic() - observation_started_at
            remaining_seconds = total_observation_seconds - elapsed_seconds
            if remaining_seconds <= 0:
                break
            await asyncio.sleep(min(float(poll_interval_seconds), max(0.0, remaining_seconds)))
            post_check = await _capture_check_snapshot(services, session=session, plan=plan)
        pre_alert = pre_check.alert if pre_check is not None else None
        post_alert = post_check.alert
        alert_cleared = bool(post_alert is not None and post_alert.available and not post_alert.is_firing)
        alert_review = None
        if pre_alert is not None and post_alert is not None:
            alert_review = RemediationAlertReview(
                fingerprint=post_alert.fingerprint,
                alert_name=post_alert.alert_name,
                before_status=pre_alert.status,
                after_status=post_alert.status,
                cleared=alert_cleared,
            )

        metric_reviews, metrics_improved = _build_metric_reviews(pre_check, post_check)
        post_metrics_unavailable_keys = [item.metric_key for item in post_check.metrics if not item.available]
        use_alert_status_only_policy = _should_use_alert_status_only_policy(post_alert.alert_name) and bool(
            post_metrics_unavailable_keys
        )
        policy_applied = (
            "alert_status_only_when_post_metrics_unavailable"
            if use_alert_status_only_policy
            else "default_alert_and_metrics"
        )
        observed_ok = alert_cleared if use_alert_status_only_policy else (alert_cleared and metrics_improved)
        escalation_reasons: list[str] = []
        if not observed_ok:
            if not alert_cleared:
                escalation_reasons.append("alert_not_cleared")
            if post_metrics_unavailable_keys:
                escalation_reasons.append("metrics_unavailable")
            elif not metrics_improved:
                escalation_reasons.append("metrics_not_improved")
        evidence = RemediationEvidence(
            pre_check=pre_check,
            post_check=post_check,
            alert_review=alert_review,
            metric_reviews=metric_reviews,
            alert_cleared=alert_cleared,
            metrics_improved=metrics_improved,
        )
        details = {
            "alert_cleared": alert_cleared,
            "metrics_improved": metrics_improved,
            "observation_seconds": total_observation_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "poll_count": poll_count,
            "metrics_checked": len(metric_reviews),
            "baseline_alert": pre_alert.model_dump(mode="json") if pre_alert is not None else None,
            "baseline_metrics": [item.model_dump(mode="json") for item in (pre_check.metrics if pre_check is not None else [])],
            "post_alert": post_alert.model_dump(mode="json") if post_alert is not None else None,
            "post_metrics": [item.model_dump(mode="json") for item in post_check.metrics],
            "pre_check": pre_check.model_dump(mode="json") if pre_check is not None else None,
            "post_check": post_check.model_dump(mode="json"),
            "alert_review": alert_review.model_dump(mode="json") if alert_review is not None else None,
            "metric_reviews": [item.model_dump(mode="json") for item in metric_reviews],
            "policy_applied": policy_applied,
            "post_metrics_unavailable_keys": post_metrics_unavailable_keys,
            "escalation_reasons": escalation_reasons,
            "collected_at": evidence.collected_at.isoformat(),
        }
        updated_session = _update_session_evidence(services, session=session, evidence=evidence)
        await _publish_remediation_progress(
            services,
            session_id=updated_session.session_id,
            stage="observation_result",
            details=details,
        )
        return observed_ok, details, evidence

    def _extract_step_results(result: RemediationResult) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for item in result.verification_results:
            if isinstance(item, dict):
                payload.append(dict(item))
        return payload

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

        source = str(
            payload.get("source")
            or payload.get("dataset_name")
            or metadata.get("source")
            or metadata.get("dataset_name")
            or ""
        )
        category = str(
            payload.get("category")
            or payload.get("dataset_id")
            or metadata.get("category")
            or metadata.get("dataset_id")
            or "general"
        )
        title = str(
            payload.get("title")
            or payload.get("name")
            or metadata.get("title")
            or metadata.get("name")
            or source
            or f"Document {index + 1}"
        )
        excerpt = str(
            payload.get("excerpt")
            or payload.get("content")
            or payload.get("text")
            or metadata.get("excerpt")
            or payload.get("indexing_status")
            or ""
        )

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

        doc_id = str(payload.get("id") or payload.get("document_id") or metadata.get("id") or f"{category}-{index + 1}")
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

    def _normalize_knowledge_dataset(item: Any, *, default_dataset_id: str = "") -> dict[str, Any]:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) if isinstance(item, dict) else {}
        dataset_id = str(payload.get("id") or default_dataset_id)
        name = str(payload.get("name") or payload.get("title") or dataset_id or "dataset")
        description = str(payload.get("description") or payload.get("intro") or "")
        document_count = payload.get("document_count") or payload.get("documents_count") or payload.get("files_count") or 0
        word_count = payload.get("word_count") or payload.get("tokens") or 0
        return {
            "id": dataset_id,
            "name": name,
            "description": description,
            "document_count": int(document_count) if str(document_count).strip().isdigit() else 0,
            "word_count": int(word_count) if str(word_count).strip().isdigit() else 0,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "status": payload.get("indexing_status") or payload.get("status") or "ready",
        }

    def _normalize_knowledge_segment(
        item: Any,
        *,
        index: int,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        payload = item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) if isinstance(item, dict) else {}
        segment = payload.get("segment")
        if isinstance(segment, dict):
            merged = dict(segment)
            merged.update({k: v for k, v in payload.items() if k not in {"segment"}})
            payload = merged
        segment_id = str(payload.get("id") or payload.get("segment_id") or f"segment-{index + 1}")
        content = str(payload.get("content") or payload.get("text") or payload.get("answer") or "")
        score = payload.get("score")
        try:
            score_value = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_value = None
        result = {
            "id": segment_id,
            "document_id": str(payload.get("document_id") or document_id or ""),
            "content": content,
            "status": str(payload.get("status") or payload.get("enabled") or "enabled"),
            "source": str(payload.get("source") or payload.get("dataset_name") or ""),
            "position": payload.get("position"),
            "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        }
        if score_value is not None:
            result["score"] = score_value
        return result

    def _normalize_knowledge_scope(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"private", "personal", "user"}:
            return "private"
        return "shared"

    def _normalize_knowledge_status(value: Any) -> str:
        if isinstance(value, bool):
            return "enabled" if value else "disabled"
        normalized = str(value or "").strip().lower()
        if normalized in {"disabled", "disable", "inactive", "off", "false", "0"}:
            return "disabled"
        return "enabled"

    def _normalize_knowledge_index_status(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"ready", "enabled", "active", "indexed", "available"}:
            return "ready"
        if normalized in {"indexing", "building", "processing", "running"}:
            return "indexing"
        if normalized in {"failed", "error", "unavailable"}:
            return "failed"
        return "pending"

    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            if isinstance(value, bool):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_timestamp(value: Any, *, fallback: str | None = None) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value or "").strip()
        if text:
            return text
        return fallback or datetime.now(UTC).isoformat()

    def _slugify_code(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
        return normalized or "knowledge_base"

    def _as_payload_dict(item: Any) -> dict[str, Any]:
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _normalize_knowledge_base_summary(item: Any, *, default_dataset_id: str = "") -> dict[str, Any]:
        payload = _as_payload_dict(item)
        dataset = _normalize_knowledge_dataset(payload, default_dataset_id=default_dataset_id)
        dataset_id = str(dataset.get("id") or default_dataset_id or "default")
        name = str(dataset.get("name") or payload.get("title") or dataset_id or "Knowledge Base")
        code = str(payload.get("code") or payload.get("dataset_code") or _slugify_code(name or dataset_id))
        document_count = _safe_int(payload.get("document_count", dataset.get("document_count")), default=0)
        storage_bytes = _safe_int(
            payload.get("storage_bytes")
            or payload.get("size_bytes")
            or payload.get("bytes")
            or payload.get("word_count")
            or dataset.get("word_count"),
            default=0,
        )
        summary = {
            "id": dataset_id,
            "name": name,
            "code": code,
            "scope": _normalize_knowledge_scope(payload.get("scope") or payload.get("visibility") or payload.get("access")),
            "document_count": max(document_count, 0),
            "storage_bytes": max(storage_bytes, 0),
            "index_status": _normalize_knowledge_index_status(
                payload.get("index_status") or payload.get("indexing_status") or dataset.get("status")
            ),
            "status": _normalize_knowledge_status(payload.get("status") if "status" in payload else payload.get("enabled")),
            "updated_at": _normalize_timestamp(payload.get("updated_at") or dataset.get("updated_at")),
            "description": str(payload.get("description") or dataset.get("description") or ""),
        }
        return summary

    def _normalize_preview_sections(raw_sections: Any, *, fallback_text: str) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        if isinstance(raw_sections, list):
            for idx, section in enumerate(raw_sections):
                section_payload = _as_payload_dict(section)
                if not section_payload:
                    continue
                heading = str(section_payload.get("heading") or section_payload.get("title") or f"Section {idx + 1}")
                body = str(section_payload.get("body") or section_payload.get("content") or "")
                section_id = str(section_payload.get("id") or f"section-{idx + 1}")
                if not heading.strip() and not body.strip():
                    continue
                sections.append({"id": section_id, "heading": heading, "body": body})
        if sections:
            return sections
        return [{"id": "section-1", "heading": "摘要", "body": fallback_text or "暂无正文片段"}]

    def _infer_knowledge_source_type(payload: dict[str, Any], *, source_label: str, file_name: str) -> str:
        explicit = str(payload.get("source_type") or "").strip().lower()
        if explicit in {"file", "manual", "link"}:
            return explicit
        source = str(payload.get("source_uri") or payload.get("source") or source_label).strip().lower()
        if source.startswith("http://") or source.startswith("https://"):
            return "link"
        if source.startswith("manual://"):
            return "manual"
        if source.startswith(("platform://", "repo://", "archive://", "file://")):
            return "file"
        if "." in file_name:
            return "file"
        return "manual"

    def _normalize_knowledge_base_document(item: Any, *, index: int) -> dict[str, Any]:
        payload = _as_payload_dict(item)
        normalized = _normalize_knowledge_document(payload, index=index)
        preview_payload = _as_payload_dict(payload.get("preview"))
        title = str(normalized.get("title") or f"Document {index + 1}")
        excerpt = str(normalized.get("excerpt") or "")
        source_label = str(
            payload.get("source_label")
            or preview_payload.get("source_label")
            or normalized.get("source")
            or "unknown"
        )
        source_uri_raw = str(payload.get("source_uri") or preview_payload.get("source_uri") or "")
        source_uri = source_uri_raw.strip() or (source_label if source_label.startswith(("http://", "https://")) else None)
        updated_at = _normalize_timestamp(
            payload.get("updated_at")
            or preview_payload.get("updated_at")
            or payload.get("created_at"),
        )

        raw_tags = preview_payload.get("tags", payload.get("tags", normalized.get("tags", [])))
        if isinstance(raw_tags, list):
            tags = [str(tag) for tag in raw_tags if str(tag).strip()]
        elif isinstance(raw_tags, str):
            tags = [segment.strip() for segment in raw_tags.split(",") if segment.strip()]
        else:
            tags = []

        file_name = str(payload.get("file_name") or payload.get("filename") or payload.get("name") or f"{_slugify_code(title)}.md")
        size_raw = payload.get("size_bytes")
        size_bytes = _safe_int(size_raw, default=0) if size_raw is not None else None
        index_status = _normalize_knowledge_index_status(payload.get("index_status") or payload.get("indexing_status"))
        status = _normalize_knowledge_status(payload.get("status") if "status" in payload else payload.get("enabled"))
        preview_summary = str(payload.get("preview_summary") or preview_payload.get("description") or excerpt or "暂无摘要")
        warning = str(preview_payload.get("warning") or "").strip() or None
        if warning is None and index_status != "ready":
            warning = "该文档索引未就绪，预览可能不完整。"

        preview = {
            "title": str(preview_payload.get("title") or title),
            "description": str(preview_payload.get("description") or excerpt or "暂无摘要"),
            "source_label": source_label,
            "source_uri": source_uri,
            "tags": tags,
            "updated_at": updated_at,
            "sections": _normalize_preview_sections(preview_payload.get("sections"), fallback_text=excerpt),
            "warning": warning,
        }

        return {
            "id": str(normalized.get("id") or payload.get("document_id") or f"doc-{index + 1}"),
            "title": title,
            "source_type": _infer_knowledge_source_type(payload, source_label=source_label, file_name=file_name),
            "source_label": source_label,
            "file_name": file_name,
            "size_bytes": size_bytes,
            "index_status": index_status,
            "status": status,
            "updated_at": updated_at,
            "preview_summary": preview_summary,
            "tags": tags,
            "preview": preview,
        }

    def _normalize_knowledge_base_detail(dataset_item: Any, documents: list[Any], *, default_dataset_id: str = "") -> dict[str, Any]:
        dataset_payload = _as_payload_dict(dataset_item)
        summary = _normalize_knowledge_base_summary(dataset_payload, default_dataset_id=default_dataset_id)
        mapped_documents = [_normalize_knowledge_base_document(item, index=idx) for idx, item in enumerate(documents)]
        indexed_at_raw = dataset_payload.get("indexed_at")
        indexed_at = (
            _normalize_timestamp(indexed_at_raw)
            if indexed_at_raw is not None
            else (summary["updated_at"] if summary["index_status"] == "ready" else None)
        )
        document_count = max(summary["document_count"], len(mapped_documents))
        return {
            **summary,
            "document_count": document_count,
            "knowledge_base_id": str(dataset_payload.get("knowledge_base_id") or summary["id"]),
            "created_at": _normalize_timestamp(dataset_payload.get("created_at"), fallback=summary["updated_at"]),
            "indexed_at": indexed_at,
            "documents": mapped_documents,
        }

    def _knowledge_default_dataset_id(request: Request, knowledge: Any | None = None) -> str:
        cfg = getattr(request.app.state, "config", None)
        kb_cfg = getattr(cfg, "knowledge_base", None) if cfg is not None else None
        dataset_id = str(getattr(kb_cfg, "dataset_id", "") or "").strip() if kb_cfg is not None else ""
        if dataset_id:
            return dataset_id
        runbook_dataset_id = str(getattr(kb_cfg, "runbook_dataset_id", "") or "").strip() if kb_cfg is not None else ""
        if runbook_dataset_id:
            return runbook_dataset_id
        if knowledge is not None:
            for attribute in ("default_dataset_id", "runbook_dataset_id", "dataset_id"):
                candidate = str(getattr(knowledge, attribute, "") or "").strip()
                if candidate:
                    return candidate
        return "default"

    def _knowledge_resolved_dataset_id(
        request: Request,
        *,
        knowledge: Any | None = None,
        dataset_id: str | None = None,
    ) -> str:
        explicit_dataset_id = str(dataset_id or "").strip()
        if explicit_dataset_id:
            return explicit_dataset_id
        return _knowledge_default_dataset_id(request, knowledge)

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

    def _blocked_alert_names_from_request(request: Request) -> set[str]:
        cfg = getattr(request.app.state, "config", None)
        global_cfg = getattr(cfg, "global_", None) if cfg is not None else None
        raw = getattr(global_cfg, "blocked_alert_names", None) if global_cfg is not None else None
        return build_blocked_alert_name_set(raw)

    def _blocked_alert_response(request: Request, alert_name: str) -> SREResponse[Any]:
        message = f"alert '{alert_name}' is temporarily filtered and cannot be processed"
        return SREResponse(
            success=False,
            error=SREError(code=ErrorCode.VALIDATION_ERROR, message=message),
            trace_id=_trace_id(request),
        )

    def _filter_alert_snapshot(alerts: Any, clusters: Any, *, blocked_alert_names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        filtered_alerts: list[dict[str, Any]] = []
        if isinstance(alerts, list):
            for item in alerts:
                if not isinstance(item, dict):
                    continue
                if is_blocked_alert(item, blocked_names=blocked_alert_names):
                    continue
                filtered_alerts.append(item)

        allowed_fingerprints = {
            str(item.get("fingerprint", "")).strip()
            for item in filtered_alerts
            if isinstance(item.get("fingerprint"), str) and str(item.get("fingerprint", "")).strip()
        }
        filtered_clusters: list[dict[str, Any]] = []
        if isinstance(clusters, list):
            for cluster in clusters:
                if not isinstance(cluster, dict):
                    continue
                refs = cluster.get("alerts", [])
                if not isinstance(refs, list):
                    refs = []
                kept = [ref for ref in refs if isinstance(ref, str) and ref in allowed_fingerprints]
                if not kept:
                    continue
                copied = dict(cluster)
                copied["alerts"] = kept
                filtered_clusters.append(copied)
        return filtered_alerts, filtered_clusters

    def _resolve_alert_entity_ids(alert_data: dict[str, Any], ontology: Any) -> list[str]:
        """Extract entity candidates from alert labels and resolve against ontology graph."""
        raw_labels = alert_data.get("labels")
        labels = raw_labels if isinstance(raw_labels, dict) else {}
        candidates = [
            str(labels.get(key, "")).strip()
            for key in ("node", "instance", "service", "pod", "switch", "host", "job", "kubernetes_node")
        ]
        candidates = [item for item in candidates if item]
        resolved: list[str] = []
        for candidate in candidates:
            if ontology.get_entity(candidate) is not None:
                resolved.append(candidate)
                continue
            by_name = ontology.find_entities(filters={"name": candidate})
            if by_name:
                resolved.append(by_name[0].id)
        return resolved

    def _build_topology_aware_clusters(
        alerts: list[dict[str, Any]],
        ontology: Any,
    ) -> list[dict[str, Any]]:
        """Build alert clusters based on topology entity relationships."""
        if not alerts or ontology is None:
            return []
        fingerprint_to_entities: dict[str, list[str]] = {}
        alert_by_fingerprint: dict[str, dict[str, Any]] = {}
        for alert_data in alerts:
            if not isinstance(alert_data, dict):
                continue
            fp = str(alert_data.get("fingerprint", "")).strip()
            if not fp:
                continue
            alert_by_fingerprint[fp] = alert_data
            fingerprint_to_entities[fp] = _resolve_alert_entity_ids(alert_data, ontology)

        fingerprints = list(alert_by_fingerprint.keys())
        parent: dict[str, str] = {fp: fp for fp in fingerprints}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Merge alerts whose entities are connected in the topology graph
        for i in range(len(fingerprints)):
            entities_i = fingerprint_to_entities.get(fingerprints[i], [])
            for j in range(i + 1, len(fingerprints)):
                entities_j = fingerprint_to_entities.get(fingerprints[j], [])
                if _entities_topologically_connected(entities_i, entities_j, ontology):
                    union(fingerprints[i], fingerprints[j])

        # Build clusters from union-find groups
        groups: dict[str, list[str]] = {}
        for fp in fingerprints:
            root = find(fp)
            groups.setdefault(root, []).append(fp)

        severity_rank = {"critical": 3, "warning": 2, "info": 1}
        clusters: list[dict[str, Any]] = []
        for idx, group_fps in enumerate(groups.values(), start=1):
            group_alerts = [alert_by_fingerprint[fp] for fp in group_fps]
            summary = str(group_alerts[0].get("annotations", {}).get("summary", "") or group_alerts[0].get("alert_name", ""))
            top_severity = max(
                (str(a.get("severity", "info")) for a in group_alerts),
                key=lambda s: severity_rank.get(s, 0),
            )
            clusters.append({
                "cluster_id": f"cluster-{idx}",
                "summary": summary,
                "severity": top_severity,
                "alerts": group_fps,
            })
        return clusters

    def _entities_topologically_connected(
        entities_a: list[str],
        entities_b: list[str],
        ontology: Any,
        max_depth: int = 2,
    ) -> bool:
        """Check if any entity from group A has a topology path to any entity in group B."""
        if not entities_a or not entities_b:
            return False
        for ea in entities_a:
            for eb in entities_b:
                if ea == eb:
                    return True
                path = ontology.get_path(ea, eb)
                if path is not None and len(path) <= max_depth + 1:
                    return True
        return False

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
        extra_alert_fingerprints: list[str] = Query(default_factory=list),
    ) -> SREResponse[DiagnosisSession]:
        services = _services(request)
        blocked_alert_names = _blocked_alert_names_from_request(request)
        if is_blocked_alert(alert, blocked_names=blocked_alert_names):
            return _blocked_alert_response(request, alert.alert_name)
        if services.diagnosis_runner is None:
            raise HTTPException(status_code=500, detail="diagnosis runner is not configured")
        prepared_alert = _enrich_alert_with_topology_summary(services, alert)
        extra_alerts = _lookup_extra_alerts(services.alert_store, extra_alert_fingerprints) if extra_alert_fingerprints else []
        if _supports_extra_alerts(services.diagnosis_runner.adiagnose):
            session = await services.diagnosis_runner.adiagnose(prepared_alert, extra_alerts=extra_alerts)
        else:
            session = await services.diagnosis_runner.adiagnose(prepared_alert)
        plan = _extract_recommended_fix(session)
        if plan is not None:
            services.remediation_engine.register_plan(session.session_id, plan)
            session = session.model_copy(update={"status": "approval_required"})
        services.session_store.put(session)
        if plan is not None:
            plan_version = services.remediation_engine.get_latest_plan_version(session.session_id)
            await _publish_session_event(
                services,
                event_type=EventType.APPROVAL_REQUIRED,
                session_id=session.session_id,
                data={"plan_id": plan.plan_id, "plan_version": plan_version},
            )
        return SREResponse(success=True, data=session, trace_id=_trace_id(request))

    @router.post("/auth/token")
    async def issue_auth_token(
        request: Request,
        user: CurrentUser = Depends(require_role("viewer", "operator", "admin")),
    ) -> SREResponse[AuthTokenResponse]:
        _ = user
        payload = _build_auth_token_response(request, user)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.post("/auth/bootstrap")
    async def bootstrap_auth_token(
        request: Request,
    ) -> SREResponse[AuthTokenResponse]:
        if not _demo_auto_bootstrap_enabled():
            raise HTTPException(status_code=404, detail="demo auth bootstrap is disabled")
        payload = _build_auth_token_response(request, _demo_bootstrap_user())
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.post("/auth/refresh")
    async def refresh_auth_token(
        payload: AuthRefreshRequest,
        request: Request,
    ) -> SREResponse[AuthTokenResponse]:
        settings = getattr(request.app.state, "jwt_settings", None)
        if settings is None:
            raise HTTPException(status_code=500, detail="jwt settings not configured")
        try:
            refreshed_user = decode_refresh_token(payload.refresh_token, settings)
        except TokenDecodeError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        data = _build_auth_token_response(request, refreshed_user)
        return SREResponse(success=True, data=data, trace_id=_trace_id(request))

    @router.get("/auth/status")
    async def auth_status(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(auth_security),
    ) -> SREResponse[AuthStatusResponse]:
        user, auth_error_kind = _optional_current_user(request, credentials)
        expires_at: datetime | None = None
        if user is not None and credentials is not None:
            settings = getattr(request.app.state, "jwt_settings", None)
            if settings is not None:
                try:
                    expires_at = extract_expiry_datetime(credentials.credentials, settings)
                except TokenDecodeError as exc:
                    auth_error_kind = exc.kind
        response = AuthStatusResponse(
            server_boot_id=_server_boot_id(request),
            access_token_expires_at=expires_at,
            skew_hint_seconds=30,
            auth_error_kind=auth_error_kind,
        )
        return SREResponse(success=True, data=response, trace_id=_trace_id(request))

    @router.post("/diagnose/start")
    async def diagnose_start(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
        extra_alert_fingerprints: list[str] = Query(default_factory=list),
    ) -> SREResponse[DiagnosisSession]:
        _ = user
        services = _services(request)
        blocked_alert_names = _blocked_alert_names_from_request(request)
        if is_blocked_alert(alert, blocked_names=blocked_alert_names):
            return _blocked_alert_response(request, alert.alert_name)
        if services.diagnosis_runner is None:
            raise HTTPException(status_code=500, detail="diagnosis runner is not configured")
        coordinator = getattr(services, "diagnosis_start_coordinator", None)
        if coordinator is None:
            raise HTTPException(status_code=500, detail="diagnosis start coordinator is not configured")

        prepared_alert = _enrich_alert_with_topology_summary(services, alert)
        extra_alerts = _lookup_extra_alerts(services.alert_store, extra_alert_fingerprints) if extra_alert_fingerprints else []
        handle = coordinator.start(
            alert=prepared_alert,
            extra_alerts=extra_alerts,
            task_name_prefix="diagnose-start",
        )
        _track_background_task(request.app, handle.task)
        return SREResponse(success=True, data=handle.initial_session, trace_id=_trace_id(request))

    @router.post("/diagnose/stream")
    async def diagnose_stream(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
        extra_alert_fingerprints: list[str] = Query(default_factory=list),
    ):
        """Stream diagnosis events via SSE (Server-Sent Events).

        Requires ``sse-starlette`` package.
        """
        _ = user
        services = _services(request)
        blocked_alert_names = _blocked_alert_names_from_request(request)
        if is_blocked_alert(alert, blocked_names=blocked_alert_names):
            raise HTTPException(status_code=400, detail="alert is blocked")
        runner = getattr(services, "streaming_diagnosis_runner", None)
        if runner is None:
            raise HTTPException(status_code=500, detail="streaming diagnosis runner is not configured")

        prepared_alert = _enrich_alert_with_topology_summary(services, alert)
        extra_alerts = _lookup_extra_alerts(services.alert_store, extra_alert_fingerprints) if extra_alert_fingerprints else []

        # Lazy import to avoid hard dependency at module load time.
        from sse_starlette.sse import EventSourceResponse

        async def event_generator():
            latest_session_id = ""
            last_event_type = ""
            LOGGER.info(
                "diagnose_stream connected alert=%s fingerprint=%s extra_alerts=%s",
                prepared_alert.alert_name,
                prepared_alert.fingerprint,
                len(extra_alerts),
            )
            try:
                async for event in runner.astream_diagnose(prepared_alert, extra_alerts=extra_alerts):
                    latest_session_id = str(event.get("session_id", "")).strip() or latest_session_id
                    last_event_type = str(event.get("type", "")).strip() or last_event_type
                    yield {
                        "event": event.get("type", "message"),
                        "data": json.dumps(event, ensure_ascii=False, default=str),
                    }
            except asyncio.CancelledError:
                LOGGER.warning(
                    "diagnose_stream cancelled session=%s alert=%s last_event_type=%s reason=client_disconnect_or_request_cancelled",
                    latest_session_id,
                    prepared_alert.alert_name,
                    last_event_type,
                )
                if latest_session_id:
                    log_stream_lifecycle_event(
                        session_id=latest_session_id,
                        step=0,
                        mode="stream_cancelled",
                        stage="diagnose_stream.event_generator",
                        status="cancelled",
                        reason="client_disconnect_or_request_cancelled",
                        event_type=last_event_type,
                        pending_tool_calls_count=0,
                        step_count=0,
                        max_steps=0,
                    )
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception(
                    "diagnose_stream error session=%s alert=%s last_event_type=%s error=%s",
                    latest_session_id,
                    prepared_alert.alert_name,
                    last_event_type,
                    exc,
                )
                if latest_session_id:
                    log_stream_lifecycle_event(
                        session_id=latest_session_id,
                        step=0,
                        mode="stream_exception",
                        stage="diagnose_stream.event_generator",
                        status="failed",
                        reason=str(exc).strip() or exc.__class__.__name__,
                        event_type=last_event_type,
                        pending_tool_calls_count=0,
                        step_count=0,
                        max_steps=0,
                        extra={"exception_type": exc.__class__.__name__},
                    )
                yield {
                    "event": "error",
                    "data": json.dumps({"message": str(exc)}),
                }

        return EventSourceResponse(event_generator(), ping=15)

    @router.post("/handle")
    async def handle_alert(
        alert: Alert,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[LoopResult]:
        _ = user
        blocked_alert_names = _blocked_alert_names_from_request(request)
        if is_blocked_alert(alert, blocked_names=blocked_alert_names):
            return _blocked_alert_response(request, alert.alert_name)
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

    @router.get("/sessions/{session_id}/events")
    async def get_session_events(
        session_id: str,
        request: Request,
        limit: int = 200,
        after: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        services = _services(request)
        list_events = getattr(services.trace_publisher, "list_events", None)
        if not callable(list_events):
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        events = list_events(session_id, limit=max(1, int(limit)), after=after)
        payload = [event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event) for event in events]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.post("/remediate/{session_id}/plan/revise")
    async def revise_plan(
        session_id: str,
        payload: RevisePlanRequest,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[RevisePlanResponse]:
        services = _services(request)
        trace_id = _trace_id(request)
        services.audit_logger.record(
            user,
            "revise_plan",
            session_id=session_id,
            trace_id=trace_id,
            details=payload.instruction,
        )
        session = services.session_store.get(session_id)
        if session is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="session not found"),
                trace_id=trace_id,
            )
        try:
            plan_version, revised_plan = services.remediation_engine.revise_plan(
                session_id=session_id,
                instruction=payload.instruction,
                base_plan_version=payload.base_plan_version,
            )
        except KeyError:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.REMEDIATION_PLAN_INVALID, message="no remediation plan to revise"),
                trace_id=trace_id,
            )
        except ValueError as exc:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=str(exc)),
                trace_id=trace_id,
            )

        diagnosis = session.diagnosis_result
        if diagnosis is not None:
            diagnosis = diagnosis.model_copy(update={"recommended_fix": revised_plan})
        session_with_plan = session.model_copy(update={"diagnosis_result": diagnosis})
        services.session_store.put(session_with_plan)
        updated_session = _update_session_status(
            services,
            session=session_with_plan,
            status="approval_required",
            outcome=None,
        )
        await _publish_session_event(
            services,
            event_type=EventType.PLAN_REVISED,
            session_id=session_id,
            data={
                "plan_id": revised_plan.plan_id,
                "plan_version": plan_version,
                "instruction": payload.instruction,
            },
        )
        await _publish_session_event(
            services,
            event_type=EventType.APPROVAL_REQUIRED,
            session_id=session_id,
            data={"plan_id": revised_plan.plan_id, "plan_version": plan_version},
        )

        response_payload = RevisePlanResponse(
            session_id=session_id,
            plan_version=plan_version,
            plan=revised_plan.model_dump(mode="json"),
            session=updated_session.model_dump(mode="json"),
        )
        return SREResponse(success=True, data=response_payload, trace_id=trace_id)

    @router.post("/remediate/{session_id}/approve")
    async def approve_remediation(
        session_id: str,
        approval: ApprovalInput,
        request: Request,
        user: CurrentUser = Depends(require_role("operator", "admin")),
    ) -> SREResponse[RemediationResult]:
        services = _services(request)
        trace_id = _trace_id(request)
        try:
            services.audit_logger.record(user, "approve", session_id=session_id, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "audit logger failed during remediation approval: session_id=%s trace_id=%s",
                session_id,
                trace_id,
                exc_info=exc,
            )
        get_latest_plan_version = getattr(services.remediation_engine, "get_latest_plan_version", None)
        latest_plan_version = int(get_latest_plan_version(session_id)) if callable(get_latest_plan_version) else 0
        if latest_plan_version <= 0:
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_PLAN_INVALID,
                    message=f"remediation plan not found for session {session_id}",
                ),
                trace_id=trace_id,
            )
        requested_plan_version = int(approval.plan_version or latest_plan_version)
        if requested_plan_version != latest_plan_version:
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_PLAN_VERSION_OUTDATED,
                    message="plan_version_outdated",
                    details={
                        "requested_plan_version": requested_plan_version,
                        "latest_plan_version": latest_plan_version,
                    },
                ),
                trace_id=trace_id,
            )
        plan = services.remediation_engine.get_plan(session_id)
        if plan is None:
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_PLAN_INVALID,
                    message=f"remediation plan not found for session {session_id}",
                ),
                trace_id=trace_id,
            )
        if approval.approved:
            # 参数有效性检查（占位值阻断）
            plan_param_errors = _validate_plan_param_values(plan)
            if plan_param_errors:
                return SREResponse(
                    success=False,
                    error=SREError(
                        code=ErrorCode.VALIDATION_ERROR,
                        message=plan_param_errors[0],
                        details={"param_errors": plan_param_errors},
                    ),
                    trace_id=trace_id,
                )
            readiness_issue = _validate_real_execution_readiness(services, plan=plan)
            if readiness_issue is not None:
                return SREResponse(
                    success=False,
                    error=SREError(
                        code=ErrorCode.VALIDATION_ERROR,
                        message=str(readiness_issue["message"]),
                        details=readiness_issue,
                    ),
                    trace_id=trace_id,
                )
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
            stage="approval_accepted",
            details={
                "user": approval.user,
                "plan_version": requested_plan_version,
                "message": "审批通过，准备执行修复",
            },
        )
        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="execution_started",
            details={
                "user": approval.user,
                "plan_version": requested_plan_version,
                "message": "执行修复中",
            },
        )
        app_config = getattr(request.app.state, "config", None)
        remediation_cfg = getattr(app_config, "remediation", None)
        observation_seconds = int(getattr(remediation_cfg, "observation_seconds", 180) or 180) if remediation_cfg else 180
        observation_poll_seconds = (
            int(getattr(remediation_cfg, "observation_poll_seconds", 10) or 10) if remediation_cfg else 10
        )
        execution_timeout_seconds = (
            int(getattr(remediation_cfg, "execution_timeout_seconds", 600) or 600) if remediation_cfg else 600
        )
        execution_timeout_seconds = max(1, execution_timeout_seconds)
        observation_poll_seconds = max(1, observation_poll_seconds)
        workflow_started_at = time.monotonic()
        pre_check: RemediationCheckSnapshot | None = None

        async def _timeout_response() -> SREResponse[RemediationResult]:
            _update_session_status(services, session=session, status="timeout", outcome="timeout")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_timeout",
                details={
                    "message": "修复执行超时退出",
                    "timeout_seconds": execution_timeout_seconds,
                    "plan_version": requested_plan_version,
                },
            )
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_EXECUTION_FAILED,
                    message=f"remediation execution timeout ({execution_timeout_seconds}s)",
                ),
                trace_id=trace_id,
            )

        pre_check = await _capture_check_snapshot(services, session=session, plan=plan)
        pre_evidence = RemediationEvidence(pre_check=pre_check)
        session = _update_session_evidence(services, session=session, evidence=pre_evidence)
        await _publish_remediation_progress(
            services,
            session_id=session_id,
            stage="pre_remediation_baseline_collected",
            details={
                "plan_version": requested_plan_version,
                "baseline_alert": pre_check.alert.model_dump(mode="json") if pre_check.alert is not None else None,
                "baseline_metrics": [item.model_dump(mode="json") for item in pre_check.metrics],
                "pre_check": pre_check.model_dump(mode="json"),
                "collected_at": pre_evidence.collected_at.isoformat(),
                "message": "已采集修复前基线",
            },
        )

        async def _engine_progress_callback(*, stage: str, details: dict[str, Any] | None = None) -> None:
            await _publish_remediation_progress(services, session_id=session_id, stage=stage, details=details)

        try:
            result = await asyncio.wait_for(
                services.remediation_engine.approve_and_execute(
                    session_id,
                    approval,
                    progress_callback=_engine_progress_callback,
                ),
                timeout=execution_timeout_seconds,
            )
        except KeyError:
            _update_session_status(services, session=session, status="failed", outcome="failed")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_failed",
                details={
                    "error": "remediation plan not found",
                    "message": "修复执行失败：未找到修复计划",
                    "plan_version": requested_plan_version,
                },
            )
            return SREResponse(
                success=False,
                error=SREError(
                    code=ErrorCode.REMEDIATION_PLAN_INVALID,
                    message=f"remediation plan not found for session {session_id}",
                ),
                trace_id=trace_id,
            )
        except TimeoutError:
            return await _timeout_response()
        except Exception as exc:  # noqa: BLE001
            _update_session_status(services, session=session, status="failed", outcome="failed")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_failed",
                details={
                    "error": str(exc),
                    "message": "修复执行失败",
                    "plan_version": requested_plan_version,
                },
            )
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.REMEDIATION_EXECUTION_FAILED, message=str(exc)),
                trace_id=trace_id,
            )

        step_results = _extract_step_results(result)
        if result.success:
            if getattr(services.remediation_engine, "execution_mode", "real") == "mock":
                await _publish_remediation_progress(
                    services,
                    session_id=session_id,
                    stage="execution_mocked",
                    details={
                        "message": "mock 已执行修复计划",
                        "plan_id": result.plan_id,
                        "plan_version": requested_plan_version,
                        "step_results": step_results,
                    },
                )
            observed_ok = True
            observation_details: dict[str, Any] = {}
            remaining_timeout = execution_timeout_seconds - (time.monotonic() - workflow_started_at)
            if remaining_timeout <= 0:
                return await _timeout_response()
            try:
                observed_ok, observation_details, evidence = await asyncio.wait_for(
                    _observe_post_remediation(
                        services,
                        session=session,
                        pre_check=pre_check,
                        observation_seconds=observation_seconds,
                        observation_poll_seconds=observation_poll_seconds,
                    ),
                    timeout=remaining_timeout,
                )
                session = _update_session_evidence(services, session=session, evidence=evidence)
            except TimeoutError:
                return await _timeout_response()
            if not observed_ok:
                print("需要工程师介入")
                _update_session_status(services, session=session, status="escalated", outcome="escalated")
                await _publish_remediation_progress(
                    services,
                    session_id=session_id,
                    stage="escalation_required",
                    details={
                        **observation_details,
                        "message": "需要工程师介入",
                        "plan_version": requested_plan_version,
                        "step_results": step_results,
                    },
                )
                return SREResponse(
                    success=False,
                    error=SREError(
                        code=ErrorCode.REMEDIATION_EXECUTION_FAILED,
                        message="需要工程师介入",
                    ),
                    trace_id=trace_id,
                )
            _update_session_status(services, session=session, status="resolved", outcome="resolved")
            await _publish_remediation_progress(
                services,
                session_id=session_id,
                stage="execution_succeeded",
                details={
                    "plan_id": result.plan_id,
                    "steps_completed": result.steps_completed,
                    "plan_version": requested_plan_version,
                    "step_results": step_results,
                    "message": "修复执行成功",
                },
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
                "plan_version": requested_plan_version,
                "step_results": step_results,
                "message": "修复执行失败",
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

    @router.get("/topology-explorer")
    async def get_topology_explorer(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[TopologyExplorerResponse]:
        _ = user
        services = _services(request)
        payload = _topology_explorer_payload(services)
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
        blocked_alert_names = _blocked_alert_names_from_request(request)
        raw_alerts = snapshot.get("alerts", [])
        raw_clusters = snapshot.get("clusters", [])
        filtered_alerts, _ = _filter_alert_snapshot(
            raw_alerts,
            raw_clusters,
            blocked_alert_names=blocked_alert_names,
        )
        # Build topology-aware clusters instead of namespace+service grouping
        ontology = getattr(services, "ontology", None)
        topology_clusters = _build_topology_aware_clusters(filtered_alerts, ontology) if ontology is not None else []
        payload = AlertSnapshotResponse(alerts=filtered_alerts, clusters=topology_clusters)
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
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        try:
            results = await _maybe_await(knowledge.search(query=query, category=category, top_k=top_k))
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge search failed: {exc}"),
                trace_id=_trace_id(request),
            )
        if not isinstance(results, list):
            results = []
        payload = [_normalize_knowledge_document(item, index=idx) for idx, item in enumerate(results)]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/dataset")
    async def knowledge_dataset(
        request: Request,
        dataset_id: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="knowledge store is not configured"),
                trace_id=_trace_id(request),
            )
        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge, dataset_id=dataset_id)
        try:
            dataset_payload: Any | None = None
            if hasattr(knowledge, "get_dataset"):
                try:
                    dataset_payload = await _maybe_await(knowledge.get_dataset(target_dataset_id))
                except TypeError:
                    dataset_payload = await _maybe_await(knowledge.get_dataset())
            elif hasattr(knowledge, "list_datasets"):
                datasets = await _maybe_await(knowledge.list_datasets(keyword=None, page=1, limit=100))
                if isinstance(datasets, list):
                    for item in datasets:
                        candidate = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                        if isinstance(candidate, dict) and str(candidate.get("id", "")) == target_dataset_id:
                            dataset_payload = candidate
                            break
            if dataset_payload is None and hasattr(knowledge, "list_documents"):
                documents = await _maybe_await(knowledge.list_documents())
                doc_count = len(documents) if isinstance(documents, list) else 0
                dataset_payload = {
                    "id": target_dataset_id,
                    "name": "Knowledge Dataset",
                    "description": "",
                    "document_count": doc_count,
                    "status": "ready",
                }
            if dataset_payload is None:
                return SREResponse(
                    success=False,
                    error=SREError(
                        code=ErrorCode.VALIDATION_ERROR,
                        message=f"knowledge dataset not found: {target_dataset_id}",
                    ),
                    trace_id=_trace_id(request),
                )
            return SREResponse(
                success=True,
                data=_normalize_knowledge_dataset(dataset_payload, default_dataset_id=target_dataset_id),
                trace_id=_trace_id(request),
            )
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge dataset query failed: {exc}"),
                trace_id=_trace_id(request),
            )

    @router.get("/knowledge/datasets")
    async def knowledge_datasets(
        request: Request,
        keyword: str | None = None,
        page: int = 1,
        limit: int = 50,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))

        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge)
        datasets: list[Any] = []
        try:
            if hasattr(knowledge, "list_datasets"):
                list_attempts = [
                    lambda: knowledge.list_datasets(keyword=keyword, page=max(1, int(page)), limit=max(1, int(limit))),
                    lambda: knowledge.list_datasets(page=max(1, int(page)), limit=max(1, int(limit))),
                    lambda: knowledge.list_datasets(),
                ]
                for attempt in list_attempts:
                    try:
                        datasets = await _maybe_await(attempt())
                        break
                    except TypeError:
                        continue
            elif hasattr(knowledge, "get_dataset"):
                get_attempts = [
                    lambda: knowledge.get_dataset(target_dataset_id),
                    lambda: knowledge.get_dataset(),
                ]
                for attempt in get_attempts:
                    try:
                        item = await _maybe_await(attempt())
                        datasets = [item] if item else []
                        break
                    except TypeError:
                        continue
            if not isinstance(datasets, list):
                datasets = []

            payload = [_normalize_knowledge_dataset(item, default_dataset_id=target_dataset_id) for item in datasets]
            if not payload:
                payload = [
                    _normalize_knowledge_dataset(
                        {
                            "id": target_dataset_id,
                            "name": "Knowledge Dataset",
                            "description": "",
                            "status": "ready",
                        },
                        default_dataset_id=target_dataset_id,
                    )
                ]
            return SREResponse(success=True, data=payload, trace_id=_trace_id(request))
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge datasets query failed: {exc}"),
                trace_id=_trace_id(request),
            )

    @router.get("/knowledge/bases")
    async def knowledge_bases(
        request: Request,
        keyword: str | None = None,
        page: int = 1,
        limit: int = 50,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        datasets_response = await knowledge_datasets(
            request=request,
            keyword=keyword,
            page=page,
            limit=limit,
            user=user,
        )
        if not datasets_response.success:
            return SREResponse(
                success=False,
                error=datasets_response.error,
                trace_id=_trace_id(request),
            )

        knowledge = _services(request).knowledge
        default_dataset_id = _knowledge_default_dataset_id(request, knowledge)
        datasets = datasets_response.data if isinstance(datasets_response.data, list) else []
        payload = [_normalize_knowledge_base_summary(item, default_dataset_id=default_dataset_id) for item in datasets]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/bases/{knowledge_base_id}")
    async def knowledge_base_detail(
        knowledge_base_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        target_id = str(knowledge_base_id).strip()
        if not target_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="knowledge base not found")

        datasets_response = await knowledge_datasets(
            request=request,
            keyword=None,
            page=1,
            limit=200,
            user=user,
        )
        if not datasets_response.success:
            return SREResponse(
                success=False,
                error=datasets_response.error,
                trace_id=_trace_id(request),
            )

        datasets = datasets_response.data if isinstance(datasets_response.data, list) else []
        selected_dataset = next(
            (
                item
                for item in datasets
                if str(_as_payload_dict(item).get("id", "")).strip() == target_id
            ),
            None,
        )
        if selected_dataset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"knowledge base not found: {target_id}")

        documents_response = await knowledge_documents(
            request=request,
            keyword=None,
            dataset_id=target_id,
            page=1,
            limit=200,
            user=user,
        )
        if not documents_response.success:
            return SREResponse(
                success=False,
                error=documents_response.error,
                trace_id=_trace_id(request),
            )

        documents = documents_response.data if isinstance(documents_response.data, list) else []
        payload = _normalize_knowledge_base_detail(selected_dataset, documents, default_dataset_id=target_id)
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/documents")
    async def knowledge_documents(
        request: Request,
        keyword: str | None = None,
        dataset_id: str | None = None,
        page: int = 1,
        limit: int = 50,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))

        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge, dataset_id=dataset_id)
        documents: list[Any] = []
        try:
            if hasattr(knowledge, "list_documents"):
                list_attempts = [
                    lambda: knowledge.list_documents(
                        target_dataset_id,
                        page=max(1, int(page)),
                        limit=max(1, int(limit)),
                        keyword=keyword,
                    ),
                    lambda: knowledge.list_documents(page=max(1, int(page)), limit=max(1, int(limit)), keyword=keyword),
                    lambda: knowledge.list_documents(target_dataset_id),
                    lambda: knowledge.list_documents(),
                ]
                for attempt in list_attempts:
                    try:
                        documents = await _maybe_await(attempt())
                        break
                    except TypeError:
                        continue
            elif hasattr(knowledge, "list_files") and target_dataset_id:
                documents = await _maybe_await(
                    knowledge.list_files(
                        target_dataset_id,
                        page=max(1, int(page)),
                        limit=max(1, int(limit)),
                        keyword=keyword,
                    )
                )
            elif hasattr(knowledge, "search"):
                documents = await _maybe_await(knowledge.search(query=keyword or "", top_k=max(1, int(limit))))
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge documents query failed: {exc}"),
                trace_id=_trace_id(request),
            )

        if not isinstance(documents, list):
            documents = []
        payload = [_normalize_knowledge_document(item, index=idx) for idx, item in enumerate(documents)]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/documents/{document_id}")
    async def knowledge_document_detail(
        document_id: str,
        request: Request,
        dataset_id: str | None = None,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="knowledge store is not configured"),
                trace_id=_trace_id(request),
            )
        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge, dataset_id=dataset_id)
        method = getattr(knowledge, "get_document", None)
        if method is None:
            method = getattr(knowledge, "get_file", None)
        if not callable(method):
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="knowledge provider does not support document detail"),
                trace_id=_trace_id(request),
            )
        try:
            try:
                detail = await _maybe_await(method(target_dataset_id, document_id))
            except TypeError:
                detail = await _maybe_await(method(document_id))
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge document query failed: {exc}"),
                trace_id=_trace_id(request),
            )
        payload = _normalize_knowledge_document(detail, index=0)
        payload["id"] = document_id
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/documents/{document_id}/segments")
    async def knowledge_document_segments(
        document_id: str,
        request: Request,
        keyword: str | None = None,
        status: str | None = None,
        dataset_id: str | None = None,
        page: int = 1,
        limit: int = 50,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge, dataset_id=dataset_id)

        method = getattr(knowledge, "list_document_segments", None)
        if method is None:
            method = getattr(knowledge, "retrieve_chunks", None)
        if not callable(method):
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="knowledge provider does not support document segments"),
                trace_id=_trace_id(request),
            )
        try:
            try:
                segments = await _maybe_await(
                    method(
                        target_dataset_id,
                        document_id,
                        page=max(1, int(page)),
                        limit=max(1, int(limit)),
                        keyword=keyword,
                        status=status,
                    )
                )
            except TypeError:
                try:
                    segments = await _maybe_await(
                        method(
                            target_dataset_id,
                            document_id,
                            page=max(1, int(page)),
                            limit=max(1, int(limit)),
                            keyword=keyword,
                        )
                    )
                except TypeError:
                    try:
                        segments = await _maybe_await(
                            method(document_id, page=max(1, int(page)), limit=max(1, int(limit)), keyword=keyword, status=status)
                        )
                    except TypeError:
                        segments = await _maybe_await(
                            method(document_id, page=max(1, int(page)), limit=max(1, int(limit)), keyword=keyword)
                        )
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge segments query failed: {exc}"),
                trace_id=_trace_id(request),
            )
        if not isinstance(segments, list):
            segments = []

        payload = [_normalize_knowledge_segment(item, index=idx, document_id=document_id) for idx, item in enumerate(segments)]
        if status:
            normalized_status = status.strip().lower()
            payload = [item for item in payload if str(item.get("status", "")).strip().lower() == normalized_status]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/knowledge/segments/search")
    async def knowledge_segments_search(
        query: str,
        request: Request,
        dataset_id: str | None = None,
        top_k: int = 5,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[list[dict[str, Any]]]:
        _ = user
        knowledge = _services(request).knowledge
        if knowledge is None:
            return SREResponse(success=True, data=[], trace_id=_trace_id(request))
        if not hasattr(knowledge, "search"):
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message="knowledge provider does not support search"),
                trace_id=_trace_id(request),
            )
        target_dataset_id = _knowledge_resolved_dataset_id(request, knowledge=knowledge, dataset_id=dataset_id)
        records: list[Any] = []
        try:
            search_resolved = False
            retrieve_method = getattr(knowledge, "_retrieve", None)
            map_method = getattr(knowledge, "_to_result_item", None)
            if dataset_id and callable(retrieve_method) and callable(map_method):
                raw_records = await _maybe_await(
                    retrieve_method(dataset_id=target_dataset_id, query=query, top_k=max(1, int(top_k)))
                )
                if isinstance(raw_records, list):
                    records = [
                        map_method(item, dataset_id=target_dataset_id, dataset_name=None)
                        if isinstance(item, dict)
                        else item
                        for item in raw_records
                    ]
                else:
                    records = []
                search_resolved = True
            if not search_resolved:
                search_attempts: list[Any]
                if dataset_id:
                    search_attempts = [
                        lambda: knowledge.search(query=query, top_k=max(1, int(top_k)), dataset_id=target_dataset_id),
                        lambda: knowledge.search(query=query, category=target_dataset_id, top_k=max(1, int(top_k))),
                        lambda: knowledge.search(query=query, top_k=max(1, int(top_k))),
                    ]
                else:
                    search_attempts = [lambda: knowledge.search(query=query, top_k=max(1, int(top_k)))]
                for attempt in search_attempts:
                    try:
                        candidate = await _maybe_await(attempt())
                        records = candidate if isinstance(candidate, list) else []
                        search_resolved = True
                        break
                    except TypeError:
                        continue
                if not search_resolved:
                    records = []
        except Exception as exc:  # noqa: BLE001
            return SREResponse(
                success=False,
                error=SREError(code=ErrorCode.VALIDATION_ERROR, message=f"knowledge segment search failed: {exc}"),
                trace_id=_trace_id(request),
            )
        if not isinstance(records, list):
            records = []
        payload = [_normalize_knowledge_segment(item, index=idx) for idx, item in enumerate(records)]
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
                "description": item.description,
                "summary": item.summary,
                "source": item.source,
                "path": item.path,
                "scripts": item.scripts,
                "references": item.references,
                "permissions": item.permissions,
                "match_score": item.match_score,
                "status": "available",
                "updated_at": datetime.fromtimestamp(item.skill_file.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z"),
                "lifecycle_status": "published" if item.scope == "builtin" else "draft",
                "file_name": item.skill_file.name,
            }
            for item in skills
        ]
        return SREResponse(success=True, data=payload, trace_id=_trace_id(request))

    @router.get("/skills/{skill_id}")
    async def get_skill_detail(
        skill_id: str,
        request: Request,
        user: CurrentUser = Depends(get_current_user),
    ) -> SREResponse[dict[str, Any]]:
        _ = user
        target_id = str(skill_id).strip()
        if not target_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill not found")

        registry = SkillRegistry()
        try:
            skill, markdown_content = registry.load_skill(target_id)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        payload = {
            "id": skill.id,
            "name": skill.name,
            "scope": skill.scope,
            "description": skill.description,
            "summary": skill.summary,
            "source": skill.source,
            "path": skill.path,
            "scripts": skill.scripts,
            "references": skill.references,
            "permissions": skill.permissions,
            "match_score": skill.match_score,
            "status": "available",
            "updated_at": datetime.fromtimestamp(skill.skill_file.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z"),
            "lifecycle_status": "published" if skill.scope == "builtin" else "draft",
            "file_name": skill.skill_file.name,
            "markdown_content": markdown_content,
        }
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
