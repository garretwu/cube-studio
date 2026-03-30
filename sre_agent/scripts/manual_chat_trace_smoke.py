from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

import httpx
import websockets

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings


def _alert_payload() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "alert_name": "demo_latency_high",
        "severity": "critical",
        "labels": {"node": "node-a", "instance": "svc-a"},
        "annotations": {"summary": "manual smoke"},
        "starts_at": now,
        "fingerprint": "fp-manual-smoke-1",
        "status": "firing",
    }


async def run_smoke(base_url: str, token: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        print("\n[1/4] POST /api/diagnose")
        diagnose = await client.post("/api/diagnose", json=_alert_payload(), headers=headers)
        print(f"status={diagnose.status_code}")
        diagnose_payload = diagnose.json()
        print(json.dumps(diagnose_payload, ensure_ascii=False, indent=2))
        if diagnose.status_code != 200 or not diagnose_payload.get("success"):
            raise RuntimeError("diagnose failed, cannot continue trace test")
        session_id = diagnose_payload["data"]["session_id"]

        print("\n[2/4] WS /ws/chat")
        ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + f"/ws/chat?token={token}"
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"content": "manual smoke ping"}))
            frames = []
            while True:
                raw = await ws.recv()
                payload = json.loads(raw)
                frames.append(payload)
                if payload.get("type") == "done":
                    break
            print(json.dumps(frames, ensure_ascii=False, indent=2))

        print("\n[3/4] GET /api/chat/history")
        history = await client.get("/api/chat/history", headers=headers)
        print(f"status={history.status_code}")
        print(json.dumps(history.json(), ensure_ascii=False, indent=2))

        print(f"\n[4/4] GET /api/sessions/{session_id}/trace")
        trace = await client.get(f"/api/sessions/{session_id}/trace", headers=headers)
        print(f"status={trace.status_code}")
        print(json.dumps(trace.json(), ensure_ascii=False, indent=2))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual smoke test for chat and trace endpoints.")
    parser.add_argument("--base-url", default="http://127.0.0.1:18090")
    args = parser.parse_args()

    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u-manual", username="manual", role="operator"), settings)
    return asyncio.run(run_smoke(args.base_url, token))


if __name__ == "__main__":
    raise SystemExit(main())

