"""WebSocket routes for trace and alert streaming."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket

from sre_agent.auth.jwt import ws_authenticate


def build_websocket_router() -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/thinking-trace/{session_id}")
    async def thinking_trace_ws(websocket: WebSocket, session_id: str) -> None:
        await ws_authenticate(websocket)
        await websocket.accept()
        publisher = websocket.app.state.services.trace_publisher
        last_id = websocket.query_params.get("last_event_id")
        async for event in publisher.subscribe(session_id, after=last_id):
            try:
                await asyncio.wait_for(websocket.send_json(event.model_dump(mode="json")), timeout=5.0)
            except TimeoutError:
                break

    @router.websocket("/ws/alerts")
    async def alerts_ws(websocket: WebSocket) -> None:
        await ws_authenticate(websocket)
        await websocket.accept()
        publisher = websocket.app.state.services.trace_publisher
        last_id = websocket.query_params.get("last_event_id")
        async for event in publisher.subscribe("alerts", after=last_id):
            try:
                await asyncio.wait_for(websocket.send_json(event.model_dump(mode="json")), timeout=5.0)
            except TimeoutError:
                break

    return router
