from __future__ import annotations

import asyncio
import os
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
    execute_selected_skill_node,
    finalize_node,
    initialize_state,
    load_and_select_skill_node,
    observe_node,
    reason_node,
    route_after_decide,
    route_after_reason,
    route_after_skill_selection,
)
from sre_agent.agent.state import SREAgentState
from sre_agent.models.events import EventType
from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry
from sre_agent.tools import ToolExecutionContext, ToolRegistry, build_default_registry


class PassthroughGuardrails:
    def wrap(self, llm: Any) -> Any:
        return llm


def build_default_llm_from_env() -> ChatOpenAI:
    api_key = (
        os.getenv("SRE_OPENAI_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    if not api_key:
        raise RuntimeError("SRE_OPENAI_API_KEY or OPENAI_API_KEY is required")
    base_url = os.getenv("SRE_OPENAI_BASE_URL", "").strip() or None
    model = os.getenv("SRE_LLM_MODEL", "").strip() or "MiniMax-M2.7"
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "temperature": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


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


def _normalize_trace_item_event(*, session_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
    item_type = str(item.get("type", "")).strip().lower()
    if item_type == "thought":
        action = str(item.get("action", "tool_call")).strip().lower()
        event_type = EventType.THINKING_STEP.value
        if action == "tool_call":
            event_type = EventType.TOOL_CALL.value
        payload = {
            "step": int(item.get("step", 1)),
            "timestamp": _to_iso_utc(item.get("timestamp")),
            "thought": str(item.get("content", "")).strip() or "diagnosis step",
            "action_type": action if action in {"tool_call", "conclude", "remediate"} else "tool_call",
            "tool_name": item.get("tool_name"),
            "tool_params": _to_dict(item.get("tool_params")),
            "confidence": item.get("confidence"),
        }
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
        await _safe_emit(
            trace_callback,
            {
                "type": EventType.DIAGNOSIS_RESULT.value,
                "session_id": session_id,
                "data": _to_dict(next_state.get("diagnosis_result")),
            },
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
    registry = skill_registry or SkillRegistry()
    policy = skill_policy or SkillPolicy()
    executor = skill_executor or SkillExecutor()
    tools = tool_registry or build_default_registry()

    async def _reason(state: SREAgentState) -> SREAgentState:
        try:
            next_state = await reason_node(
                state,
                llm=wrapped_llm,
                registry=tools,
                skill_registry=registry,
                skill_policy=policy,
            )
            await _emit_incremental_trace_events(
                trace_callback=trace_callback,
                previous_state=state,
                next_state=next_state,
            )
            return next_state
        except asyncio.TimeoutError:
            return {
                **state,
                "status": "timeout",
                "summary": "reason step timed out",
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
            next_state = await act_node(state, registry=tools, context=tool_context)
            await _emit_incremental_trace_events(
                trace_callback=trace_callback,
                previous_state=state,
                next_state=next_state,
            )
            return next_state
        except asyncio.TimeoutError:
            return {
                **state,
                "status": "timeout",
                "summary": "act step timed out",
                "error": "act step timed out",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **state,
                "status": "failed",
                "summary": f"act step failed: {exc}",
                "error": f"act step failed: {exc}",
            }

    async def _execute_selected_skill(state: SREAgentState) -> SREAgentState:
        return await execute_selected_skill_node(
            state,
            registry=registry,
            executor=executor,
            context=tool_context,
            tool_registry=tools,
        )

    async def _select_skill(state: SREAgentState) -> SREAgentState:
        try:
            next_state = await load_and_select_skill_node(
                state,
                llm=wrapped_llm,
                registry=registry,
                policy=policy,
                tool_registry=tools,
            )
            await _emit_incremental_trace_events(
                trace_callback=trace_callback,
                previous_state=state,
                next_state=next_state,
            )
            return next_state
        except asyncio.TimeoutError:
            return {
                **state,
                "status": "timeout",
                "summary": "skill selection step timed out",
                "error": "skill selection step timed out",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **state,
                "status": "failed",
                "summary": f"skill selection step failed: {exc}",
                "error": f"skill selection step failed: {exc}",
            }

    graph = StateGraph(SREAgentState)
    graph.add_node("load_and_select_skill", _select_skill)
    graph.add_node("reason", _reason)
    graph.add_node("act", _act)
    graph.add_node("observe", observe_node)
    graph.add_node("decide", decide_node)
    graph.add_node("execute_selected_skill", _execute_selected_skill)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "load_and_select_skill")
    graph.add_conditional_edges(
        "load_and_select_skill",
        route_after_skill_selection,
        {
            "execute_selected_skill": "execute_selected_skill",
            "reason": "reason",
            "finalize": "finalize",
        },
    )
    graph.add_conditional_edges(
        "reason",
        route_after_reason,
        {
            "execute_selected_skill": "execute_selected_skill",
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
    graph.add_edge("execute_selected_skill", "observe")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=create_checkpointer())


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
    step_timeout_sec: float = 60.0,
    total_timeout_sec: float = 600.0,
    max_steps: int = 50,
    reasoning_context_strategy: str = "state_rebuilt",
    reasoning_overflow_behavior: str = "fail",
    reasoning_input_target_tokens: int = 180000,
    reasoning_model_family: str | None = "MiniMax-M2.7",
    reason_context_char_budget: int = 2400,
    tool_message_char_limit: int = 1200,
    reason_preserve_recent_messages: int = 6,
    checkpoint_dir: str | None = "./data/checkpoints/sre_agent",
    allowed_tool_names: list[str] | None = None,
    trace_callback: TraceEventCallback | None = None,
) -> SREAgentState:
    active_session_id = session_id or uuid4().hex
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
        reasoning_context_strategy=reasoning_context_strategy,
        reasoning_overflow_behavior=reasoning_overflow_behavior,
        reasoning_input_target_tokens=reasoning_input_target_tokens,
        reasoning_model_family=reasoning_model_family,
        reason_context_char_budget=reason_context_char_budget,
        tool_message_char_limit=tool_message_char_limit,
        reason_preserve_recent_messages=reason_preserve_recent_messages,
        checkpoint_dir=checkpoint_dir,
        allowed_tool_names=allowed_tool_names,
        alert_snapshot=alert_snapshot,
        topology_context=topology_context,
        extra_alerts=extra_alerts,
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
                config={"configurable": {"thread_id": active_session_id}},
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
    {"load_and_select_skill", "reason", "act", "observe", "decide", "execute_selected_skill", "finalize"},
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
    step_timeout_sec: float = 60.0,
    total_timeout_sec: float = 600.0,
    max_steps: int = 50,
    reasoning_context_strategy: str = "state_rebuilt",
    reasoning_overflow_behavior: str = "fail",
    reasoning_input_target_tokens: int = 180000,
    reasoning_model_family: str | None = "MiniMax-M2.7",
    reason_context_char_budget: int = 2400,
    tool_message_char_limit: int = 1200,
    reason_preserve_recent_messages: int = 6,
    checkpoint_dir: str | None = "./data/checkpoints/sre_agent",
    allowed_tool_names: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream diagnosis events by iterating over ``graph.astream_events(version='v2')``.

    Yields dicts with ``{"type": ..., "session_id": ..., "data": ...}`` which the
    SSE endpoint can relay directly to the frontend.
    """
    active_session_id = session_id or uuid4().hex
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
        reasoning_context_strategy=reasoning_context_strategy,
        reasoning_overflow_behavior=reasoning_overflow_behavior,
        reasoning_input_target_tokens=reasoning_input_target_tokens,
        reasoning_model_family=reasoning_model_family,
        reason_context_char_budget=reason_context_char_budget,
        tool_message_char_limit=tool_message_char_limit,
        reason_preserve_recent_messages=reason_preserve_recent_messages,
        checkpoint_dir=checkpoint_dir,
        allowed_tool_names=allowed_tool_names,
        alert_snapshot=alert_snapshot,
        topology_context=topology_context,
        extra_alerts=extra_alerts,
    )

    yield {
        "type": EventType.DIAGNOSIS_STARTED.value,
        "session_id": active_session_id,
        "data": {
            "alert": alert_snapshot,
            "topology": topology_context,
            "variables": variables or {},
            "extra_alerts": extra_alerts or [],
        },
    }

    final_state: dict[str, Any] = dict(initial_state)

    async def _run() -> None:
        nonlocal final_state
        async for event in graph.astream_events(
            initial_state,
            config={"configurable": {"thread_id": active_session_id}},
            version="v2",
        ):
            kind = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data", {})

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")
                if chunk is None:
                    continue
                content = getattr(chunk, "content", None)
                if not content:
                    continue
                # Some providers return content as a list of parts.
                if isinstance(content, list):
                    text = "".join(
                        part if isinstance(part, str) else str(getattr(part, "text", ""))
                        for part in content
                    )
                else:
                    text = str(content)
                if not text:
                    continue
                yield {
                    "type": EventType.TOKEN_DELTA.value,
                    "session_id": active_session_id,
                    "data": {"content": text, "node": name},
                }

            elif kind == "on_chain_start" and name in _STREAM_GRAPH_NODES:
                yield {
                    "type": EventType.NODE_STARTED.value,
                    "session_id": active_session_id,
                    "data": {"node": name},
                }

            elif kind == "on_chain_end" and name in _STREAM_GRAPH_NODES:
                output = data.get("output")
                if isinstance(output, dict):
                    final_state = output
                event_data: dict[str, Any] = {"node": name}
                if isinstance(output, dict):
                    event_data["status"] = output.get("status")
                    event_data["step_count"] = output.get("step_count")
                    trace_items = output.get("trace_items") or []
                    if trace_items:
                        event_data["new_trace_items"] = trace_items[-1:]
                    if output.get("diagnosis_result") is not None:
                        event_data["diagnosis_result"] = output["diagnosis_result"]
                    if output.get("remediation_plan") is not None:
                        event_data["remediation_plan"] = output["remediation_plan"]
                yield {
                    "type": EventType.NODE_COMPLETED.value,
                    "session_id": active_session_id,
                    "data": event_data,
                }

            elif kind == "on_tool_start":
                yield {
                    "type": EventType.TOOL_STARTED.value,
                    "session_id": active_session_id,
                    "data": {"tool": name, "params": data.get("input", {})},
                }

            elif kind == "on_tool_end":
                yield {
                    "type": EventType.TOOL_COMPLETED.value,
                    "session_id": active_session_id,
                    "data": {"tool": name, "result": data.get("output")},
                }

    try:
        async with asyncio.timeout(total_timeout_sec):
            async for event in _run():
                yield event
    except TimeoutError:
        yield {
            "type": EventType.ERROR.value,
            "session_id": active_session_id,
            "data": {"message": "diagnosis session timed out"},
        }

    # Yield the final done event.
    final_status = final_state.get("status", "completed")
    final_summary = final_state.get("summary")
    yield {
        "type": "done",
        "session_id": active_session_id,
        "data": {"status": final_status, "summary": final_summary},
    }
