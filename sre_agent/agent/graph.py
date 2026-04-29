from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from sre_agent.agent.checkpoint import create_checkpointer
from sre_agent.agent.nodes import (
    act_node,
    decide_node,
    finalize_node,
    initialize_state,
    log_stream_lifecycle_event,
    observe_node,
    reason_node,
    route_after_decide,
    route_after_reason,
)
from sre_agent.agent.state import SREAgentState
from sre_agent.config import resolve_llm_runtime_settings
from sre_agent.models.diagnosis import DiagnosisResult
from sre_agent.models.events import EventType
from sre_agent.models.diagnosis_event_payloads import build_diagnosis_candidates_ready_payload
from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry
from sre_agent.tools import ToolExecutionContext, ToolRegistry, build_default_registry

LOGGER = logging.getLogger(__name__)


class PassthroughGuardrails:
    def wrap(self, llm: Any) -> Any:
        return llm


def _extract_error_code(exc: Exception) -> str | None:
    message = str(exc).strip()
    if not message:
        return None
    for pattern in (
        r'"code"\s*:\s*"?(?P<code>\d{3,4})"?',
        r"http_code'\s*:\s*'(?P<code>\d{3,4})'",
        r"status(?:\s+code)?\s*[:=]\s*(?P<code>\d{3})",
    ):
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return str(match.group("code")).strip()
    return None


def _is_retryable_llm_error(exc: Exception) -> bool:
    message = str(exc).strip().lower()
    code = _extract_error_code(exc)
    if code == "1305" or code == "429":
        return True
    if code is not None and code.isdigit() and 500 <= int(code) <= 599:
        return True
    signals = (
        "rate limit",
        "too many requests",
        "temporarily unavailable",
        "service unavailable",
        "访问量过大",
    )
    return any(signal in message for signal in signals)


_LAST_LLM_RUNTIME_DIAGNOSTICS: dict[str, Any] = {
    "last_error_code": None,
    "last_error_message": None,
    "retry_count": 0,
    "active_model": None,
    "fallback_used": False,
    "last_attempt_at": None,
}
_RUNTIME_DIAGNOSTIC_UNSET = object()


def _record_llm_runtime_diagnostics(
    *,
    last_error_code: str | None | object = _RUNTIME_DIAGNOSTIC_UNSET,
    last_error_message: str | None | object = _RUNTIME_DIAGNOSTIC_UNSET,
    retry_count: int | None = None,
    active_model: str | None = None,
    fallback_used: bool | None = None,
) -> None:
    if last_error_code is not _RUNTIME_DIAGNOSTIC_UNSET:
        _LAST_LLM_RUNTIME_DIAGNOSTICS["last_error_code"] = last_error_code
    if last_error_message is not _RUNTIME_DIAGNOSTIC_UNSET:
        _LAST_LLM_RUNTIME_DIAGNOSTICS["last_error_message"] = last_error_message
    if retry_count is not None:
        _LAST_LLM_RUNTIME_DIAGNOSTICS["retry_count"] = max(0, int(retry_count))
    if active_model is not None:
        _LAST_LLM_RUNTIME_DIAGNOSTICS["active_model"] = active_model
    if fallback_used is not None:
        _LAST_LLM_RUNTIME_DIAGNOSTICS["fallback_used"] = bool(fallback_used)
    _LAST_LLM_RUNTIME_DIAGNOSTICS["last_attempt_at"] = datetime.now(UTC).isoformat()


def get_last_llm_runtime_diagnostics() -> dict[str, Any]:
    return dict(_LAST_LLM_RUNTIME_DIAGNOSTICS)


class _BoundResilientLLM:
    def __init__(self, parent: "ResilientOpenAICompatibleLLM", tools: list[Any], tool_choice: str) -> None:
        self._parent = parent
        self._tools = tools
        self._tool_choice = tool_choice

    async def ainvoke(self, messages: list[Any]) -> Any:
        return await self._parent._ainvoke_with_resilience(  # noqa: SLF001
            messages,
            tools=self._tools,
            tool_choice=self._tool_choice,
        )


class ResilientOpenAICompatibleLLM:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        fallback_models: list[str] | None = None,
        max_retries_per_model: int = 2,
        retry_backoff_base_sec: float = 0.35,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._max_retries_per_model = max(0, int(max_retries_per_model))
        self._retry_backoff_base_sec = max(0.1, float(retry_backoff_base_sec))
        ordered_models = [model, *(fallback_models or [])]
        self._models: list[str] = []
        seen: set[str] = set()
        for candidate in ordered_models:
            normalized = str(candidate or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            self._models.append(normalized)
        if not self._models:
            raise RuntimeError("at least one LLM model is required")
        self._clients: dict[str, ChatOpenAI] = {}

    def _get_client(self, model: str) -> ChatOpenAI:
        client = self._clients.get(model)
        if client is not None:
            return client
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "model": model,
            "temperature": 0,
        }
        if self._base_url:
            kwargs["base_url"] = self._base_url
        client = ChatOpenAI(**kwargs)
        self._clients[model] = client
        return client

    async def _invoke_once(
        self,
        *,
        model: str,
        messages: list[Any],
        tools: list[Any] | None,
        tool_choice: str | None,
    ) -> Any:
        client = self._get_client(model)
        if tools is None:
            return await client.ainvoke(messages)
        bound = client.bind_tools(tools, tool_choice=tool_choice or "auto")
        return await bound.ainvoke(messages)

    async def _ainvoke_with_resilience(
        self,
        messages: list[Any],
        *,
        tools: list[Any] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        total_retries = 0
        for model_index, model in enumerate(self._models):
            fallback_used = model_index > 0
            for attempt in range(self._max_retries_per_model + 1):
                try:
                    response = await self._invoke_once(
                        model=model,
                        messages=messages,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                    _record_llm_runtime_diagnostics(
                        last_error_code=None,
                        retry_count=total_retries,
                        active_model=model,
                        fallback_used=fallback_used,
                    )
                    return response
                except Exception as exc:  # noqa: BLE001
                    retryable = _is_retryable_llm_error(exc)
                    code = _extract_error_code(exc)
                    _record_llm_runtime_diagnostics(
                        last_error_code=code,
                        last_error_message=str(exc).strip() or exc.__class__.__name__,
                        retry_count=total_retries,
                        active_model=model,
                        fallback_used=fallback_used,
                    )
                    if not retryable:
                        raise
                    if attempt < self._max_retries_per_model:
                        total_retries += 1
                        await asyncio.sleep(self._retry_backoff_base_sec * (2**attempt))
                        continue
                    break
        raise RuntimeError(
            "all configured LLM models failed after retry/fallback: "
            f"{', '.join(self._models)}"
        )

    async def ainvoke(self, messages: list[Any]) -> Any:
        return await self._ainvoke_with_resilience(messages)

    def bind_tools(self, tools: list[Any], tool_choice: str = "auto") -> _BoundResilientLLM:
        return _BoundResilientLLM(self, tools, tool_choice)


def build_default_llm_from_env() -> Any:
    settings = resolve_llm_runtime_settings()
    api_key = str(settings["api_key"] or "").strip()
    if not api_key:
        raise RuntimeError("SRE_OPENAI_API_KEY or OPENAI_API_KEY is required")
    model = str(settings["model"] or "").strip()
    base_url = str(settings["base_url"] or "").strip() or None
    fallback_models = [
        str(item).strip()
        for item in list(settings.get("fallback_models", []))
        if str(item).strip()
    ]
    return ResilientOpenAICompatibleLLM(
        api_key=api_key,
        model=model,
        base_url=base_url,
        fallback_models=fallback_models,
    )


TraceEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _to_iso_utc(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return datetime.now(UTC).isoformat()


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_stream_text_chunk(value: Any) -> str:
    if isinstance(value, list):
        return "".join(
            part if isinstance(part, str) else str(getattr(part, "text", ""))
            for part in value
        )
    if value is None:
        return ""
    return str(value)


def _extract_stream_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    content_text = _coerce_stream_text_chunk(getattr(chunk, "content", None))
    if content_text:
        return content_text

    reasoning_text = _coerce_stream_text_chunk(getattr(chunk, "reasoning_content", None))
    if reasoning_text:
        return reasoning_text

    additional_kwargs = _to_dict(getattr(chunk, "additional_kwargs", {}))
    fallback_reasoning = _coerce_stream_text_chunk(additional_kwargs.get("reasoning_content"))
    if fallback_reasoning:
        return fallback_reasoning
    return ""


def _parse_iso_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def _build_thought_key(run_id: str | None, node: str | None) -> str | None:
    normalized_run_id = (run_id or "").strip()
    normalized_node = (node or "").strip()
    if normalized_run_id and normalized_node:
        return f"{normalized_run_id}:{normalized_node}"
    return None


def _resolve_terminal_reason_from_status(
    status: Any,
    *,
    has_diagnosis_result: bool = False,
) -> str | None:
    normalized = str(status or "").strip().lower()
    if normalized in {"failed"}:
        return "error"
    if normalized in {"timeout", "step_timeout"}:
        return "timeout"
    if has_diagnosis_result or normalized in {"diagnosed"}:
        return "diagnosis_result"
    return None


def _normalize_stream_trace_items(
    *,
    session_id: str,
    trace_items: list[dict[str, Any]],
    thought_duration_sec: int | None = None,
    thought_key: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in trace_items:
        event = _normalize_trace_item_event(session_id=session_id, item=item, thought_key=thought_key)
        if not event:
            continue
        payload = _to_dict(event.get("data"))
        if event["type"] in {EventType.THINKING_STEP.value, EventType.TOOL_CALL.value}:
            if thought_duration_sec is not None:
                payload["thought_duration_sec"] = thought_duration_sec

        payload["event_type"] = event["type"]
        normalized.append(payload)
    return normalized


def _is_context_window_exceeded_error(exc: Exception) -> bool:
    message = str(exc).strip().lower()
    if not message:
        return False
    signals = (
        "context window exceeds limit",
        "maximum context length",
        "context length exceeded",
        "prompt is too long",
        "too many tokens",
        "token limit",
        "invalid params",
    )
    return any(signal in message for signal in signals) and (
        "context" in message or "token" in message or "prompt" in message
    )


def _normalize_trace_item_event(
    *,
    session_id: str,
    item: dict[str, Any],
    thought_key: str | None = None,
) -> dict[str, Any] | None:
    item_type = str(item.get("type", "")).strip().lower()
    if item_type == "thought":
        action = str(item.get("action", "tool_call")).strip().lower()
        event_type = EventType.THINKING_STEP.value
        if action == "tool_call":
            event_type = EventType.TOOL_CALL.value
        tool_params = _to_dict(item.get("tool_params"))
        thought = str(item.get("content", "")).strip() or "diagnosis step"
        next_action = item.get("next_action")
        payload = {
            "step": int(item.get("step", 1)),
            "timestamp": _to_iso_utc(item.get("timestamp")),
            "thought": thought,
            "action_type": action if action in {"tool_call", "conclude", "remediate"} else "tool_call",
            "thought_key": thought_key or item.get("thought_key"),
            "tool_name": item.get("tool_name"),
            "tool_params": tool_params,
            "confidence": item.get("confidence"),
        }
        if isinstance(next_action, str) and next_action.strip():
            payload["next_action"] = next_action.strip()
        return {
            "type": event_type,
            "session_id": session_id,
            "data": payload,
        }
    if item_type == "observation":
        payload = {
            "tool": str(item.get("tool", "")).strip() or "unknown",
            "params": _to_dict(item.get("params")),
            "result": _to_dict(item.get("result")),
            "timestamp": _to_iso_utc(item.get("timestamp")),
        }
        return {
            "type": EventType.TOOL_RESULT.value,
            "session_id": session_id,
            "data": payload,
        }
    return None


async def _safe_emit(trace_callback: TraceEventCallback | None, event: dict[str, Any] | None) -> None:
    if trace_callback is None or event is None:
        return
    try:
        await trace_callback(event)
    except Exception:  # noqa: BLE001
        # Streaming should not fail the diagnosis execution path.
        return


async def _emit_incremental_trace_events(
    *,
    trace_callback: TraceEventCallback | None,
    previous_state: SREAgentState,
    next_state: SREAgentState,
) -> None:
    if trace_callback is None:
        return
    session_id = str(next_state.get("session_id") or previous_state.get("session_id") or "").strip()
    if not session_id:
        return
    previous_trace = list(previous_state.get("trace_items", []))
    next_trace = list(next_state.get("trace_items", []))
    start_index = len(previous_trace)
    if start_index < 0 or start_index > len(next_trace):
        start_index = 0
    for item in next_trace[start_index:]:
        normalized = _normalize_trace_item_event(session_id=session_id, item=_to_dict(item))
        await _safe_emit(trace_callback, normalized)

    if previous_state.get("diagnosis_result") is None and next_state.get("diagnosis_result") is not None:
        diagnosis_result = _to_dict(next_state.get("diagnosis_result"))
        await _safe_emit(
            trace_callback,
            {
                "type": EventType.DIAGNOSIS_CANDIDATES_READY.value,
                "session_id": session_id,
                "data": build_diagnosis_candidates_ready_payload(DiagnosisResult.model_validate(diagnosis_result)),
            },
        )
        await _safe_emit(
            trace_callback,
            {
                "type": EventType.DIAGNOSIS_RESULT.value,
                "session_id": session_id,
                "data": diagnosis_result,
            },
        )


def _bind_skill_runtime_context(
    context: ToolExecutionContext | None,
    *,
    skill_registry: SkillRegistry,
    skill_policy: SkillPolicy,
    skill_executor: SkillExecutor,
    tool_registry: ToolRegistry,
) -> ToolExecutionContext:
    base = context or ToolExecutionContext()
    metadata = dict(base.metadata)
    metadata.setdefault("skill_registry", skill_registry)
    metadata.setdefault("skill_policy", skill_policy)
    metadata.setdefault("skill_executor", skill_executor)
    metadata.setdefault("tool_registry", tool_registry)
    return ToolExecutionContext(
        channels=dict(base.channels),
        write_approved=base.write_approved,
        approval_token=base.approval_token,
        metadata=metadata,
    )


def create_sre_graph(
    *,
    llm: Any | None = None,
    guardrails: Any | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_policy: SkillPolicy | None = None,
    skill_executor: SkillExecutor | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_context: ToolExecutionContext | None = None,
    trace_callback: TraceEventCallback | None = None,
) -> Any:
    runtime_llm = llm or build_default_llm_from_env()
    runtime_guardrails = guardrails or PassthroughGuardrails()
    wrapped_llm = runtime_guardrails.wrap(runtime_llm)
    tools = tool_registry or build_default_registry()
    registry = skill_registry or SkillRegistry()
    policy = skill_policy or SkillPolicy()
    executor = skill_executor or SkillExecutor()
    effective_context = _bind_skill_runtime_context(
        tool_context,
        skill_registry=registry,
        skill_policy=policy,
        skill_executor=executor,
        tool_registry=tools,
    )

    async def _reason(state: SREAgentState) -> SREAgentState:
        try:
            next_state = await reason_node(
                state,
                llm=wrapped_llm,
                registry=tools,
            )
            await _emit_incremental_trace_events(
                trace_callback=trace_callback,
                previous_state=state,
                next_state=next_state,
            )
            return next_state
        except asyncio.TimeoutError as exc:
            trace_items = list(state.get("trace_items", []))
            retry_record = _to_dict(getattr(exc, "reason_timeout_retry", {}))
            if retry_record:
                trace_items.append(
                    {
                        "type": "thought",
                        "step": state.get("step_count", 0) + 1,
                        "content": "reason timeout retry exhausted after compact final retry",
                        "action": "conclude",
                        "confidence": None,
                        "tool_params": {
                            "kind": "reason_timeout_retry",
                            **retry_record,
                        },
                    }
                )
            trace_items.append(
                {
                    "type": "thought",
                    "step": state.get("step_count", 0) + 1,
                    "content": "reason step timed out; concluding with evidence collected so far",
                    "action": "conclude",
                    "confidence": None,
                    "tool_params": {"kind": "reason_timeout"},
                }
            )
            return {
                **state,
                "trace_items": trace_items,
                "status": "step_timeout",
                "summary": "reason step timed out; partial diagnosis from collected evidence",
                "error": "reason step timed out",
            }
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc).strip() or exc.__class__.__name__
            if _is_context_window_exceeded_error(exc):
                trace_items = list(state.get("trace_items", []))
                trace_items.append(
                    {
                        "type": "thought",
                        "step": state.get("step_count", 0) + 1,
                        "content": (
                            "Reasoning failed because the model context window limit was reached; "
                            "try reducing prompt/tool output size."
                        ),
                        "action": "conclude",
                        "confidence": None,
                        "tool_params": {
                            "kind": "reason_context_overflow",
                            "error": error_text,
                        },
                    }
                )
                return {
                    **state,
                    "trace_items": trace_items,
                    "status": "failed",
                    "summary": f"reason step failed: context window limit reached: {error_text}",
                    "error": f"reason step failed: context window limit reached: {error_text}",
                }
            return {
                **state,
                "status": "failed",
                "summary": f"reason step failed: {error_text}",
                "error": f"reason step failed: {error_text}",
            }

    async def _act(state: SREAgentState) -> SREAgentState:
        try:
            next_state = await act_node(state, registry=tools, context=effective_context)
            await _emit_incremental_trace_events(
                trace_callback=trace_callback,
                previous_state=state,
                next_state=next_state,
            )
            return next_state
        except asyncio.TimeoutError:
            trace_items = list(state.get("trace_items", []))
            trace_items.append(
                {
                    "type": "thought",
                    "step": state.get("step_count", 0) + 1,
                    "content": "act step timed out; concluding with evidence collected so far",
                    "action": "conclude",
                    "confidence": None,
                    "tool_params": {"kind": "act_timeout"},
                }
            )
            return {
                **state,
                "trace_items": trace_items,
                "status": "step_timeout",
                "summary": "act step timed out; partial diagnosis from collected evidence",
                "error": "act step timed out",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **state,
                "status": "failed",
                "summary": f"act step failed: {exc}",
                "error": f"act step failed: {exc}",
            }

    graph = StateGraph(SREAgentState)
    graph.add_node("reason", _reason)
    graph.add_node("act", _act)
    graph.add_node("observe", observe_node)
    graph.add_node("decide", decide_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "reason")
    graph.add_conditional_edges(
        "reason",
        route_after_reason,
        {
            "act": "act",
            "finalize": "finalize",
        },
    )
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "decide")
    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "reason": "reason",
            "finalize": "finalize",
        },
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=create_checkpointer())


def _compute_graph_recursion_limit(max_steps: int) -> int:
    # LangGraph recursion_limit counts graph transitions (not business step_count).
    # One diagnosis "step" may traverse multiple graph nodes, so keep a safety multiplier.
    safe_steps = max(1, int(max_steps))
    return max(50, safe_steps * 6)


async def run_diagnosis(
    *,
    query: str,
    context: ToolExecutionContext | None,
    variables: dict[str, Any] | None = None,
    alert_snapshot: dict[str, Any] | None = None,
    topology_context: dict[str, Any] | None = None,
    extra_alerts: list[dict[str, Any]] | None = None,
    llm: Any | None = None,
    guardrails: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_policy: SkillPolicy | None = None,
    skill_executor: SkillExecutor | None = None,
    session_id: str | None = None,
    step_timeout_sec: float = 120.0,
    total_timeout_sec: float = 600.0,
    max_steps: int = 50,
    checkpoint_dir: str | None = "./data/checkpoints/sre_agent",
    allowed_tool_names: list[str] | None = None,
    trace_callback: TraceEventCallback | None = None,
    reasoning_context_strategy: str | None = None,
    reasoning_overflow_behavior: str | None = None,
    reasoning_input_target_tokens: int | None = None,
    reasoning_model_family: str | None = None,
) -> SREAgentState:
    active_session_id = session_id or uuid4().hex
    recursion_limit = _compute_graph_recursion_limit(max_steps)
    graph = create_sre_graph(
        llm=llm,
        guardrails=guardrails,
        skill_registry=skill_registry,
        skill_policy=skill_policy,
        skill_executor=skill_executor,
        tool_registry=tool_registry,
        tool_context=context,
        trace_callback=trace_callback,
    )
    initial_state = initialize_state(
        query=query,
        variables=variables,
        session_id=active_session_id,
        step_timeout_sec=step_timeout_sec,
        total_timeout_sec=total_timeout_sec,
        max_steps=max_steps,
        checkpoint_dir=checkpoint_dir,
        allowed_tool_names=allowed_tool_names,
        alert_snapshot=alert_snapshot,
        topology_context=topology_context,
        extra_alerts=extra_alerts,
        reasoning_context_strategy=reasoning_context_strategy,
        reasoning_overflow_behavior=reasoning_overflow_behavior,
        reasoning_input_target_tokens=reasoning_input_target_tokens,
        reasoning_model_family=reasoning_model_family,
    )

    # Emit diagnosis_started event so frontend can display alert/topology context immediately.
    if trace_callback is not None and (alert_snapshot is not None or topology_context is not None):
        await _safe_emit(
            trace_callback,
            {
                "type": EventType.DIAGNOSIS_STARTED.value,
                "session_id": active_session_id,
                "data": {
                    "alert": alert_snapshot,
                    "topology": topology_context,
                    "variables": variables or {},
                    "extra_alerts": extra_alerts or [],
                },
            },
        )

    try:
        result = await asyncio.wait_for(
            graph.ainvoke(
                initial_state,
                config={
                    "configurable": {"thread_id": active_session_id},
                    "recursion_limit": recursion_limit,
                },
            ),
            timeout=total_timeout_sec,
        )
    except asyncio.TimeoutError:
        return {
            **initial_state,
            "status": "timeout",
            "summary": "diagnosis session timed out",
            "error": "diagnosis session timed out",
        }
    return result


_STREAM_GRAPH_NODES = frozenset(
    {"reason", "act", "observe", "decide", "finalize"},
)


async def run_diagnosis_stream(
    *,
    query: str,
    context: ToolExecutionContext | None,
    variables: dict[str, Any] | None = None,
    alert_snapshot: dict[str, Any] | None = None,
    topology_context: dict[str, Any] | None = None,
    extra_alerts: list[dict[str, Any]] | None = None,
    llm: Any | None = None,
    guardrails: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_policy: SkillPolicy | None = None,
    skill_executor: SkillExecutor | None = None,
    session_id: str | None = None,
    step_timeout_sec: float = 120.0,
    total_timeout_sec: float = 600.0,
    max_steps: int = 50,
    checkpoint_dir: str | None = "./data/checkpoints/sre_agent",
    allowed_tool_names: list[str] | None = None,
    reasoning_context_strategy: str | None = None,
    reasoning_overflow_behavior: str | None = None,
    reasoning_input_target_tokens: int | None = None,
    reasoning_model_family: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream diagnosis events by iterating over ``graph.astream_events(version='v2')``.

    Yields dicts with ``{"type": ..., "session_id": ..., "data": ...}`` which the
    SSE endpoint can relay directly to the frontend.
    """
    active_session_id = session_id or uuid4().hex
    recursion_limit = _compute_graph_recursion_limit(max_steps)
    graph = create_sre_graph(
        llm=llm,
        guardrails=guardrails,
        skill_registry=skill_registry,
        skill_policy=skill_policy,
        skill_executor=skill_executor,
        tool_registry=tool_registry,
        tool_context=context,
        trace_callback=None,  # events come from astream_events, not trace_callback
    )
    initial_state = initialize_state(
        query=query,
        variables=variables,
        session_id=active_session_id,
        step_timeout_sec=step_timeout_sec,
        total_timeout_sec=total_timeout_sec,
        max_steps=max_steps,
        checkpoint_dir=checkpoint_dir,
        allowed_tool_names=allowed_tool_names,
        alert_snapshot=alert_snapshot,
        topology_context=topology_context,
        extra_alerts=extra_alerts,
        reasoning_context_strategy=reasoning_context_strategy,
        reasoning_overflow_behavior=reasoning_overflow_behavior,
        reasoning_input_target_tokens=reasoning_input_target_tokens,
        reasoning_model_family=reasoning_model_family,
    )
    log_stream_lifecycle_event(
        session_id=active_session_id,
        step=0,
        mode="stream_started",
        stage="run_diagnosis_stream",
        status="started",
        reason="stream initialized",
        step_count=int(initial_state.get("step_count", 0) or 0),
        max_steps=int(initial_state.get("max_steps", max_steps) or max_steps),
        pending_tool_calls_count=len(list(initial_state.get("pending_tool_calls", []) or [])),
        extra={
            "step_timeout_sec": float(step_timeout_sec),
            "total_timeout_sec": float(total_timeout_sec),
        },
    )

    yield {
        "type": EventType.DIAGNOSIS_STARTED.value,
        "session_id": active_session_id,
        "data": {
            "alert": alert_snapshot,
            "topology": topology_context,
            "variables": variables or {},
            "extra_alerts": extra_alerts or [],
            "bootstrap_state": "thinking",
        },
    }

    final_state: dict[str, Any] = dict(initial_state)

    async def _run() -> None:
        nonlocal final_state
        log_stream_lifecycle_event(
            session_id=active_session_id,
            step=int(final_state.get("step_count", 0) or 0) + 1,
            mode="stream_event",
            stage="_run",
            status="started",
            reason="enter event loop",
            step_count=int(final_state.get("step_count", 0) or 0),
            max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
            pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
            event_type="run_loop_start",
        )
        active_node_runs: dict[str, str] = {}
        node_started_at: dict[tuple[str, str], datetime] = {}
        last_trace_count = 0

        def _event_run_id(raw_event: dict[str, Any]) -> str:
            raw = raw_event.get("run_id")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            return ""

        def _event_metadata(raw_event: dict[str, Any]) -> dict[str, Any]:
            return _to_dict(raw_event.get("metadata"))

        def _event_node(raw_event: dict[str, Any], fallback: str = "") -> str:
            metadata = _event_metadata(raw_event)
            candidate = metadata.get("langgraph_node")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
            fallback_text = fallback.strip()
            if fallback_text:
                return fallback_text
            raw_name = raw_event.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                return raw_name.strip()
            return ""

        async for event in graph.astream_events(
            initial_state,
            config={
                "configurable": {"thread_id": active_session_id},
                "recursion_limit": recursion_limit,
            },
            version="v2",
        ):
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})
            if kind in {"on_chain_start", "on_chain_end", "on_tool_start", "on_tool_end"}:
                LOGGER.debug(
                    "stream event session=%s kind=%s name=%s",
                    active_session_id,
                    kind,
                    name,
                )
                log_stream_lifecycle_event(
                    session_id=active_session_id,
                    step=int(final_state.get("step_count", 0) or 0) + 1,
                    mode="stream_event",
                    stage="_run",
                    status="observed",
                    reason="graph event",
                    node=name if kind.startswith("on_chain_") else "",
                    event_type=kind,
                    pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
                    step_count=int(final_state.get("step_count", 0) or 0),
                    max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
                )

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                text = _extract_stream_text(chunk)
                if not text:
                    continue
                node_name = _event_node(event)
                active_run_id = active_node_runs.get(node_name, "")
                token_run_id = active_run_id or _event_run_id(event)
                thought_key = _build_thought_key(token_run_id, node_name)
                token_payload: dict[str, Any] = {"content": text}
                if node_name:
                    token_payload["node"] = node_name
                if token_run_id:
                    token_payload["run_id"] = token_run_id
                if thought_key:
                    token_payload["thought_key"] = thought_key
                yield {
                    "type": EventType.TOKEN_DELTA.value,
                    "session_id": active_session_id,
                    "data": token_payload,
                }

            elif kind == "on_chain_start" and name in _STREAM_GRAPH_NODES:
                run_id = _event_run_id(event)
                if not run_id:
                    run_id = uuid4().hex
                started_at = datetime.now(UTC)
                active_node_runs[name] = run_id
                node_started_at[(run_id, name)] = started_at
                thought_key = _build_thought_key(run_id, name)
                yield {
                    "type": EventType.NODE_STARTED.value,
                    "session_id": active_session_id,
                    "data": {
                        "node": name,
                        "run_id": run_id,
                        "thought_key": thought_key,
                        "started_at": started_at.isoformat(),
                        "display_mode": "thinking_only",
                    },
                }

            elif kind == "on_chain_end" and name in _STREAM_GRAPH_NODES:
                output = data.get("output")
                if isinstance(output, dict):
                    final_state = output
                run_id = _event_run_id(event) or active_node_runs.get(name, "")
                started_at: datetime | None = None
                if run_id:
                    started_at = node_started_at.pop((run_id, name), None)
                if started_at is None:
                    for key, value in list(node_started_at.items()):
                        if key[1] == name:
                            started_at = value
                            if not run_id:
                                run_id = key[0]
                            node_started_at.pop(key, None)
                            break
                completed_at = datetime.now(UTC)
                duration_sec: int | None = None
                if started_at is not None:
                    elapsed = (completed_at - started_at).total_seconds()
                    duration_sec = max(1, int(round(elapsed)))
                thought_key = _build_thought_key(run_id, name)

                event_data: dict[str, Any] = {
                    "node": name,
                    "completed_at": completed_at.isoformat(),
                }
                if run_id:
                    event_data["run_id"] = run_id
                if thought_key:
                    event_data["thought_key"] = thought_key
                if started_at is not None:
                    event_data["started_at"] = started_at.isoformat()
                if duration_sec is not None:
                    event_data["thought_duration_sec"] = duration_sec
                if isinstance(output, dict):
                    output_status = output.get("status")
                    event_data["status"] = output_status
                    event_data["step_count"] = output.get("step_count")
                    trace_items = output.get("trace_items") or []
                    if trace_items:
                        serialized_trace_items = [_to_dict(item) for item in trace_items if isinstance(item, dict)]
                        if len(serialized_trace_items) < last_trace_count:
                            last_trace_count = 0
                        trace_delta = serialized_trace_items[last_trace_count:]
                        last_trace_count = len(serialized_trace_items)
                        normalized_trace_items = _normalize_stream_trace_items(
                            session_id=active_session_id,
                            trace_items=trace_delta,
                            thought_duration_sec=duration_sec,
                            thought_key=thought_key,
                        )
                        if normalized_trace_items:
                            event_data["new_trace_items"] = normalized_trace_items
                    if output.get("diagnosis_result") is not None:
                        event_data["diagnosis_result"] = output["diagnosis_result"]
                    if output.get("remediation_plan") is not None:
                        event_data["remediation_plan"] = output["remediation_plan"]
                    terminal_reason = _resolve_terminal_reason_from_status(
                        output_status,
                        has_diagnosis_result=output.get("diagnosis_result") is not None,
                    )
                    if terminal_reason is not None:
                        event_data["terminal_reason"] = terminal_reason
                active_node_runs.pop(name, None)
                yield {
                    "type": EventType.NODE_COMPLETED.value,
                    "session_id": active_session_id,
                    "data": event_data,
                }

            elif kind == "on_tool_start":
                tool_data: dict[str, Any] = {"tool": name, "params": data.get("input", {})}
                run_id = _event_run_id(event)
                if run_id:
                    tool_data["run_id"] = run_id
                yield {
                    "type": EventType.TOOL_STARTED.value,
                    "session_id": active_session_id,
                    "data": tool_data,
                }

            elif kind == "on_tool_end":
                tool_data: dict[str, Any] = {"tool": name, "result": data.get("output")}
                run_id = _event_run_id(event)
                if run_id:
                    tool_data["run_id"] = run_id
                yield {
                    "type": EventType.TOOL_COMPLETED.value,
                    "session_id": active_session_id,
                    "data": tool_data,
                }
        log_stream_lifecycle_event(
            session_id=active_session_id,
            step=int(final_state.get("step_count", 0) or 0) + 1,
            mode="stream_event",
            stage="_run",
            status="completed",
            reason="event loop finished",
            step_count=int(final_state.get("step_count", 0) or 0),
            max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
            pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
            event_type="run_loop_end",
        )

    try:
        async with asyncio.timeout(total_timeout_sec):
            async for event in _run():
                yield event
    except TimeoutError:
        log_stream_lifecycle_event(
            session_id=active_session_id,
            step=int(final_state.get("step_count", 0) or 0) + 1,
            mode="stream_timeout",
            stage="run_diagnosis_stream",
            status="timeout",
            reason="total_timeout_exceeded",
            step_count=int(final_state.get("step_count", 0) or 0),
            max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
            pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
            event_type="timeout",
            extra={"total_timeout_sec": float(total_timeout_sec)},
        )
        yield {
            "type": EventType.ERROR.value,
            "session_id": active_session_id,
            "data": {
                "message": "diagnosis session timed out",
                "terminal_reason": "timeout",
            },
        }
    except asyncio.CancelledError:
        log_stream_lifecycle_event(
            session_id=active_session_id,
            step=int(final_state.get("step_count", 0) or 0) + 1,
            mode="stream_cancelled",
            stage="run_diagnosis_stream",
            status="cancelled",
            reason="stream_task_cancelled",
            step_count=int(final_state.get("step_count", 0) or 0),
            max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
            pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
            event_type="cancelled",
        )
        raise
    except Exception as exc:  # noqa: BLE001
        log_stream_lifecycle_event(
            session_id=active_session_id,
            step=int(final_state.get("step_count", 0) or 0) + 1,
            mode="stream_exception",
            stage="run_diagnosis_stream",
            status="failed",
            reason=str(exc).strip() or exc.__class__.__name__,
            step_count=int(final_state.get("step_count", 0) or 0),
            max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
            pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
            event_type="exception",
            extra={"exception_type": exc.__class__.__name__},
        )
        raise

    # Yield the final done event.
    final_status = final_state.get("status", "completed")
    final_summary = final_state.get("summary")
    final_terminal_reason = _resolve_terminal_reason_from_status(
        final_status,
        has_diagnosis_result=final_state.get("diagnosis_result") is not None,
    ) or "done"
    log_stream_lifecycle_event(
        session_id=active_session_id,
        step=int(final_state.get("step_count", 0) or 0) + 1,
        mode="stream_done_emitted",
        stage="run_diagnosis_stream",
        status=str(final_status or "").strip() or "completed",
        reason=str(final_terminal_reason or "").strip() or "done",
        step_count=int(final_state.get("step_count", 0) or 0),
        max_steps=int(final_state.get("max_steps", max_steps) or max_steps),
        pending_tool_calls_count=len(list(final_state.get("pending_tool_calls", []) or [])),
        event_type="done",
    )
    yield {
        "type": "done",
        "session_id": active_session_id,
        "data": {
            "status": final_status,
            "summary": final_summary,
            "terminal_reason": final_terminal_reason,
        },
    }
