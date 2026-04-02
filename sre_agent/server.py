"""FastAPI application factory for Agent C."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Protocol
from uuid import uuid4

import yaml
from fastapi import FastAPI

from lib.channels.alert import AlertChannel
from sre_agent.agent import ConversationalAgent, run_diagnosis
from sre_agent.alerts_filter import build_blocked_alert_name_set, filter_blocked_alerts
from sre_agent.api import build_api_router, build_websocket_router, install_middlewares
from sre_agent.api.routes import AuditLogger
from sre_agent.auth.jwt import CurrentUser, JWTSettings, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.concurrency import AlertCorrelator, AlertDeduplicator, ResourceLock
from sre_agent.knowledge.store import KnowledgeStore
from sre_agent.memory.factory import create_memory_store
from sre_agent.models.alert import Alert, AlertSeverity
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.events import EventType, WSEvent
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.remediation import ApprovalGate, IncidentHandler, LoopConfig, LoopOrchestrator, PlanValidator, RemediationEngine, RollbackJournal
from sre_agent.runtime import bootstrap_tool_channels, register_remediation_channel
from sre_agent.topology.discovery import discover_live_snapshot, discover_static_snapshot
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

    def list_events(self, session_id: str, *, limit: int | None = None, after: str | None = None) -> list[WSEvent]:
        events = list(self._events.get(session_id, []))
        start_index = 0
        if after is not None:
            for pos, event in enumerate(events):
                if str(event.data.get("event_id")) == str(after):
                    start_index = pos + 1
                    break
        sliced = events[start_index:]
        if limit is not None and limit > 0:
            return sliced[-limit:]
        return sliced


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
        blocked_alert_names: set[str] | None = None,
    ) -> None:
        self._channel = channel
        self._alert_store = alert_store
        self._trace_publisher = trace_publisher
        self._poll_interval_seconds = max(0.2, float(poll_interval_seconds))
        self._blocked_alert_names = blocked_alert_names or set()
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
                    alerts = filter_blocked_alerts(alerts, blocked_names=self._blocked_alert_names)
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


@dataclass(frozen=True)
class TopologySyncStatus:
    snapshot_id: str | None
    sync_state: Literal["idle", "syncing", "ready", "degraded", "error"]
    mode: str
    last_synced_at: datetime | None
    last_started_at: datetime | None
    last_error: str | None
    scanner_counts: dict[str, dict[str, int]]

    def as_json(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "sync_state": self.sync_state,
            "mode": self.mode,
            "last_synced_at": self.last_synced_at.isoformat() if self.last_synced_at else None,
            "last_started_at": self.last_started_at.isoformat() if self.last_started_at else None,
            "last_error": self.last_error,
            "scanner_counts": self.scanner_counts,
        }


class TopologyDiscoveryService:
    def __init__(
        self,
        *,
        config: SREAgentConfig,
        ontology: OntologyGraph,
        trace_publisher: InMemoryTracePublisher,
    ) -> None:
        self._config = config
        self._ontology = ontology
        self._trace_publisher = trace_publisher
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._recent_events: deque[str] = deque(maxlen=100)
        self._snapshot_id: str | None = None
        self._last_synced_at: datetime | None = None
        self._last_started_at: datetime | None = None
        self._last_error: str | None = None
        self._scanner_counts: dict[str, dict[str, int]] = {}
        self._sync_state: Literal["idle", "syncing", "ready", "degraded", "error"] = "idle"
        mode = str(self._config.ontology.discovery.mode or "static").strip().lower()
        self._mode = mode if mode in {"static", "live"} else "static"

    async def start(self) -> None:
        if not bool(self._config.ontology.discovery.auto_discovery):
            LOGGER.info("topology discovery service disabled by config")
            return
        await self.trigger_discovery(reason="startup")
        interval = max(0, int(self._config.ontology.discovery.refresh_interval_seconds))
        if interval <= 0:
            return
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(interval), name="sre-topology-discovery")

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

    def status(self) -> TopologySyncStatus:
        return TopologySyncStatus(
            snapshot_id=self._snapshot_id,
            sync_state=self._sync_state,
            mode=self._mode,
            last_synced_at=self._last_synced_at,
            last_started_at=self._last_started_at,
            last_error=self._last_error,
            scanner_counts=dict(self._scanner_counts),
        )

    def recent_events(self, limit: int = 20) -> list[str]:
        safe_limit = max(1, int(limit))
        return list(self._recent_events)[-safe_limit:]

    async def trigger_discovery(self, *, reason: str) -> TopologySyncStatus:
        async with self._lock:
            self._last_started_at = datetime.now(UTC)
            self._sync_state = "syncing"
            await self._publish_event(
                action="sync_started",
                payload={
                    "reason": reason,
                    "mode": self._mode,
                    "started_at": self._last_started_at.isoformat(),
                },
            )
            self._record_event(f"[{self._last_started_at.isoformat()}] topology sync started ({reason})")

            try:
                previous_nodes = {node.id: node for node in self._ontology.list_entities()}
                previous_edges = {
                    (edge.source_id, edge.target_id, edge.relation.value): edge for edge in self._ontology.list_edges()
                }
                nodes, edges, scanner_counts, effective_mode, fallback_reason = await self._discover_once()
                preserve_existing = not nodes and not edges and bool(previous_nodes)
                if preserve_existing:
                    reason = "discovery returned empty snapshot; retained existing topology"
                    fallback_reason = f"{fallback_reason}; {reason}" if fallback_reason else reason
                    next_nodes = previous_nodes
                    next_edges = previous_edges
                else:
                    await self._replace_graph(nodes=nodes, edges=edges)
                    next_nodes = {node.id: node for node in self._ontology.list_entities()}
                    next_edges = {
                        (edge.source_id, edge.target_id, edge.relation.value): edge for edge in self._ontology.list_edges()
                    }
                    await self._publish_diff(
                        previous_nodes=previous_nodes,
                        previous_edges=previous_edges,
                        next_nodes=next_nodes,
                        next_edges=next_edges,
                    )

                self._snapshot_id = uuid4().hex
                self._last_synced_at = datetime.now(UTC)
                self._scanner_counts = scanner_counts
                self._last_error = fallback_reason
                if fallback_reason:
                    self._sync_state = "degraded"
                else:
                    self._sync_state = "ready"
                payload = {
                    "reason": reason,
                    "mode": effective_mode,
                    "snapshot_id": self._snapshot_id,
                    "last_synced_at": self._last_synced_at.isoformat(),
                    "sync_state": self._sync_state,
                    "scanner_counts": scanner_counts,
                }
                if fallback_reason:
                    payload["fallback_reason"] = fallback_reason
                await self._publish_event(action="sync_succeeded", payload=payload)
                self._record_event(
                    f"[{self._last_synced_at.isoformat()}] topology sync succeeded "
                    f"(mode={effective_mode}, nodes={len(next_nodes)}, edges={len(next_edges)})"
                )
            except Exception as exc:  # noqa: BLE001
                self._sync_state = "error"
                self._last_error = str(exc)
                self._record_event(f"[{datetime.now(UTC).isoformat()}] topology sync failed: {exc}")
                await self._publish_event(
                    action="sync_failed",
                    payload={
                        "reason": reason,
                        "mode": self._mode,
                        "error": str(exc),
                    },
                )
                LOGGER.warning("topology discovery failed: %s", exc)
            return self.status()

    async def _run_loop(self, interval_seconds: int) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_seconds)
                continue
            except TimeoutError:
                pass
            try:
                await self.trigger_discovery(reason="periodic")
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("topology periodic sync failed: %s", exc)

    async def _discover_once(
        self,
    ) -> tuple[list[Any], list[Any], dict[str, dict[str, int]], str, str | None]:
        fallback_reason: str | None = None
        if self._mode == "live":
            try:
                nodes, edges, scanner_counts = await discover_live_snapshot(
                    self._config,
                    inventory_path=Path(self._config.ontology.discovery.live_inventory_path),
                )
                return nodes, edges, scanner_counts, "live", None
            except Exception as exc:  # noqa: BLE001
                if not bool(self._config.ontology.discovery.live_fallback_to_static):
                    raise
                fallback_reason = f"live discovery failed and fell back to static: {exc}"
                LOGGER.warning(fallback_reason)
        nodes, edges, scanner_counts = await discover_static_snapshot(self._config)
        return nodes, edges, scanner_counts, "static", fallback_reason

    async def _replace_graph(self, *, nodes: list[Any], edges: list[Any]) -> None:
        for node in self._ontology.list_entities():
            await self._ontology.remove_node(node.id)
        await self._ontology.add_nodes(nodes)
        await self._ontology.add_edges(edges)

    async def _publish_diff(
        self,
        *,
        previous_nodes: dict[str, Any],
        previous_edges: dict[tuple[str, str, str], Any],
        next_nodes: dict[str, Any],
        next_edges: dict[tuple[str, str, str], Any],
    ) -> None:
        for node_id in sorted(set(previous_nodes) - set(next_nodes)):
            await self._publish_event(action="node_remove", payload={"id": node_id})
        for node_id in sorted(next_nodes):
            if node_id in previous_nodes and previous_nodes[node_id] == next_nodes[node_id]:
                continue
            await self._publish_event(
                action="node_upsert",
                payload={"node": next_nodes[node_id].model_dump(mode="json")},
            )

        for edge_key in sorted(set(previous_edges) - set(next_edges)):
            await self._publish_event(
                action="edge_remove",
                payload={
                    "source_id": edge_key[0],
                    "target_id": edge_key[1],
                    "relation": edge_key[2],
                },
            )
        for edge_key in sorted(next_edges):
            if edge_key in previous_edges and previous_edges[edge_key] == next_edges[edge_key]:
                continue
            await self._publish_event(
                action="edge_upsert",
                payload={"edge": next_edges[edge_key].model_dump(mode="json")},
            )

    async def _publish_event(self, *, action: str, payload: dict[str, Any]) -> None:
        await self._trace_publisher.publish(
            {
                "type": EventType.TOPOLOGY.value,
                "session_id": "topology",
                "data": {"action": action, **payload},
            }
        )

    def _record_event(self, message: str) -> None:
        self._recent_events.append(message)


class MissingDiagnosisRunner:
    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        _ = (alert, trace_callback)
        raise RuntimeError("diagnosis runner is not configured")


def _extract_alert_entities(alert: Alert) -> list[str]:
    ordered_keys = (
        "node",
        "instance",
        "service",
        "pod",
        "switch",
        "host",
        "job",
        "kubernetes_node",
    )
    values: list[str] = []
    for key in ordered_keys:
        raw = alert.labels.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        if text and text not in values:
            values.append(text)
    return values


def _compact_blast_entity(entity: Any) -> dict[str, Any]:
    if hasattr(entity, "model_dump"):
        payload = entity.model_dump(mode="json")
    elif isinstance(entity, dict):
        payload = dict(entity)
    else:
        payload = {"id": str(entity), "entity_type": "unknown", "properties": {}}
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    key_attributes = {}
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


def _build_alert_blast_radius_context(ontology: OntologyGraph, alert: Alert) -> dict[str, Any]:
    resolved_entities: list[str] = []
    blast_entities: list[dict[str, Any]] = []
    total_affected = 0
    for candidate in _extract_alert_entities(alert):
        target_id = candidate
        if ontology.get_entity(target_id) is None:
            by_name = ontology.find_entities(filters={"name": target_id})
            if by_name:
                target_id = by_name[0].id
            else:
                continue
        if target_id in resolved_entities:
            continue
        resolved_entities.append(target_id)
        blast = ontology.get_blast_radius(target_id)
        affected = blast.get("affected_entities", [])
        if isinstance(affected, list):
            blast_entities.extend([_compact_blast_entity(item) for item in affected])
            total_affected += len(affected)

    deduped: dict[str, dict[str, Any]] = {}
    for entity in blast_entities:
        entity_id = str(entity.get("id", "")).strip()
        if entity_id:
            deduped[entity_id] = entity
    entity_list = list(deduped.values())
    entity_preview = ", ".join(item["id"] for item in entity_list[:6]) if entity_list else "none"
    summary = (
        f"topology blast radius: roots={resolved_entities or ['none']}, "
        f"affected_count={len(entity_list)}, affected_preview={entity_preview}"
    )
    return {
        "roots": resolved_entities,
        "affected_count": len(entity_list),
        "affected_entities": entity_list,
        "summary": summary,
        "raw_affected_count": total_affected,
    }


class DefaultDiagnosisRunner:
    def __init__(
        self,
        *,
        execution_context: ToolExecutionContext,
        tool_registry: Any,
        config: SREAgentConfig,
        ontology: OntologyGraph,
    ) -> None:
        self._execution_context = execution_context
        self._tool_registry = tool_registry
        self._config = config
        self._ontology = ontology

    async def adiagnose(
        self,
        alert: Alert,
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        topology_context = _build_alert_blast_radius_context(self._ontology, alert)
        annotations = dict(alert.annotations)
        annotations["topology_blast_radius_summary"] = str(topology_context["summary"])
        enriched_alert = alert.model_copy(update={"annotations": annotations})
        query = f"{_build_default_query(enriched_alert)}\n{topology_context['summary']}"
        result = await run_diagnosis(
            query=query,
            context=self._execution_context,
            variables={
                "alert_name": enriched_alert.alert_name,
                "severity": enriched_alert.severity.value,
                "labels": enriched_alert.labels,
                "annotations": enriched_alert.annotations,
                "aidc_id": self._config.global_.aidc_id,
                "topology_blast_radius": topology_context,
            },
            tool_registry=self._tool_registry,
            checkpoint_dir=None,
            trace_callback=trace_callback,
        )
        return _diagnosis_session_from_state(alert=enriched_alert, state=result)


class DefaultReDiagnoseRunner:
    def __init__(
        self,
        *,
        execution_context: ToolExecutionContext,
        tool_registry: Any,
        config: SREAgentConfig,
        ontology: OntologyGraph,
    ) -> None:
        self._execution_context = execution_context
        self._tool_registry = tool_registry
        self._config = config
        self._ontology = ontology

    async def re_diagnose(
        self,
        session: DiagnosisSession,
        context: dict[str, Any],
        trace_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> DiagnosisSession:
        alert = session.alert
        topology_context = _build_alert_blast_radius_context(self._ontology, alert)
        query = (
            f"{_build_default_query(alert)}\n\n"
            f"Previous diagnosis session failed remediation attempts. "
            f"Failed candidates: {context.get('failed_candidates', [])}. "
            f"Re-diagnose and provide updated ranked candidates.\n"
            f"{topology_context['summary']}"
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
                "topology_blast_radius": topology_context,
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
    channel_states = {
        name: payload.get("health", "unknown")
        for name, payload in sorted((services.tool_channel_status or {}).items())
    }
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
        "runtime modes: alert=%s memory=%s knowledge=%s ws_max_events_per_session=%s tool_runtime=%s",
        alert_mode,
        memory_mode,
        knowledge_mode,
        ws_retention,
        services.tool_runtime_mode,
    )
    LOGGER.warning("tool channel states: %s", channel_states if channel_states else {"none": "unknown"})
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
    topology_discovery: TopologyDiscoveryService | None = None
    chat_handler: ChatHandlerProtocol | None = None
    tool_channel_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_runtime_mode: str = "degraded"
    llm_runtime_status: dict[str, Any] = field(default_factory=dict)


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


def _resolve_tool_runtime_mode(cfg: SREAgentConfig) -> str:
    mode = str(getattr(cfg.tool_runtime, "mode", "degraded")).strip().lower()
    return "strict" if mode == "strict" else "degraded"


def _collect_llm_runtime_status() -> dict[str, Any]:
    key_name = ""
    api_key = (os.getenv("SRE_OPENAI_API_KEY") or "").strip()
    if api_key:
        key_name = "SRE_OPENAI_API_KEY"
    else:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if api_key:
            key_name = "OPENAI_API_KEY"
    model = (os.getenv("SRE_LLM_MODEL") or "gpt-4o-mini").strip()
    base_url = (os.getenv("SRE_OPENAI_BASE_URL") or "").strip()
    status = {
        "ready": bool(api_key),
        "required": True,
        "api_key_configured": bool(api_key),
        "api_key_source": key_name or "none",
        "api_key_length": len(api_key),
        "model": model,
        "base_url": base_url or None,
        "reason": None if api_key else "SRE_OPENAI_API_KEY or OPENAI_API_KEY is required",
    }
    return status


def _should_require_llm_runtime(
    *,
    diagnosis_runner: DiagnosisRunnerProtocol | None,
    re_diagnose_runner: ReDiagnoseRunnerProtocol | None,
    chat_handler: ChatHandlerProtocol | None,
) -> bool:
    # Any default runtime component needs environment-backed LLM initialization.
    return diagnosis_runner is None or re_diagnose_runner is None or chat_handler is None


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
    blocked_alert_names = build_blocked_alert_name_set(getattr(cfg.global_, "blocked_alert_names", None))
    return AlertPollingService(
        channel=channel,
        alert_store=alert_store,
        trace_publisher=trace_publisher,
        poll_interval_seconds=interval,
        blocked_alert_names=blocked_alert_names,
    )


def _normalize_secret_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.upper() in {"REPLACE_ME", "CHANGEME", "TODO"}:
        return ""
    return text


def _read_inventory_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_redfish_preauth_targets(cfg: SREAgentConfig) -> list[dict[str, Any]]:
    mode = str(cfg.ontology.discovery.mode or "static").strip().lower()
    if mode != "live":
        return []
    inventory_path = Path(str(cfg.ontology.discovery.live_inventory_path or "").strip())
    payload = _read_inventory_payload(inventory_path)
    workers = payload.get("inventory", {}).get("workers", [])
    if not isinstance(workers, list):
        return []

    env_username = _normalize_secret_value(os.getenv("SRE_REDFISH_USERNAME", ""))
    env_password = _normalize_secret_value(os.getenv("SRE_REDFISH_PASSWORD", ""))
    env_verify_tls_raw = str(os.getenv("SRE_REDFISH_VERIFY_TLS", "")).strip().lower()
    env_verify_tls: bool | None
    if env_verify_tls_raw in {"1", "true", "yes"}:
        env_verify_tls = True
    elif env_verify_tls_raw in {"0", "false", "no"}:
        env_verify_tls = False
    else:
        env_verify_tls = None

    targets: list[dict[str, Any]] = []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        redfish = worker.get("redfish")
        if not isinstance(redfish, dict):
            continue
        bmc_host = str(redfish.get("bmc_host", "")).strip()
        if not bmc_host:
            continue
        username = _normalize_secret_value(redfish.get("username")) or env_username
        password = _normalize_secret_value(redfish.get("password")) or env_password
        if not username or not password:
            continue
        verify_tls_raw = redfish.get("verify_tls")
        if verify_tls_raw is None and env_verify_tls is not None:
            verify_tls = env_verify_tls
        else:
            verify_tls = bool(True if verify_tls_raw is None else verify_tls_raw)
        targets.append(
            {
                "worker": str(worker.get("name", "")).strip() or bmc_host,
                "bmc_host": bmc_host,
                "username": username,
                "password": password,
                "verify_tls": verify_tls,
            }
        )
    return targets


def _set_channel_status(
    *,
    services: AgentCServices,
    context: ToolExecutionContext,
    channel_name: str,
    health: str,
    last_error: str | None,
    mode: str | None = None,
) -> None:
    previous = services.tool_channel_status.get(channel_name, {})
    payload: dict[str, Any] = dict(previous)
    payload["name"] = channel_name
    payload["health"] = health
    payload["last_error"] = last_error
    payload["last_checked_at"] = datetime.now(UTC).isoformat()
    if mode:
        payload["mode"] = mode
    services.tool_channel_status[channel_name] = payload
    context.metadata.setdefault("channel_status", {})[channel_name] = payload
    context.metadata.setdefault("channel_health", {})[channel_name] = health


async def _preload_redfish_sessions(
    *,
    cfg: SREAgentConfig,
    context: ToolExecutionContext,
    services: AgentCServices,
) -> None:
    redfish_channel = context.channels.get("redfish")
    if redfish_channel is None or not hasattr(redfish_channel, "authenticate"):
        return
    mode = str(cfg.ontology.discovery.mode or "static").strip().lower()
    if mode != "live":
        return

    targets = _load_redfish_preauth_targets(cfg)
    if not targets:
        _set_channel_status(
            services=services,
            context=context,
            channel_name="redfish",
            health="degraded",
            last_error="no redfish credentials discovered from live inventory or SRE_REDFISH_* environment variables",
            mode="channel",
        )
        return

    ok_count = 0
    errors: list[str] = []
    for item in targets:
        bmc_host = str(item["bmc_host"])
        username = str(item["username"])
        password = str(item["password"])
        verify_tls = bool(item["verify_tls"])
        if hasattr(redfish_channel, "set_web_credentials"):
            try:
                redfish_channel.set_web_credentials(bmc_host, username, password)
            except Exception:  # noqa: BLE001
                # best effort only; Redfish API token auth is still attempted below
                pass
        try:
            result = await redfish_channel.authenticate(
                bmc_host,
                username,
                password,
                verify_tls=verify_tls,
            )
            if bool(getattr(result, "success", False)):
                ok_count += 1
                continue
            message = str(getattr(result, "error", "") or "authentication failed")
            errors.append(f"{bmc_host}: {message}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{bmc_host}: {exc}")

    total = len(targets)
    if ok_count == total:
        _set_channel_status(
            services=services,
            context=context,
            channel_name="redfish",
            health="ready",
            last_error=None,
            mode="channel+preauth",
        )
        LOGGER.info("redfish pre-auth succeeded for %s/%s hosts", ok_count, total)
        return

    preview = "; ".join(errors[:3])
    suffix = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
    _set_channel_status(
        services=services,
        context=context,
        channel_name="redfish",
        health="degraded",
        last_error=f"redfish pre-auth succeeded {ok_count}/{total}: {preview}{suffix}",
        mode="channel+preauth",
    )
    LOGGER.warning("redfish pre-auth degraded: succeeded %s/%s hosts", ok_count, total)


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
    require_llm_ready: bool = True,
) -> FastAPI:
    cfg = config or SREAgentConfig()
    jwt_settings = resolve_jwt_settings(cfg.auth)
    llm_runtime_status = _collect_llm_runtime_status()
    llm_required = _should_require_llm_runtime(
        diagnosis_runner=diagnosis_runner,
        re_diagnose_runner=re_diagnose_runner,
        chat_handler=chat_handler,
    )
    llm_runtime_status["required"] = llm_required
    if require_llm_ready and llm_required and not llm_runtime_status["ready"]:
        raise RuntimeError(str(llm_runtime_status["reason"]))
    LOGGER.warning(
        "llm runtime status: required=%s ready=%s source=%s key_length=%s model=%s base_url=%s",
        llm_runtime_status["required"],
        llm_runtime_status["ready"],
        llm_runtime_status["api_key_source"],
        llm_runtime_status["api_key_length"],
        llm_runtime_status["model"],
        llm_runtime_status["base_url"] or "<default>",
    )

    ontology_graph = ontology or OntologyGraph(cfg.ontology.db_path)
    created_ontology = ontology is None
    memory_store = memory or create_memory_store(aidc_id=cfg.global_.aidc_id, db_dir=cfg.memory.db_dir)
    created_memory = memory is None
    knowledge_store = knowledge or KnowledgeStore(persist_dir=cfg.knowledge_base.persist_dir)
    registry = tool_registry or build_default_registry()
    context = execution_context or ToolExecutionContext()
    if "alert" not in context.channels:
        alertmanager_url = str(cfg.global_.alertmanager_url or "").strip()
        if alertmanager_url:
            try:
                context.channels["alert"] = AlertChannel(
                    alertmanager_url=alertmanager_url,
                    prometheus_url=str(cfg.global_.prometheus_url or "").strip(),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("failed to inject alert channel from config: %s", exc)
    bootstrap_result = bootstrap_tool_channels(
        cfg=cfg,
        context=context,
        ontology=ontology_graph,
        memory_store=memory_store,
        knowledge_store=knowledge_store,
    )
    if _resolve_tool_runtime_mode(cfg) == "strict":
        unmet_core = [
            name
            for name in getattr(cfg.tool_runtime, "core_required_channels", ["ssh", "k8s", "prometheus"])
            if getattr(bootstrap_result.statuses.get(str(name)), "health", None) != "ready"
        ]
        if unmet_core:
            raise RuntimeError(f"tool runtime strict mode startup blocked: unavailable core channels={sorted(set(unmet_core))}")
    default_diagnosis_runner: DiagnosisRunnerProtocol | None = None
    default_re_diagnose_runner: ReDiagnoseRunnerProtocol | None = None
    if diagnosis_runner is None or re_diagnose_runner is None:
        try:
            if diagnosis_runner is None:
                default_diagnosis_runner = DefaultDiagnosisRunner(
                    execution_context=context,
                    tool_registry=registry,
                    config=cfg,
                    ontology=ontology_graph,
                )
            if re_diagnose_runner is None:
                default_re_diagnose_runner = DefaultReDiagnoseRunner(
                    execution_context=context,
                    tool_registry=registry,
                    config=cfg,
                    ontology=ontology_graph,
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
        execution_mode=cfg.remediation.execution_mode,
    )
    register_remediation_channel(
        context=context,
        statuses=bootstrap_result.statuses,
        remediation_engine=engine,
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

    topology_discovery_service = TopologyDiscoveryService(
        config=cfg,
        ontology=ontology_graph,
        trace_publisher=publisher,
    )
    services = AgentCServices(
        diagnosis_runner=final_diagnosis_runner,
        remediation_engine=engine,
        incident_handler=handler,
        ontology=ontology_graph,
        memory=memory_store,
        knowledge=knowledge_store,
        session_store=session_store,
        loop_store=loop_store,
        trace_publisher=publisher,
        alert_store=alert_store,
        audit_logger=AuditLogger(),
        topology_discovery=topology_discovery_service,
        chat_handler=runtime_chat_handler,
        tool_channel_status={name: status.as_json() for name, status in bootstrap_result.statuses.items()},
        tool_runtime_mode=bootstrap_result.runtime_mode,
        llm_runtime_status=llm_runtime_status,
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
        await _preload_redfish_sessions(
            cfg=cfg,
            context=context,
            services=services,
        )
        if alert_polling_service is not None:
            await alert_polling_service.start()
        if topology_discovery_service is not None and created_ontology:
            await topology_discovery_service.start()
        try:
            yield
        finally:
            if topology_discovery_service is not None:
                await topology_discovery_service.stop()
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
