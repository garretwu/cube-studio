from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any, AsyncIterator
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from sre_agent.agent.graph import build_default_llm_from_env
from sre_agent.tools import ToolExecutionContext, ToolRegistry, build_default_registry

MAX_HISTORY_MESSAGES = 100
MAX_TOOL_ROUNDS = 4
STREAM_CHUNK_SIZE = 120

CONVERSATION_SYSTEM_PROMPT = (
    "You are an SRE conversational assistant.\n"
    "Use read-only tools when they help answer with evidence.\n"
    "Never claim write actions are executed.\n"
    "Answer clearly and keep focus on operational debugging context."
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", "")).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(value or "").strip()


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return json.dumps({"value": str(value)}, ensure_ascii=False)


class ConversationalAgent:
    def __init__(
        self,
        *,
        llm: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_context: ToolExecutionContext | None = None,
        max_history_messages: int = MAX_HISTORY_MESSAGES,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        allowed_tool_names: list[str] | None = None,
    ) -> None:
        self._llm = llm
        self._tool_registry = tool_registry or build_default_registry()
        self._tool_context = tool_context or ToolExecutionContext()
        self._max_history_messages = max(2, int(max_history_messages))
        self._max_tool_rounds = max(1, int(max_tool_rounds))
        self._allowed_tool_names = list(allowed_tool_names) if allowed_tool_names else None
        self._history: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=self._max_history_messages)
        )
        self._lock = asyncio.Lock()

    async def __call__(self, message: str) -> str:
        return await self.chat(message, user_id="anonymous")

    async def chat(self, message: str, *, user_id: str = "anonymous") -> str:
        chunks: list[str] = []
        async for chunk in self.chat_stream(message, user_id=user_id):
            if chunk:
                chunks.append(chunk)
        return "".join(chunks).strip()

    async def chat_stream(self, message: str, *, user_id: str = "anonymous") -> AsyncIterator[str]:
        text = str(message or "").strip()
        if not text:
            yield "message is required"
            return

        reply = await self._generate_reply(text, user_id=user_id)
        if not reply:
            reply = "I do not have enough information yet. Please provide more context."
        for start in range(0, len(reply), STREAM_CHUNK_SIZE):
            yield reply[start : start + STREAM_CHUNK_SIZE]

    async def get_history(self, *, user_id: str = "anonymous") -> list[dict[str, Any]]:
        identity = user_id.strip() or "anonymous"
        async with self._lock:
            return [dict(item) for item in self._history[identity]]

    async def _generate_reply(self, message: str, *, user_id: str) -> str:
        identity = user_id.strip() or "anonymous"
        llm = await self._get_llm()
        if llm is None:
            await self._append_history(
                identity,
                {"id": f"user-{uuid4().hex}", "role": "user", "content": message, "created_at": _utc_now_iso()},
                {
                    "id": f"assistant-{uuid4().hex}",
                    "role": "assistant",
                    "content": "chat runtime is not available; please configure SRE_OPENAI_API_KEY.",
                    "created_at": _utc_now_iso(),
                },
            )
            return "chat runtime is not available; please configure SRE_OPENAI_API_KEY."

        async with self._lock:
            history = [dict(item) for item in self._history[identity]]

        model_messages: list[Any] = [SystemMessage(content=CONVERSATION_SYSTEM_PROMPT)]
        for item in history:
            role = str(item.get("role", "")).strip()
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if role == "assistant":
                model_messages.append(AIMessage(content=content))
            elif role == "user":
                model_messages.append(HumanMessage(content=content))
        model_messages.append(HumanMessage(content=message))

        tool_llm = llm.bind_tools(
            self._tool_registry.get_langchain_tools(tool_names=self._allowed_tool_names),
            tool_choice="auto",
        )

        tool_history_items: list[dict[str, Any]] = []
        final_reply = ""
        for _ in range(self._max_tool_rounds):
            response = await tool_llm.ainvoke(model_messages)
            if not isinstance(response, AIMessage):
                final_reply = _as_text(response)
                break

            tool_calls = list(response.tool_calls or [])
            content = _as_text(response.content)
            if not tool_calls:
                final_reply = content
                break

            model_messages.append(response)
            for call in tool_calls:
                tool_name = str(call.get("name", "")).strip() or "unknown_tool"
                tool_args = call.get("args")
                if not isinstance(tool_args, dict):
                    tool_args = {}
                result = await self._tool_registry.execute(tool_name, tool_args, self._tool_context)
                payload = {
                    "success": result.success,
                    "data": result.data,
                    "error": result.error,
                }
                tool_call_id = str(call.get("id", "")).strip() or uuid4().hex
                model_messages.append(
                    ToolMessage(
                        tool_call_id=tool_call_id,
                        name=tool_name,
                        content=_safe_json(payload),
                    )
                )
                tool_history_items.append(
                    {
                        "id": f"tool-{uuid4().hex}",
                        "role": "tool",
                        "content": _safe_json(payload),
                        "tool_name": tool_name,
                        "created_at": _utc_now_iso(),
                        "metadata": {"params": tool_args, "success": result.success},
                    }
                )
        else:
            final_reply = "I reached the tool-calling limit for this turn. Please narrow the question."

        if not final_reply:
            final_reply = "No response generated."

        await self._append_history(
            identity,
            {"id": f"user-{uuid4().hex}", "role": "user", "content": message, "created_at": _utc_now_iso()},
            *tool_history_items,
            {
                "id": f"assistant-{uuid4().hex}",
                "role": "assistant",
                "content": final_reply,
                "created_at": _utc_now_iso(),
            },
        )
        return final_reply

    async def _append_history(self, user_id: str, *items: dict[str, Any]) -> None:
        identity = user_id.strip() or "anonymous"
        async with self._lock:
            queue = self._history[identity]
            for item in items:
                queue.append(dict(item))

    async def _get_llm(self) -> Any | None:
        if self._llm is not None:
            return self._llm
        async with self._lock:
            if self._llm is not None:
                return self._llm
            try:
                value = build_default_llm_from_env()
                if inspect.isawaitable(value):
                    value = await value
                self._llm = value
            except Exception:  # noqa: BLE001
                self._llm = None
            return self._llm
