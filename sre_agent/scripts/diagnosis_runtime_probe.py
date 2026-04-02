#!/usr/bin/env python3
"""Probe diagnosis runtime chain in fixed order: chat -> sessions -> trace -> websocket."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

DEFAULT_RUNTIME_INFO = Path("sre_agent/temp/dev_runtime_info.json")


@dataclass
class ProbeContext:
    base_url: str
    token: str
    timeout_seconds: float


@dataclass
class ProbeOutcome:
    status: str
    detail: str | None = None
    payload: Any | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe diagnosis runtime health chain.")
    parser.add_argument("--runtime-info", default=str(DEFAULT_RUNTIME_INFO), help="Path to runtime info json.")
    parser.add_argument("--backend-url", default="", help="Backend base url override, e.g. http://127.0.0.1:8000")
    parser.add_argument("--token", default="", help="Bearer token override.")
    parser.add_argument("--session-id", default="", help="Session id override for trace/ws checks.")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request timeout seconds.")
    return parser.parse_args()


def _load_runtime_info(path: str) -> dict[str, Any]:
    runtime_path = Path(path)
    if not runtime_path.exists():
        return {}
    try:
        return json.loads(runtime_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_context(args: argparse.Namespace) -> ProbeContext:
    runtime_info = _load_runtime_info(args.runtime_info)
    base_url = str(args.backend_url or runtime_info.get("backend_url") or "http://127.0.0.1:8000").rstrip("/")
    token = str(args.token or runtime_info.get("frontend_bearer_token") or "").strip()
    if not token:
        raise SystemExit("missing bearer token: pass --token or ensure runtime info has frontend_bearer_token")
    return ProbeContext(base_url=base_url, token=token, timeout_seconds=max(1.0, float(args.timeout)))


def _request_json(ctx: ProbeContext, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> ProbeOutcome:
    url = f"{ctx.base_url}{path}"
    headers = {
        "Authorization": f"Bearer {ctx.token}",
        "x-trace-id": "diagnosis-runtime-probe",
    }
    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=body,
            timeout=ctx.timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome(status="REQUEST_FAILED", detail=str(exc))

    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return ProbeOutcome(status="INVALID_JSON", detail=f"status={response.status_code}", payload=response.text)

    if response.status_code >= 400:
        return ProbeOutcome(status="HTTP_ERROR", detail=f"status={response.status_code}", payload=payload)
    return ProbeOutcome(status="OK", payload=payload)


async def _ws_probe(ctx: ProbeContext, session_id: str) -> ProbeOutcome:
    try:
        import websockets
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome(status="WS_UNSTABLE", detail=f"websockets import failed: {exc}")

    ws_base = ctx.base_url.replace("http://", "ws://").replace("https://", "wss://")
    query = urlencode({"token": ctx.token})
    ws_url = f"{ws_base}/ws/thinking-trace/{session_id}?{query}"

    try:
        async with websockets.connect(ws_url, open_timeout=ctx.timeout_seconds, close_timeout=ctx.timeout_seconds) as websocket:
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=ctx.timeout_seconds)
                return ProbeOutcome(status="WS_READY", payload=message)
            except TimeoutError:
                return ProbeOutcome(status="WS_CONNECTED_IDLE", detail="connected but no event within timeout")
    except Exception as exc:  # noqa: BLE001
        return ProbeOutcome(status="WS_UNSTABLE", detail=str(exc))


def _safe_envelope_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "success" in payload and "data" in payload:
        return payload.get("data")
    return payload


def _extract_envelope_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    message = error.get("message")
    return str(message or "").strip()


def main() -> int:
    args = _parse_args()
    ctx = _build_context(args)

    report: dict[str, Any] = {
        "base_url": ctx.base_url,
        "checks": {},
        "conclusion": "UNKNOWN",
    }

    chat = _request_json(
        ctx,
        "POST",
        "/api/chat",
        body={"content": "diagnosis runtime probe", "session_id": args.session_id or None},
    )
    report["checks"]["chat"] = {"status": chat.status, "detail": chat.detail, "payload": chat.payload}
    if chat.status != "OK":
        error_text = _extract_envelope_error_message(chat.payload)
        lower_error = error_text.lower()
        if "chat runtime is not available" in lower_error or "openai_api_key" in lower_error or "null value for 'choices'" in lower_error:
            report["conclusion"] = "LLM_UNAVAILABLE"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        report["conclusion"] = "CHAT_API_ERROR"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    chat_data = _safe_envelope_data(chat.payload)
    reply = ""
    if isinstance(chat_data, dict):
        reply = str(chat_data.get("reply") or "")
    if "chat runtime is not available" in reply.lower():
        report["conclusion"] = "LLM_UNAVAILABLE"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    sessions = _request_json(ctx, "GET", "/api/sessions", params={"limit": 5})
    report["checks"]["sessions"] = {"status": sessions.status, "detail": sessions.detail, "payload": sessions.payload}
    if sessions.status != "OK":
        report["conclusion"] = "SESSIONS_API_ERROR"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    sessions_data = _safe_envelope_data(sessions.payload)
    if not isinstance(sessions_data, list) or not sessions_data:
        report["conclusion"] = "NO_SESSIONS"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3

    session_id = str(args.session_id or sessions_data[0].get("session_id") or "").strip()
    if not session_id:
        report["conclusion"] = "NO_SESSIONS"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 3

    report["selected_session_id"] = session_id

    trace = _request_json(ctx, "GET", f"/api/sessions/{session_id}/trace")
    report["checks"]["trace"] = {"status": trace.status, "detail": trace.detail, "payload": trace.payload}
    if trace.status != "OK":
        report["conclusion"] = "TRACE_API_ERROR"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    trace_data = _safe_envelope_data(trace.payload)
    if not isinstance(trace_data, list) or not trace_data:
        report["conclusion"] = "TRACE_EMPTY"
    else:
        report["conclusion"] = "TRACE_READY"

    ws = asyncio.run(_ws_probe(ctx, session_id))
    report["checks"]["ws"] = {"status": ws.status, "detail": ws.detail, "payload": ws.payload}
    if ws.status == "WS_UNSTABLE":
        if report["conclusion"] == "TRACE_READY":
            report["conclusion"] = "WS_UNSTABLE"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["conclusion"] == "TRACE_EMPTY":
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
