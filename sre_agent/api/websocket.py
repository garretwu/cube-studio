"""WebSocket routes for trace and alert streaming."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect

from sre_agent.auth.jwt import ws_authenticate


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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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
            except (TimeoutError, WebSocketDisconnect):
                break
            except Exception:  # noqa: BLE001
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
            except (TimeoutError, WebSocketDisconnect):
                break
            except Exception:  # noqa: BLE001
                break

    @router.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket) -> None:
        user = await ws_authenticate(websocket)
        await websocket.accept()

        services = websocket.app.state.services
        chat_handler = services.chat_handler
        if chat_handler is None:
            await websocket.send_json({"type": "error", "data": {"message": "chat handler is not configured"}})
            await websocket.close(code=1011, reason="chat handler is not configured")
            return

        while True:
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:  # noqa: BLE001
                await websocket.send_json({"type": "error", "data": {"message": "invalid payload"}})
                continue

            content = str(payload.get("content", "")).strip() if isinstance(payload, dict) else ""
            if not content:
                await websocket.send_json({"type": "error", "data": {"message": "content is required"}})
                continue

            stream_method = getattr(chat_handler, "chat_stream", None)
            if callable(stream_method):
                stream_value = _call_with_optional_user_id(stream_method, content, user_id=user.user_id)
                stream = await _maybe_await(stream_value)
                if hasattr(stream, "__aiter__"):
                    async for chunk in stream:
                        text = str(chunk)
                        if text:
                            await websocket.send_json({"type": "chunk", "data": {"content": text}})
                else:
                    text = str(stream)
                    if text:
                        await websocket.send_json({"type": "chunk", "data": {"content": text}})
            else:
                chat_method = getattr(chat_handler, "chat", None)
                callable_obj = chat_method if callable(chat_method) else chat_handler
                value = _call_with_optional_user_id(callable_obj, content, user_id=user.user_id)
                reply = await _maybe_await(value)
                await websocket.send_json({"type": "chunk", "data": {"content": str(reply)}})

            try:
                await websocket.send_json({"type": "done"})
            except WebSocketDisconnect:
                break

    return router
