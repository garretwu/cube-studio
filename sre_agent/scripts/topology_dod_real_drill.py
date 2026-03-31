#!/usr/bin/env python3
"""Run topology/ontology real-environment DoD drill and archive evidence.

DoD scope:
1) automatic discovery is active
2) periodic refresh is observed
3) manual discovery trigger works and topology WS replay works
4) diagnosis includes topology blast-radius context (hit)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    index = max(0, min(index, len(ordered) - 1))
    return ordered[index]


def _ws_base_from_http(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    raise ValueError(f"unsupported base url: {base_url}")


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _ensure_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for category in ("api", "ws", "metrics", "report"):
        (root / category).mkdir(parents=True, exist_ok=True)


def _load_runtime_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_token(explicit: str, runtime_info: dict[str, Any], role: str, user: str) -> str:
    if explicit.strip():
        return explicit.strip()
    env_token = os.getenv("SRE_API_TOKEN", "").strip()
    if env_token:
        return env_token
    runtime_token = str(runtime_info.get("frontend_bearer_token", "")).strip()
    if runtime_token:
        return runtime_token
    settings = resolve_jwt_settings()
    current = CurrentUser(user_id=f"dod-{user}", username=user, role=role)  # type: ignore[arg-type]
    return encode_token(current, settings)


def _resolve_base_url(explicit: str, runtime_info: dict[str, Any]) -> str:
    if explicit.strip():
        return _normalize_base_url(explicit)
    runtime_url = str(runtime_info.get("backend_url", "")).strip()
    if runtime_url:
        return _normalize_base_url(runtime_url)
    env_url = os.getenv("SRE_BASE_URL", "").strip()
    if env_url:
        return _normalize_base_url(env_url)
    return "http://127.0.0.1:8000"


def _choose_blast_target(nodes: list[dict[str, Any]]) -> tuple[str | None, str | None, str | None]:
    if not nodes:
        return None, None, None
    preferred_types = {
        "hardware",
        "network_device",
        "switch",
        "host",
        "node",
        "pod",
        "k8s_pod",
        "prometheus",
        "metric_endpoint",
    }
    for node in nodes:
        node_id = str(node.get("id", "")).strip()
        name = str(node.get("name", "")).strip()
        node_type = str(node.get("entity_type", "")).strip()
        if node_type in preferred_types and (node_id or name):
            return (node_id or None), (name or None), (node_type or None)
    first = nodes[0]
    node_id = str(first.get("id", "")).strip() or None
    name = str(first.get("name", "")).strip() or None
    node_type = str(first.get("entity_type", "")).strip() or None
    return node_id, name, node_type


def _candidate_node_order(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred_types = {
        "hardware",
        "network_device",
        "switch",
        "host",
        "node",
        "pod",
        "k8s_pod",
        "prometheus",
        "metric_endpoint",
        "switch_port",
    }
    preferred: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for node in nodes:
        node_type = str(node.get("entity_type", "")).strip()
        if node_type in preferred_types:
            preferred.append(node)
        else:
            others.append(node)
    return preferred + others


def _extract_event_id(payload: dict[str, Any]) -> int | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    raw = data.get("event_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


async def _capture_topology_ws_for_manual_trigger(
    *,
    ws_base: str,
    token: str,
    max_events: int,
    timeout_seconds: float,
    trigger_call: Any,
) -> dict[str, Any]:
    try:
        import websockets
    except ModuleNotFoundError:
        return {"supported": False, "reason": "python package 'websockets' is not installed", "events": []}

    url = f"{ws_base}/ws/topology?token={quote(token)}"
    events: list[dict[str, Any]] = []
    start = asyncio.get_running_loop().time()
    async with websockets.connect(url, open_timeout=5, close_timeout=1) as websocket:
        trigger_response = await trigger_call()
        while len(events) < max_events and asyncio.get_running_loop().time() - start < timeout_seconds:
            remain = timeout_seconds - (asyncio.get_running_loop().time() - start)
            if remain <= 0:
                break
            try:
                raw = await asyncio.wait_for(websocket.recv(), timeout=min(1.5, remain))
            except TimeoutError:
                continue
            payload = json.loads(raw)
            payload["received_at"] = _now_iso()
            events.append(payload)
            if payload.get("data", {}).get("action") in {"sync_succeeded", "sync_failed"}:
                break
    return {"supported": True, "events": events, "trigger_response": trigger_response}


async def _check_topology_ws_resume(
    *,
    ws_base: str,
    token: str,
    checkpoint_event_id: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        import websockets
    except ModuleNotFoundError:
        return {"supported": False, "success": False, "reason": "python package 'websockets' is not installed"}

    url = f"{ws_base}/ws/topology?token={quote(token)}&last_event_id={quote(str(checkpoint_event_id))}"
    try:
        async with websockets.connect(url, open_timeout=5, close_timeout=1) as websocket:
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)
        payload = json.loads(raw)
        received_event_id = _extract_event_id(payload)
        return {
            "supported": True,
            "success": bool(received_event_id and received_event_id > checkpoint_event_id),
            "checkpoint_event_id": checkpoint_event_id,
            "received_event_id": received_event_id,
            "received_action": payload.get("data", {}).get("action"),
            "event": payload,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "supported": True,
            "success": False,
            "checkpoint_event_id": checkpoint_event_id,
            "reason": str(exc),
        }


@dataclass(frozen=True)
class DrillArgs:
    base_url: str
    token: str
    role: str
    user: str
    request_timeout_seconds: float
    periodic_wait_seconds: int
    periodic_poll_seconds: int
    ws_timeout_seconds: float
    ws_max_events: int
    evidence_root: Path
    run_id: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run topology real-env DoD drill and persist evidence.")
    parser.add_argument("--base-url", default="", help="Backend base URL. If empty, read runtime info/env/default.")
    parser.add_argument("--token", default="", help="Bearer token. If empty, runtime info/env/JWT fallback.")
    parser.add_argument(
        "--runtime-info",
        default="sre_agent/temp/dev_runtime_info.json",
        help="Runtime info file generated by start_frontend_backend.py",
    )
    parser.add_argument("--role", default="operator", choices=["viewer", "operator", "admin"], help="JWT role fallback.")
    parser.add_argument("--user", default="topology-dod", help="JWT username fallback.")
    parser.add_argument("--request-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--periodic-wait-seconds", type=int, default=150, help="Wait window for periodic refresh.")
    parser.add_argument("--periodic-poll-seconds", type=int, default=5)
    parser.add_argument("--ws-timeout-seconds", type=float, default=12.0)
    parser.add_argument("--ws-max-events", type=int, default=80)
    parser.add_argument("--evidence-root", default="sre_agent/docs/evidence", help="Evidence root directory.")
    parser.add_argument("--run-id", default="", help="Run id. Default: YYYYMMDD-HHMM-topology-dod")
    return parser.parse_args()


def _build_drill_args(parsed: argparse.Namespace) -> DrillArgs:
    runtime_info = _load_runtime_info(Path(parsed.runtime_info))
    run_id = parsed.run_id or f"{datetime.now().strftime('%Y%m%d-%H%M')}-topology-dod"
    return DrillArgs(
        base_url=_resolve_base_url(parsed.base_url, runtime_info),
        token=_resolve_token(parsed.token, runtime_info, parsed.role, parsed.user),
        role=parsed.role,
        user=parsed.user,
        request_timeout_seconds=max(5.0, float(parsed.request_timeout_seconds)),
        periodic_wait_seconds=max(30, int(parsed.periodic_wait_seconds)),
        periodic_poll_seconds=max(2, int(parsed.periodic_poll_seconds)),
        ws_timeout_seconds=max(2.0, float(parsed.ws_timeout_seconds)),
        ws_max_events=max(10, int(parsed.ws_max_events)),
        evidence_root=Path(parsed.evidence_root),
        run_id=run_id,
    )


def _http_exchange(
    exchanges: list[dict[str, Any]],
    *,
    method: str,
    path: str,
    status_code: int,
    request_body: dict[str, Any] | None,
    response_body: dict[str, Any] | list[Any] | str | None,
    started_at: str,
    ended_at: str,
    elapsed_seconds: float,
) -> None:
    exchanges.append(
        {
            "method": method,
            "path": path,
            "status_code": status_code,
            "request_body": request_body,
            "response_body": response_body,
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_seconds": elapsed_seconds,
        }
    )


async def _run(d: DrillArgs) -> tuple[dict[str, Any], int]:
    run_dir = d.evidence_root / d.run_id
    _ensure_dirs(run_dir)
    headers = {
        "Authorization": f"Bearer {d.token}",
        "Content-Type": "application/json",
        "x-trace-id": f"topology-dod-{int(time.time())}",
    }
    exchanges: list[dict[str, Any]] = []
    ws_events: list[dict[str, Any]] = []
    periodic_samples: list[dict[str, Any]] = []
    manual_sync_duration_samples: list[float] = []
    selected_target_blast_json: dict[str, Any] | None = None
    selected_target_blast_count: int | None = None

    async with httpx.AsyncClient(base_url=d.base_url, timeout=d.request_timeout_seconds, trust_env=False) as client:
        # 1) auto-discovery status
        s_start = _now_iso()
        t0 = time.monotonic()
        status_resp = await client.get("/api/topology/status", headers=headers)
        t1 = time.monotonic()
        s_end = _now_iso()
        status_json: dict[str, Any] = status_resp.json()
        _http_exchange(
            exchanges,
            method="GET",
            path="/api/topology/status",
            status_code=status_resp.status_code,
            request_body=None,
            response_body=status_json,
            started_at=s_start,
            ended_at=s_end,
            elapsed_seconds=t1 - t0,
        )
        if status_resp.status_code != 200:
            raise RuntimeError(f"/api/topology/status failed: {status_resp.status_code} {status_json}")

        status_data = status_json.get("data", {}) if isinstance(status_json, dict) else {}
        initial_last_synced = _parse_iso(status_data.get("last_synced_at"))
        initial_snapshot_id = str(status_data.get("snapshot_id") or "")
        initial_sync_state = str(status_data.get("sync_state") or "")

        auto_discovery_pass = bool(initial_last_synced) and initial_sync_state in {"ready", "degraded"}

        # 2) topology snapshot and target entity
        s_start = _now_iso()
        t0 = time.monotonic()
        top_resp = await client.get("/api/topology", headers=headers)
        t1 = time.monotonic()
        s_end = _now_iso()
        top_json: dict[str, Any] = top_resp.json()
        _http_exchange(
            exchanges,
            method="GET",
            path="/api/topology",
            status_code=top_resp.status_code,
            request_body=None,
            response_body=top_json,
            started_at=s_start,
            ended_at=s_end,
            elapsed_seconds=t1 - t0,
        )
        if top_resp.status_code != 200:
            raise RuntimeError(f"/api/topology failed: {top_resp.status_code} {top_json}")
        top_data = top_json.get("data", {}) if isinstance(top_json, dict) else {}
        nodes = top_data.get("nodes", []) if isinstance(top_data, dict) else []
        edges = top_data.get("edges", []) if isinstance(top_data, dict) else []
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(edges, list):
            edges = []
        target_id, target_name, target_type = _choose_blast_target(nodes)
        by_id: dict[str, dict[str, Any]] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id", "")).strip()
            if node_id:
                by_id[node_id] = node

        for candidate in _candidate_node_order([node for node in nodes if isinstance(node, dict)]):
            candidate_id = str(candidate.get("id", "")).strip()
            if not candidate_id:
                continue
            s_start = _now_iso()
            t0 = time.monotonic()
            probe_resp = await client.post(
                "/api/ontology/blast",
                headers=headers,
                json={"entity_id": candidate_id},
            )
            t1 = time.monotonic()
            s_end = _now_iso()
            probe_json: dict[str, Any] = probe_resp.json()
            _http_exchange(
                exchanges,
                method="POST",
                path="/api/ontology/blast",
                status_code=probe_resp.status_code,
                request_body={"entity_id": candidate_id},
                response_body=probe_json,
                started_at=s_start,
                ended_at=s_end,
                elapsed_seconds=t1 - t0,
            )
            if probe_resp.status_code != 200:
                continue
            probe_data = probe_json.get("data", {}) if isinstance(probe_json, dict) else {}
            if not isinstance(probe_data, dict):
                continue
            raw_count = probe_data.get("affected_count")
            if not isinstance(raw_count, int):
                continue
            if raw_count > 0:
                target_id = candidate_id
                target_name = str(candidate.get("name", "")).strip() or None
                target_type = str(candidate.get("entity_type", "")).strip() or None
                selected_target_blast_json = probe_json
                selected_target_blast_count = raw_count
                break

        # 3) periodic refresh detect (status changes without manual trigger)
        periodic_refresh_pass = False
        periodic_detected_at: str | None = None
        periodic_snapshot_id: str | None = None
        periodic_elapsed_seconds: float | None = None
        periodic_sync_state: str | None = None
        periodic_start_monotonic = time.monotonic()
        baseline_sync = initial_last_synced
        while time.monotonic() - periodic_start_monotonic < d.periodic_wait_seconds:
            await asyncio.sleep(d.periodic_poll_seconds)
            s_start = _now_iso()
            t0 = time.monotonic()
            sample_resp = await client.get("/api/topology/status", headers=headers)
            t1 = time.monotonic()
            s_end = _now_iso()
            sample_json: dict[str, Any] = sample_resp.json()
            _http_exchange(
                exchanges,
                method="GET",
                path="/api/topology/status",
                status_code=sample_resp.status_code,
                request_body=None,
                response_body=sample_json,
                started_at=s_start,
                ended_at=s_end,
                elapsed_seconds=t1 - t0,
            )
            if sample_resp.status_code != 200:
                continue
            sample_data = sample_json.get("data", {}) if isinstance(sample_json, dict) else {}
            sample_sync = _parse_iso(sample_data.get("last_synced_at"))
            sample_snapshot_id = str(sample_data.get("snapshot_id") or "")
            sample_state = str(sample_data.get("sync_state") or "")
            periodic_samples.append(
                {
                    "observed_at": s_end,
                    "last_synced_at": sample_data.get("last_synced_at"),
                    "snapshot_id": sample_snapshot_id,
                    "sync_state": sample_state,
                }
            )
            if sample_sync and baseline_sync and sample_sync > baseline_sync:
                periodic_refresh_pass = True
                periodic_detected_at = s_end
                periodic_snapshot_id = sample_snapshot_id
                periodic_sync_state = sample_state
                periodic_elapsed_seconds = time.monotonic() - periodic_start_monotonic
                break
            if sample_sync and baseline_sync is None:
                periodic_refresh_pass = True
                periodic_detected_at = s_end
                periodic_snapshot_id = sample_snapshot_id
                periodic_sync_state = sample_state
                periodic_elapsed_seconds = time.monotonic() - periodic_start_monotonic
                break

        # 4) manual trigger + ws incremental events + ws resume
        ws_base = _ws_base_from_http(d.base_url)

        async def _trigger_manual() -> dict[str, Any]:
            s_start = _now_iso()
            t0 = time.monotonic()
            resp = await client.post("/api/topology/discover", headers=headers)
            t1 = time.monotonic()
            s_end = _now_iso()
            body = resp.json()
            _http_exchange(
                exchanges,
                method="POST",
                path="/api/topology/discover",
                status_code=resp.status_code,
                request_body={},
                response_body=body,
                started_at=s_start,
                ended_at=s_end,
                elapsed_seconds=t1 - t0,
            )
            return {"status_code": resp.status_code, "body": body, "elapsed_seconds": t1 - t0}

        capture = await _capture_topology_ws_for_manual_trigger(
            ws_base=ws_base,
            token=d.token,
            max_events=d.ws_max_events,
            timeout_seconds=d.ws_timeout_seconds,
            trigger_call=_trigger_manual,
        )
        ws_events = list(capture.get("events", [])) if isinstance(capture, dict) else []
        trigger_response = capture.get("trigger_response", {}) if isinstance(capture, dict) else {}

        manual_trigger_pass = False
        manual_status_ok = (
            isinstance(trigger_response, dict)
            and int(trigger_response.get("status_code", 0)) == 200
            and isinstance(trigger_response.get("body"), dict)
            and bool(trigger_response.get("body", {}).get("success"))
        )
        ws_has_sync_succeeded = any(
            isinstance(item, dict) and item.get("data", {}).get("action") == "sync_succeeded" for item in ws_events
        )
        ws_has_sync_failed = any(
            isinstance(item, dict) and item.get("data", {}).get("action") == "sync_failed" for item in ws_events
        )
        if manual_status_ok and ws_has_sync_succeeded and not ws_has_sync_failed:
            manual_trigger_pass = True

        sync_started_ts: datetime | None = None
        sync_done_ts: datetime | None = None
        for item in ws_events:
            if not isinstance(item, dict):
                continue
            action = item.get("data", {}).get("action")
            event_ts = _parse_iso(str(item.get("timestamp") or ""))
            if action == "sync_started":
                sync_started_ts = event_ts
            if action == "sync_succeeded":
                sync_done_ts = event_ts
                break
        if sync_started_ts and sync_done_ts and sync_done_ts >= sync_started_ts:
            manual_sync_duration_samples.append((sync_done_ts - sync_started_ts).total_seconds())

        ws_resume_result: dict[str, Any] = {"supported": False, "success": False, "reason": "no ws events"}
        event_ids = [eid for eid in (_extract_event_id(item) for item in ws_events) if eid is not None]
        if len(event_ids) >= 2:
            checkpoint = sorted(event_ids)[-2]
            ws_resume_result = await _check_topology_ws_resume(
                ws_base=ws_base,
                token=d.token,
                checkpoint_event_id=checkpoint,
                timeout_seconds=3.0,
            )

        # 5) diagnose with topology labels and verify blast-radius hit
        label_value = target_name or target_id or "node-a"
        now = _now_iso()
        alert_payload = {
            "alert_name": "TopologyDoDLatencyHigh",
            "severity": "critical",
            "labels": {
                "node": label_value,
                "instance": label_value,
                "service": "topology-dod-check",
            },
            "annotations": {
                "summary": "Topology DoD blast-radius verification",
                "description": f"target={label_value}, type={target_type or 'unknown'}",
            },
            "starts_at": now,
            "fingerprint": f"topology-dod-{int(time.time())}",
            "status": "firing",
            "source": "topology-dod-script",
        }

        s_start = _now_iso()
        t0 = time.monotonic()
        diag_resp: httpx.Response | None = None
        diag_json: dict[str, Any] = {}
        diag_error: str | None = None
        try:
            diag_resp = await client.post("/api/diagnose", headers=headers, json=alert_payload)
            diag_json = diag_resp.json()
        except Exception as exc:  # noqa: BLE001
            diag_error = str(exc)
            diag_json = {
                "success": False,
                "error": {"message": str(exc)},
            }
        t1 = time.monotonic()
        s_end = _now_iso()
        _http_exchange(
            exchanges,
            method="POST",
            path="/api/diagnose",
            status_code=diag_resp.status_code if diag_resp is not None else 599,
            request_body=alert_payload,
            response_body=diag_json,
            started_at=s_start,
            ended_at=s_end,
            elapsed_seconds=t1 - t0,
        )
        diagnosis_pass = diag_resp is not None and diag_resp.status_code == 200 and bool(diag_json.get("success"))
        if not diagnosis_pass and not diag_error:
            if diag_resp is None:
                diag_error = "diagnose request failed before receiving response"
            else:
                err_text = ""
                if isinstance(diag_json, dict):
                    raw_error = diag_json.get("error")
                    if isinstance(raw_error, dict):
                        err_text = str(raw_error.get("message") or raw_error)
                diag_error = f"status={diag_resp.status_code}, error={err_text or 'unknown'}"
        diag_data = diag_json.get("data") if isinstance(diag_json, dict) else {}
        if not isinstance(diag_data, dict):
            diag_data = {}
        session_id = str(diag_data.get("session_id") or "")
        raw_alert_obj = diag_data.get("alert", {})
        annotations = raw_alert_obj.get("annotations", {}) if isinstance(raw_alert_obj, dict) else {}
        blast_summary = str(annotations.get("topology_blast_radius_summary") or "")
        affected_match = re.search(r"affected_count=(\d+)", blast_summary)
        affected_count = int(affected_match.group(1)) if affected_match else 0
        has_non_none_root = "roots=['none']" not in blast_summary and 'roots=["none"]' not in blast_summary
        blast_radius_hit_pass = diagnosis_pass and bool(blast_summary) and has_non_none_root and affected_count > 0

        trace_json: dict[str, Any] | None = None
        if session_id:
            s_start = _now_iso()
            t0 = time.monotonic()
            trace_resp = await client.get(f"/api/sessions/{session_id}/trace", headers=headers)
            t1 = time.monotonic()
            s_end = _now_iso()
            trace_json = trace_resp.json()
            _http_exchange(
                exchanges,
                method="GET",
                path=f"/api/sessions/{session_id}/trace",
                status_code=trace_resp.status_code,
                request_body=None,
                response_body=trace_json,
                started_at=s_start,
                ended_at=s_end,
                elapsed_seconds=t1 - t0,
            )

        blast_api_json: dict[str, Any] | None = selected_target_blast_json
        blast_api_affected_count: int | None = selected_target_blast_count
        if target_id:
            if blast_api_json is None:
                s_start = _now_iso()
                t0 = time.monotonic()
                blast_resp = await client.post(
                    "/api/ontology/blast",
                    headers=headers,
                    json={"entity_id": target_id},
                )
                t1 = time.monotonic()
                s_end = _now_iso()
                blast_api_json = blast_resp.json()
                _http_exchange(
                    exchanges,
                    method="POST",
                    path="/api/ontology/blast",
                    status_code=blast_resp.status_code,
                    request_body={"entity_id": target_id},
                    response_body=blast_api_json,
                    started_at=s_start,
                    ended_at=s_end,
                    elapsed_seconds=t1 - t0,
                )
                if blast_resp.status_code == 200 and isinstance(blast_api_json, dict):
                    blast_data = blast_api_json.get("data", {})
                    if isinstance(blast_data, dict):
                        raw_count = blast_data.get("affected_count")
                        if isinstance(raw_count, int):
                            blast_api_affected_count = raw_count

    node_missing = 0
    for node in nodes:
        if not isinstance(node, dict):
            node_missing += 1
            continue
        if not str(node.get("id", "")).strip() or not str(node.get("entity_type", "")).strip():
            node_missing += 1
    node_total = len(nodes)
    node_missing_rate = (node_missing / node_total) if node_total else 1.0

    ws_reconnect_success_rate = 1.0 if ws_resume_result.get("success") else 0.0
    discovery_success_observations = 0
    discovery_total_observations = 0
    for sample in periodic_samples:
        discovery_total_observations += 1
        if str(sample.get("sync_state")) in {"ready", "degraded"}:
            discovery_success_observations += 1
    discovery_total_observations += 1
    if initial_sync_state in {"ready", "degraded"}:
        discovery_success_observations += 1
    if manual_trigger_pass:
        discovery_total_observations += 1
        discovery_success_observations += 1

    discovery_success_rate = (
        discovery_success_observations / discovery_total_observations if discovery_total_observations else 0.0
    )
    blast_radius_hit_rate = 1.0 if blast_radius_hit_pass else 0.0
    refresh_duration_p95 = _percentile(manual_sync_duration_samples, 0.95)
    overall_pass = auto_discovery_pass and periodic_refresh_pass and manual_trigger_pass and blast_radius_hit_pass

    summary = {
        "run_id": d.run_id,
        "generated_at": _now_iso(),
        "base_url": d.base_url,
        "checks": {
            "auto_discovery_pass": auto_discovery_pass,
            "periodic_refresh_pass": periodic_refresh_pass,
            "manual_trigger_pass": manual_trigger_pass,
            "blast_radius_hit_pass": blast_radius_hit_pass,
            "overall_pass": overall_pass,
        },
        "metrics": {
            "discovery_success_rate": discovery_success_rate,
            "refresh_duration_seconds_p95": refresh_duration_p95,
            "ws_reconnect_success_rate": ws_reconnect_success_rate,
            "node_missing_rate": node_missing_rate,
            "blast_radius_hit_rate": blast_radius_hit_rate,
        },
        "observations": {
            "initial_status": {
                "sync_state": initial_sync_state,
                "last_synced_at": status_data.get("last_synced_at"),
                "snapshot_id": initial_snapshot_id,
            },
            "periodic_detection": {
                "detected": periodic_refresh_pass,
                "detected_at": periodic_detected_at,
                "snapshot_id": periodic_snapshot_id,
                "sync_state": periodic_sync_state,
                "elapsed_seconds": periodic_elapsed_seconds,
                "wait_window_seconds": d.periodic_wait_seconds,
                "poll_interval_seconds": d.periodic_poll_seconds,
            },
            "topology": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "target_entity_id": target_id,
                "target_entity_name": target_name,
                "target_entity_type": target_type,
            },
            "manual_discovery": {
                "ws_event_count": len(ws_events),
                "ws_resume": ws_resume_result,
                "manual_sync_duration_samples_seconds": manual_sync_duration_samples,
            },
            "diagnosis": {
                "session_id": session_id,
                "topology_blast_radius_summary": blast_summary,
                "summary_affected_count": affected_count,
                "ontology_blast_api_affected_count": blast_api_affected_count,
                "diagnose_error": diag_error,
            },
        },
    }

    (run_dir / "api" / "http_exchanges.json").write_text(
        json.dumps(exchanges, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "api" / "periodic_status_samples.json").write_text(
        json.dumps(periodic_samples, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "ws" / "topology_events.json").write_text(
        json.dumps(ws_events, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics" / "topology_dod_metrics.json").write_text(
        json.dumps(summary.get("metrics", {}), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report" / "topology_dod_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_lines = [
        f"# Topology DoD Report ({d.run_id})",
        "",
        f"- generated_at: {summary['generated_at']}",
        f"- base_url: {d.base_url}",
        "",
        "## Checks",
        f"- auto_discovery_pass: {auto_discovery_pass}",
        f"- periodic_refresh_pass: {periodic_refresh_pass}",
        f"- manual_trigger_pass: {manual_trigger_pass}",
        f"- blast_radius_hit_pass: {blast_radius_hit_pass}",
        f"- overall_pass: {overall_pass}",
        "",
        "## Metrics",
        f"- discovery_success_rate: {discovery_success_rate:.3f}",
        f"- refresh_duration_seconds_p95: {refresh_duration_p95}",
        f"- ws_reconnect_success_rate: {ws_reconnect_success_rate:.3f}",
        f"- node_missing_rate: {node_missing_rate:.3f}",
        f"- blast_radius_hit_rate: {blast_radius_hit_rate:.3f}",
        "",
        "## Notes",
        f"- topology_nodes={len(nodes)}, topology_edges={len(edges)}",
        f"- target_entity_id={target_id}, target_entity_name={target_name}, target_entity_type={target_type}",
        f"- diagnosis_session_id={summary['observations']['diagnosis']['session_id']}",
        f"- blast_summary={blast_summary}",
        "",
    ]
    (run_dir / "report" / "topology_dod_report.md").write_text("\n".join(md_lines), encoding="utf-8")

    exit_code = 0 if overall_pass else 2
    return summary, exit_code


def main() -> int:
    parsed = _parse_args()
    args = _build_drill_args(parsed)
    summary, exit_code = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(str(args.evidence_root / args.run_id))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
