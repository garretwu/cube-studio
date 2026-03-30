#!/usr/bin/env python3
"""Run a real Wave-5 remediation drill and archive API/WS/metrics evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings


@dataclass(frozen=True)
class DrillSettings:
    base_url: str
    evidence_root: Path
    run_id: str
    user: str
    role: str
    ws_max_events: int
    ws_timeout_seconds: int
    reconnect_attempts: int
    token: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a controlled Wave-5 real-environment drill.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL.")
    parser.add_argument("--evidence-root", default="sre_agent/docs/evidence", help="Evidence root directory.")
    parser.add_argument("--run-id", default="", help="Run id, default: YYYYMMDD-HHMM-wave5-real")
    parser.add_argument("--user", default="wave5-operator", help="Approval user name.")
    parser.add_argument("--role", default="operator", choices=["viewer", "operator", "admin"], help="JWT role.")
    parser.add_argument("--token", default="", help="Bearer token. If empty, generate from local JWT settings.")
    parser.add_argument("--ws-max-events", type=int, default=200, help="Maximum WS events to capture.")
    parser.add_argument("--ws-timeout-seconds", type=int, default=8, help="WS capture timeout in seconds.")
    parser.add_argument("--reconnect-attempts", type=int, default=5, help="WS reconnect checks to run.")
    return parser.parse_args()


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _build_token(explicit: str, user: str, role: str) -> str:
    if explicit.strip():
        return explicit.strip()
    env_token = os.getenv("SRE_API_TOKEN", "").strip()
    if env_token:
        return env_token
    settings = resolve_jwt_settings()
    current = CurrentUser(user_id=f"wave5-{user}", username=user, role=role)  # type: ignore[arg-type]
    return encode_token(current, settings)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * q))
    return ordered[max(0, min(index, len(ordered) - 1))]


def _ws_base_from_http(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    raise ValueError(f"unsupported base url: {base_url}")


def _ensure_dirs(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for category in ("api", "ws", "metrics", "ui"):
        (root / category).mkdir(parents=True, exist_ok=True)


def _post_json(
    client: httpx.Client,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    exchanges: list[dict[str, Any]],
) -> dict[str, Any]:
    url = f"{client.base_url}{path}"
    started_at = _now_iso()
    response = client.request(method, path, headers=headers, json=payload)
    ended_at = _now_iso()
    body: dict[str, Any]
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        body = {"raw": response.text}
    exchanges.append(
        {
            "method": method,
            "url": url,
            "request_body": payload,
            "status_code": response.status_code,
            "response_body": body,
            "started_at": started_at,
            "ended_at": ended_at,
        }
    )
    response.raise_for_status()
    return body


async def _capture_ws_events(
    *,
    ws_base: str,
    session_id: str,
    token: str,
    last_event_id: str,
    max_events: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        import websockets
    except ModuleNotFoundError:
        return {"supported": False, "reason": "python package 'websockets' is not installed", "events": []}

    url = f"{ws_base}/ws/thinking-trace/{quote(session_id)}?token={quote(token)}&last_event_id={quote(last_event_id)}"
    deadline = asyncio.get_running_loop().time() + max(1, timeout_seconds)
    events: list[dict[str, Any]] = []
    try:
        async with websockets.connect(url, open_timeout=5, close_timeout=1) as websocket:
            while len(events) < max_events and asyncio.get_running_loop().time() < deadline:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=min(1.0, remaining))
                except TimeoutError:
                    break
                payload = json.loads(raw)
                payload["received_at"] = _now_iso()
                events.append(payload)
    except Exception as exc:  # noqa: BLE001
        return {"supported": True, "error": str(exc), "events": events}
    return {"supported": True, "events": events}


async def _run_reconnect_checks(
    *,
    ws_base: str,
    session_id: str,
    token: str,
    checkpoints: list[str],
) -> list[dict[str, Any]]:
    if not checkpoints:
        return []
    try:
        import websockets
    except ModuleNotFoundError:
        return [{"checkpoint": cp, "success": False, "reason": "websockets package missing"} for cp in checkpoints]

    results: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        url = (
            f"{ws_base}/ws/thinking-trace/{quote(session_id)}"
            f"?token={quote(token)}&last_event_id={quote(checkpoint)}"
        )
        try:
            async with websockets.connect(url, open_timeout=5, close_timeout=1) as websocket:
                raw = await asyncio.wait_for(websocket.recv(), timeout=2.0)
            payload = json.loads(raw)
            next_event_id = str(payload.get("data", {}).get("event_id", ""))
            success = bool(next_event_id) and next_event_id != checkpoint
            results.append(
                {
                    "checkpoint": checkpoint,
                    "success": success,
                    "next_event_id": next_event_id,
                    "received_type": payload.get("type"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"checkpoint": checkpoint, "success": False, "reason": str(exc)})
    return results


def _compute_event_missing_rate(event_ids: list[int]) -> tuple[float, int]:
    if len(event_ids) <= 1:
        return 0.0, 0
    ordered = sorted(set(event_ids))
    expected = ordered[-1] - ordered[0] + 1
    missing = max(0, expected - len(ordered))
    if expected <= 0:
        return 0.0, 0
    return missing / expected, missing


def _compute_metrics(events: list[dict[str, Any]], reconnect: list[dict[str, Any]]) -> dict[str, Any]:
    int_event_ids: list[int] = []
    delivery_latencies: list[float] = []
    stages: dict[str, str] = {}
    for event in events:
        event_id = event.get("data", {}).get("event_id")
        if isinstance(event_id, str) and event_id.isdigit():
            int_event_ids.append(int(event_id))
        elif isinstance(event_id, int):
            int_event_ids.append(event_id)

        event_time = event.get("timestamp")
        received_at = event.get("received_at")
        if isinstance(event_time, str) and isinstance(received_at, str):
            try:
                latency = (_parse_iso(received_at) - _parse_iso(event_time)).total_seconds()
                if latency >= 0:
                    delivery_latencies.append(latency)
            except Exception:  # noqa: BLE001
                pass

        if event.get("type") == "remediation_progress":
            stage = event.get("data", {}).get("stage")
            if isinstance(stage, str) and stage not in stages:
                stages[stage] = event.get("timestamp", "")

    missing_rate, missing_count = _compute_event_missing_rate(int_event_ids)
    reconnect_total = len(reconnect)
    reconnect_success = sum(1 for item in reconnect if item.get("success") is True)
    execution_duration = None
    rollback_duration = None
    if "execution_started" in stages and "execution_succeeded" in stages:
        execution_duration = (_parse_iso(stages["execution_succeeded"]) - _parse_iso(stages["execution_started"])).total_seconds()
    if "rollback_started" in stages and "rollback_succeeded" in stages:
        rollback_duration = (_parse_iso(stages["rollback_succeeded"]) - _parse_iso(stages["rollback_started"])).total_seconds()

    return {
        "computed_at": _now_iso(),
        "event_count": len(events),
        "event_id_min": min(int_event_ids) if int_event_ids else None,
        "event_id_max": max(int_event_ids) if int_event_ids else None,
        "event_missing_count": missing_count,
        "event_missing_rate": missing_rate,
        "ws_delivery_seconds_p95": _percentile(delivery_latencies, 0.95),
        "ws_delivery_seconds_samples": delivery_latencies,
        "reconnect_attempts": reconnect_total,
        "reconnect_successes": reconnect_success,
        "reconnect_success_rate": (reconnect_success / reconnect_total) if reconnect_total else None,
        "execution_duration_seconds": execution_duration,
        "rollback_duration_seconds": rollback_duration,
        "stage_timestamps": stages,
    }


def main() -> int:
    args = _parse_args()
    run_id = args.run_id or f"{datetime.now().strftime('%Y%m%d-%H%M')}-wave5-real"
    token = _build_token(args.token, args.user, args.role)
    settings = DrillSettings(
        base_url=_normalize_base_url(args.base_url),
        evidence_root=Path(args.evidence_root),
        run_id=run_id,
        user=args.user,
        role=args.role,
        ws_max_events=max(10, args.ws_max_events),
        ws_timeout_seconds=max(2, args.ws_timeout_seconds),
        reconnect_attempts=max(1, args.reconnect_attempts),
        token=token,
    )

    run_dir = settings.evidence_root / settings.run_id
    _ensure_dirs(run_dir)

    headers = {
        "Authorization": f"Bearer {settings.token}",
        "Content-Type": "application/json",
        "x-trace-id": f"wave5-drill-{uuid4().hex}",
    }
    now = _now_iso()
    alert_payload = {
        "alert_name": "Wave5RealDrillSynthetic",
        "severity": "critical",
        "labels": {
            "namespace": "service",
            "service": "sre-agent-drill",
            "node": "wave5-test-node",
        },
        "annotations": {
            "summary": "Wave5 controlled remediation drill",
            "description": "Controlled real-environment drill for approval and rollback evidence",
        },
        "starts_at": now,
        "fingerprint": f"wave5-drill-{uuid4().hex}",
        "status": "firing",
        "source": "wave5-drill",
    }

    exchanges: list[dict[str, Any]] = []
    with httpx.Client(base_url=settings.base_url, timeout=30.0, trust_env=False) as client:
        diagnose = _post_json(
            client,
            method="POST",
            path="/api/diagnose",
            headers=headers,
            payload=alert_payload,
            exchanges=exchanges,
        )
        if not diagnose.get("success"):
            raise RuntimeError(f"/api/diagnose failed: {diagnose}")
        diagnose_data = diagnose.get("data") or {}
        session_id = str(diagnose_data.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError(f"missing session_id in diagnose response: {diagnose}")

        approve = _post_json(
            client,
            method="POST",
            path=f"/api/remediate/{session_id}/approve",
            headers=headers,
            payload={"approved": True, "user": settings.user},
            exchanges=exchanges,
        )
        rollback = _post_json(
            client,
            method="POST",
            path=f"/api/remediate/{session_id}/rollback",
            headers=headers,
            payload=None,
            exchanges=exchanges,
        )

    ws_base = _ws_base_from_http(settings.base_url)
    capture = asyncio.run(
        _capture_ws_events(
            ws_base=ws_base,
            session_id=session_id,
            token=settings.token,
            last_event_id="0",
            max_events=settings.ws_max_events,
            timeout_seconds=settings.ws_timeout_seconds,
        )
    )
    events = capture.get("events", []) if isinstance(capture, dict) else []
    checkpoints: list[str] = []
    if events:
        event_ids = [
            str(item.get("data", {}).get("event_id"))
            for item in events
            if item.get("data", {}).get("event_id") is not None
        ]
        event_ids = [item for item in event_ids if item and item != "None"]
        if len(event_ids) > 1:
            step = max(1, (len(event_ids) - 1) // settings.reconnect_attempts)
            checkpoints = event_ids[:-1:step][: settings.reconnect_attempts]
    reconnect = asyncio.run(
        _run_reconnect_checks(
            ws_base=ws_base,
            session_id=session_id,
            token=settings.token,
            checkpoints=checkpoints,
        )
    )
    metrics = _compute_metrics(events, reconnect)

    api_summary = {
        "run_id": settings.run_id,
        "generated_at": _now_iso(),
        "base_url": settings.base_url,
        "session_id": session_id,
        "diagnose_success": bool(diagnose.get("success")),
        "approve_success": bool(approve.get("success")),
        "rollback_success": bool(rollback.get("success")),
        "capture": capture,
    }

    (run_dir / "api" / "drill_http_exchanges.json").write_text(
        json.dumps(exchanges, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "api" / "drill_summary.json").write_text(
        json.dumps(api_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "ws" / "thinking_trace_events.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "ws" / "reconnect_checks.json").write_text(
        json.dumps(reconnect, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics" / "wave5_drill_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
