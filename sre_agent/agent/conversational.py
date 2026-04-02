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
RECENT_SESSION_HISTORY_LIMIT = 6

CONVERSATION_SYSTEM_PROMPT = (
    "You are an SRE conversational assistant.\n"
    "Use read-only tools when they help answer with evidence.\n"
    "Never claim write actions are executed.\n"
    "Keep answers anchored to the active diagnosis session context.\n"
    "Do not mix topics from other sessions.\n"
    "Always answer in this structure:\n"
    "1) 结论\n"
    "2) 关键证据\n"
    "3) 影响范围\n"
    "4) 建议下一步\n"
    "If user question is ambiguous, still provide a minimal conclusion first, then one clarification sentence."
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

    async def chat(
        self,
        message: str,
        *,
        user_id: str = "anonymous",
        session_id: str | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> str:
        chunks: list[str] = []
        async for chunk in self.chat_stream(
            message,
            user_id=user_id,
            session_id=session_id,
            session_context=session_context,
        ):
            if chunk:
                chunks.append(chunk)
        return "".join(chunks).strip()

    async def chat_stream(
        self,
        message: str,
        *,
        user_id: str = "anonymous",
        session_id: str | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        text = str(message or "").strip()
        if not text:
            yield "message is required"
            return

        reply = await self._generate_reply(
            text,
            user_id=user_id,
            session_id=session_id,
            session_context=session_context,
        )
        if not reply:
            reply = "I do not have enough information yet. Please provide more context."
        for start in range(0, len(reply), STREAM_CHUNK_SIZE):
            yield reply[start : start + STREAM_CHUNK_SIZE]

    async def get_history(self, *, user_id: str = "anonymous", session_id: str | None = None) -> list[dict[str, Any]]:
        identity = user_id.strip() or "anonymous"
        async with self._lock:
            history = [dict(item) for item in self._history[identity]]
        session = (session_id or "").strip()
        if not session:
            return history
        scoped = [item for item in history if isinstance(item.get("metadata"), dict) and item["metadata"].get("session_id") == session]
        if scoped:
            return scoped
        if any(isinstance(item.get("metadata"), dict) and item["metadata"].get("session_id") for item in history):
            return []
        return history

    async def _generate_reply(
        self,
        message: str,
        *,
        user_id: str,
        session_id: str | None = None,
        session_context: dict[str, Any] | None = None,
    ) -> str:
        identity = user_id.strip() or "anonymous"
        scoped_session_id = (session_id or "").strip()
        metadata = {"session_id": scoped_session_id} if scoped_session_id else None
        llm = await self._get_llm()
        if llm is None:
            await self._append_history(
                identity,
                {
                    "id": f"user-{uuid4().hex}",
                    "role": "user",
                    "content": message,
                    "created_at": _utc_now_iso(),
                    "metadata": metadata,
                },
                {
                    "id": f"assistant-{uuid4().hex}",
                    "role": "assistant",
                    "content": "chat runtime is not available; please configure SRE_OPENAI_API_KEY.",
                    "created_at": _utc_now_iso(),
                    "metadata": metadata,
                },
            )
            return "chat runtime is not available; please configure SRE_OPENAI_API_KEY."

        async with self._lock:
            history = [dict(item) for item in self._history[identity]]
        if scoped_session_id:
            scoped_history = [
                item
                for item in history
                if isinstance(item.get("metadata"), dict)
                and item["metadata"].get("session_id") == scoped_session_id
            ]
            if scoped_history:
                history = scoped_history
        if len(history) > RECENT_SESSION_HISTORY_LIMIT:
            history = history[-RECENT_SESSION_HISTORY_LIMIT:]

        system_prompt = CONVERSATION_SYSTEM_PROMPT
        context_text = ""
        if isinstance(session_context, dict):
            context_text = str(session_context.get("context_text", "")).strip()
        if context_text:
            system_prompt = (
                f"{CONVERSATION_SYSTEM_PROMPT}\n\n"
                "Diagnosis session context (trusted internal state):\n"
                f"{context_text}\n"
                "Use this context as the default factual baseline for follow-up answers."
            )

        model_messages: list[Any] = [SystemMessage(content=system_prompt)]
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

        try:
            tool_llm = llm.bind_tools(
                self._tool_registry.get_langchain_tools(tool_names=self._allowed_tool_names),
                tool_choice="auto",
            )
        except Exception:  # noqa: BLE001
            tool_llm = llm

        tool_history_items: list[dict[str, Any]] = []
        final_reply = ""
        for _ in range(self._max_tool_rounds):
            try:
                response = await tool_llm.ainvoke(model_messages)
            except Exception:  # noqa: BLE001
                try:
                    plain = await llm.ainvoke(model_messages)
                    final_reply = _as_text(plain)
                except Exception:  # noqa: BLE001
                    if context_text:
                        preview = context_text[:260].replace("\n", " ")
                        final_reply = (
                            "诊断会话上下文已加载，但当前 LLM 网关暂时不可用。"
                            f"上下文摘要：{preview}"
                        )
                    else:
                        final_reply = "当前 LLM 网关不可用，请稍后重试。"
                break

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
                        "metadata": {
                            "params": tool_args,
                            "success": result.success,
                            **({"session_id": scoped_session_id} if scoped_session_id else {}),
                        },
                    }
                )
        else:
            final_reply = "I reached the tool-calling limit for this turn. Please narrow the question."

        if not final_reply:
            final_reply = "No response generated."

        await self._append_history(
            identity,
            {
                "id": f"user-{uuid4().hex}",
                "role": "user",
                "content": message,
                "created_at": _utc_now_iso(),
                "metadata": metadata,
            },
            *tool_history_items,
            {
                "id": f"assistant-{uuid4().hex}",
                "role": "assistant",
                "content": final_reply,
                "created_at": _utc_now_iso(),
                "metadata": metadata,
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
