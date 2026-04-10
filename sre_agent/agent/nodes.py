from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, Field

from sre_agent.agent.checkpoint import persist_state_snapshot
from sre_agent.agent.prompts import build_system_prompt
from sre_agent.agent.state import SREAgentState
from sre_agent.models.diagnosis import DiagnosisResult, Observation, ThinkingStep, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
from sre_agent.runtime.token_estimation import estimate_token_count
from sre_agent.tools import ToolExecutionContext, ToolRegistry, ToolResult

# LLM 交互日志记录器
_llm_logger = logging.getLogger("sre_agent.llm")
_llm_logger.setLevel(logging.DEBUG)
_llm_logger.addHandler(logging.NullHandler())  # 默认空 handler，避免警告

LLM_LOG_DIR = Path("./data/llm_logs")


class FinalDiagnosisEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any]
    remediation_plan: dict[str, Any] | None = None


class ReasoningEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any] | None = None
    remediation_plan: dict[str, Any] | None = None


def initialize_state(
    *,
    query: str,
    variables: dict[str, Any] | None = None,
    session_id: str,
    step_timeout_sec: float,
    total_timeout_sec: float,
    max_steps: int,
    checkpoint_dir: str | None = None,
    allowed_tool_names: list[str] | None = None,
    alert_snapshot: dict[str, Any] | None = None,
    topology_context: dict[str, Any] | None = None,
    extra_alerts: list[dict[str, Any]] | None = None,
) -> SREAgentState:
    return {
        "query": query,
        "variables": variables or {},
        "session_id": session_id,
        "messages": [],
        "llm_interactions": [],
        "trace_items": [],
        "pending_tool_calls": [],
        "tool_runs": [],
        "skill_runs": [],
        "step_count": 0,
        "max_steps": max_steps,
        "step_timeout_sec": step_timeout_sec,
        "total_timeout_sec": total_timeout_sec,
        "diagnosis_result": None,
        "remediation_plan": None,
        "status": None,
        "summary": None,
        "error": None,
        "checkpoint_dir": checkpoint_dir,
        "allowed_tool_names": allowed_tool_names,
        "alert_snapshot": alert_snapshot,
        "topology_context": topology_context,
        "extra_alerts": extra_alerts,
    }


def _log_tool_execution(
    *,
    session_id: str,
    step: int,
    tool_name: str,
    tool_args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """将工具执行结果写入日志文件。

    日志格式: JSONL (每行一个 JSON 对象)
    日志路径: ./data/llm_logs/{session_id}.jsonl
    """
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LLM_LOG_DIR / f"{session_id}.jsonl"

        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "step": step,
            "mode": "tool_execution",
            "tool_name": tool_name,
            "tool_args": tool_args,
            "result": result,
        }

        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        _llm_logger.debug("Tool execution logged to %s", log_file)
    except Exception:
        _llm_logger.exception("Failed to log tool execution")


def _log_llm_interaction(
    *,
    session_id: str,
    step: int,
    prompt_messages: list[Any],
    response: AIMessage | None,
    mode: str,
    tool_choice: str,
    tool_calls: list[dict[str, Any]],
    prompt_message_stats: list[dict[str, Any]] | None = None,
    prompt_fallback_used: bool = False,
    prompt_metadata: dict[str, Any] | None = None,
) -> None:
    """将 LLM 交互日志写入文件，便于调试和分析。

    日志格式: JSONL (每行一个 JSON 对象)
    日志路径: ./data/llm_logs/{session_id}.jsonl
    """
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LLM_LOG_DIR / f"{session_id}.jsonl"

        # 构建日志内容
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "step": step,
            "mode": mode,
            "tool_choice": tool_choice,
            "prompt": {
                "messages": messages_to_dict(prompt_messages),
                "message_count": len(prompt_messages),
                "stats": prompt_message_stats or [],
                "fallback_used": bool(prompt_fallback_used),
                "metadata": prompt_metadata or {},
            },
            "response": {
                "content": _extract_text(response.content) if isinstance(response, AIMessage) else "",
                "tool_calls": tool_calls,
                "response_metadata": getattr(response, "response_metadata", {}) if isinstance(response, AIMessage) else {},
            },
        }

        # 追加写入 JSONL 文件
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        _llm_logger.debug("LLM interaction logged to %s", log_file)
    except Exception:  # noqa: BLE001
        # 日志记录失败不应影响诊断流程
        _llm_logger.exception("Failed to log LLM interaction")


async def reason_node(
    state: SREAgentState,
    *,
    llm: Any,
    registry: ToolRegistry,
) -> SREAgentState:
    if state.get("step_count", 0) >= state.get("max_steps", 10):
        updated = {
            **state,
            "status": "timeout",
            "summary": "max diagnosis steps reached",
            "error": "max diagnosis steps reached",
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason-timeout", updated)
        return updated

    system_prompt = build_system_prompt(
        registry,
        allowed_tool_names=state.get("allowed_tool_names"),
    )
    messages = list(_coerce_messages(state.get("messages", [])))
    if not messages:
        messages = [HumanMessage(content=state["query"])]

    invoked_messages: list[Any] = []
    prompt_metadata: dict[str, Any] = {}
    prompt_fallback_used = False
    bound_tool_names: list[str] = []
    tool_choice = "auto"
    final_turn = bool(state.get("tool_runs")) and state.get("step_count", 0) >= max(state.get("max_steps", 10) - 1, 1)
    interaction_mode = "final_json" if final_turn else "tool_bound"
    if final_turn and hasattr(llm, "ainvoke"):
        invoked_messages, prompt_metadata, overflow_failed = _prepare_reason_prompt_messages_for_invocation(
            system_prompt=system_prompt,
            state=state,
            final_turn=True,
            tool_binding_fallback=False,
        )
        prompt_fallback_used = bool(prompt_metadata.get("prompt_fallback_used"))
        if overflow_failed:
            return _build_reason_overflow_failure_state(
                state,
                prompt_messages=invoked_messages,
                prompt_metadata=prompt_metadata,
                interaction_mode=interaction_mode,
                tool_choice="none",
            )
        tool_choice = "none"
        response = await asyncio.wait_for(llm.ainvoke(invoked_messages), timeout=state["step_timeout_sec"])
    else:
        invoked_messages, prompt_metadata, overflow_failed = _prepare_reason_prompt_messages_for_invocation(
            system_prompt=system_prompt,
            state=state,
            final_turn=False,
            tool_binding_fallback=False,
        )
        prompt_fallback_used = bool(prompt_metadata.get("prompt_fallback_used"))
        if overflow_failed:
            return _build_reason_overflow_failure_state(
                state,
                prompt_messages=invoked_messages,
                prompt_metadata=prompt_metadata,
                interaction_mode=interaction_mode,
                tool_choice=tool_choice,
            )
        bound_tools = registry.get_langchain_tools(
            tool_names=state.get("allowed_tool_names"),
        )
        bound_tool_names = [str(getattr(tool, "name", "")) for tool in bound_tools]
        tool_choice = "required" if not state.get("tool_runs") else "auto"
        try:
            call_model = llm.bind_tools(
                bound_tools,
                tool_choice=tool_choice,
            )
            response = await asyncio.wait_for(call_model.ainvoke(invoked_messages), timeout=state["step_timeout_sec"])
        except Exception as exc:  # noqa: BLE001
            if hasattr(llm, "ainvoke") and _is_reason_prompt_empty_error(exc):
                invoked_messages, prompt_metadata, overflow_failed = _prepare_reason_prompt_messages_for_invocation(
                    system_prompt=system_prompt,
                    state=state,
                    final_turn=False,
                    tool_binding_fallback=False,
                )
                prompt_metadata = {
                    **prompt_metadata,
                    "retry_after_empty_messages_error": True,
                    "retry_error": _normalized_exception_message(exc),
                }
                prompt_fallback_used = bool(prompt_metadata.get("prompt_fallback_used"))
                if overflow_failed:
                    return _build_reason_overflow_failure_state(
                        state,
                        prompt_messages=invoked_messages,
                        prompt_metadata=prompt_metadata,
                        interaction_mode=interaction_mode,
                        tool_choice=tool_choice,
                    )
                try:
                    call_model = llm.bind_tools(
                        bound_tools,
                        tool_choice=tool_choice,
                    )
                    response = await asyncio.wait_for(call_model.ainvoke(invoked_messages), timeout=state["step_timeout_sec"])
                except Exception as retry_exc:  # noqa: BLE001
                    exc = retry_exc
            if not hasattr(llm, "ainvoke") or not (
                _is_tool_binding_incompatible_error(exc) or _is_reason_prompt_empty_error(exc)
            ):
                raise
            # Provider compatibility fallback: continue diagnosis without tool-binding,
            # otherwise the whole session fails before any trace is generated.
            interaction_mode = "tool_binding_fallback"
            tool_choice = "none"
            invoked_messages, prompt_metadata, overflow_failed = _prepare_reason_prompt_messages_for_invocation(
                system_prompt=system_prompt,
                state=state,
                final_turn=False,
                tool_binding_fallback=True,
            )
            prompt_fallback_used = bool(prompt_metadata.get("prompt_fallback_used"))
            if overflow_failed:
                return _build_reason_overflow_failure_state(
                    state,
                    prompt_messages=invoked_messages,
                    prompt_metadata=prompt_metadata,
                    interaction_mode=interaction_mode,
                    tool_choice=tool_choice,
                )
            response = await asyncio.wait_for(llm.ainvoke(invoked_messages), timeout=state["step_timeout_sec"])
    if not isinstance(response, AIMessage):
        raise RuntimeError(f"expected AIMessage from LLM, got {type(response).__name__}")

    updated_messages = [*messages, response]
    updated_trace = list(state.get("trace_items", []))
    updated_interactions = list(state.get("llm_interactions", []))
    pending_tool_calls = list(response.tool_calls or [])
    raw_response_text = _extract_text(response.content)
    step_index = state.get("step_count", 0) + 1

    # 记录 LLM 交互到日志文件
    _log_llm_interaction(
        session_id=str(state.get("session_id", "unknown")),
        step=step_index,
        prompt_messages=list(invoked_messages),
        response=response,
        mode=interaction_mode,
        tool_choice=tool_choice,
        tool_calls=list(pending_tool_calls),
        prompt_message_stats=_build_prompt_message_stats(list(invoked_messages)),
        prompt_fallback_used=prompt_fallback_used,
        prompt_metadata=prompt_metadata,
    )

    updated_interactions.append(
        {
            "step": step_index,
            "mode": interaction_mode,
            "tool_choice": tool_choice,
            "bound_tool_names": [name for name in bound_tool_names if name],
            "prompt_messages": messages_to_dict(invoked_messages),
            "prompt_message_stats": _build_prompt_message_stats(list(invoked_messages)),
            "prompt_fallback_used": bool(prompt_fallback_used),
            "prompt_metadata": dict(prompt_metadata),
            "response_message": messages_to_dict([response])[0],
            "raw_response_text": raw_response_text,
            "tool_calls": pending_tool_calls,
        }
    )

    if pending_tool_calls:
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": raw_response_text or "正在调用只读工具补充诊断证据。",
                "action": "tool_call",
                "tool_name": pending_tool_calls[0]["name"],
                "tool_params": pending_tool_calls[0].get("args", {}),
                "confidence": None,
            }
        )
        updated = {
            **state,
            "messages": updated_messages,
            "llm_interactions": updated_interactions,
            "trace_items": updated_trace,
            "pending_tool_calls": pending_tool_calls,
            "step_count": step_index,
            "status": "running",
            "summary": None,
            "error": None,
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
        return updated

    try:
        parsed = _parse_reasoning_output(raw_response_text)
    except Exception:  # noqa: BLE001
        parsed = _build_fallback_reasoning_output(
            query=str(state.get("query", "")).strip(),
            content=raw_response_text,
        )
    if parsed.diagnosis is None:
        parsed = _build_fallback_reasoning_output(
            query=str(state.get("query", "")).strip(),
            content=raw_response_text,
        )
        if parsed.diagnosis is None:
            parsed = ReasoningEnvelope(
                thought="已将非 JSON 模型输出转换为结构化的低置信度诊断结果。",
                diagnosis=_build_fallback_final_output(
                    query=str(state.get("query", "")).strip(),
                    content=raw_response_text,
                ).diagnosis,
                remediation_plan=None,
            )
    diagnosis = DiagnosisResult.model_validate(_normalize_diagnosis_payload(parsed.diagnosis))
    remediation_plan = _normalize_remediation_plan_payload(
        raw_plan=parsed.remediation_plan,
        diagnosis=diagnosis,
        session_id=str(state.get("session_id", "")),
        registry=registry,
    )
    if remediation_plan is not None:
        diagnosis = diagnosis.model_copy(update={"recommended_fix": remediation_plan})
    updated_trace.append(
        {
            "type": "thought",
            "step": step_index,
            "content": parsed.thought,
            "action": "conclude" if remediation_plan is None else "remediate",
            "confidence": diagnosis.confidence,
        }
    )
    updated = {
        **state,
        "messages": updated_messages,
        "llm_interactions": updated_interactions,
        "trace_items": updated_trace,
        "pending_tool_calls": [],
        "step_count": step_index,
        "diagnosis_result": diagnosis.model_dump(mode="json"),
        "remediation_plan": None if remediation_plan is None else remediation_plan.model_dump(mode="json"),
        "status": "diagnosed",
        "summary": diagnosis.impact_summary,
        "error": None,
    }
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
    return updated


def _is_tool_binding_incompatible_error(exc: Exception) -> bool:
    message = _normalized_exception_message(exc).lower()
    signals = (
        "null value for 'choices'",
        "response with null value for 'choices'",
        "tool_calls",
        "tool binding",
        "function call",
    )
    return any(signal in message for signal in signals)


def _is_reason_prompt_empty_error(exc: Exception) -> bool:
    message = _normalized_exception_message(exc).lower()
    signals = (
        "messages is empty",
        "invalid params, messages is empty",
        "(2013)",
    )
    return any(signal in message for signal in signals)


def _normalized_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _normalize_positive_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except Exception:  # noqa: BLE001
        parsed = default
    if parsed < minimum:
        return minimum
    return parsed


def _is_minimax_m27_family(model_family: str | None) -> bool:
    normalized = str(model_family or "").strip().lower()
    return normalized == "minimax-m2.7" or normalized.endswith("/minimax-m2.7")


def _normalize_reasoning_context_strategy(value: Any, *, model_family: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"state_rebuilt", "transcript_compact"}:
        return normalized
    return "state_rebuilt" if _is_minimax_m27_family(model_family) else "transcript_compact"


def _normalize_reasoning_overflow_behavior(value: Any, *, model_family: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"fail", "compact"}:
        return normalized
    return "fail" if _is_minimax_m27_family(model_family) else "compact"


def _normalize_reasoning_input_target_tokens(value: Any, *, model_family: str | None) -> int:
    default = 180000 if _is_minimax_m27_family(model_family) else 32000
    return _normalize_positive_int(value, default=default, minimum=1024)


def _estimate_message_chars(message: Any) -> int:
    base = _extract_text(getattr(message, "content", ""))
    if isinstance(message, ToolMessage):
        base = f"{getattr(message, 'name', '')}:{base}"
    return len(base)


def _truncate_text_end(text: str, *, max_chars: int, marker: str = "\n...[truncated]...") -> str:
    normalized = str(text or "")
    if max_chars <= 0:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= len(marker) + 8:
        return normalized[:max_chars]
    return f"{normalized[: max_chars - len(marker)]}{marker}"


def _truncate_atomic_message_text(text: str, *, max_chars: int) -> str:
    normalized = str(text or "")
    if len(normalized) <= max_chars:
        return normalized
    lines = normalized.splitlines()
    preserved_lines: list[str] = []
    remaining_budget = max_chars
    for line in lines[:2]:
        if not line:
            continue
        line_cost = len(line) + (1 if preserved_lines else 0)
        if line_cost > remaining_budget:
            break
        preserved_lines.append(line)
        remaining_budget -= line_cost
    if not preserved_lines:
        return _truncate_text_end(normalized, max_chars=max_chars)
    preserved_text = "\n".join(preserved_lines)
    if len(preserved_lines) == len(lines):
        return preserved_text
    if remaining_budget <= 0:
        return preserved_text
    remainder = "\n".join(lines[len(preserved_lines):]).strip()
    if not remainder:
        return preserved_text
    compact_remainder = _truncate_text_end(remainder, max_chars=remaining_budget)
    return f"{preserved_text}\n{compact_remainder}" if compact_remainder else preserved_text


def _truncate_text_middle(text: str, *, max_chars: int, marker: str = "\n...[truncated]...\n") -> str:
    normalized = str(text or "")
    if max_chars <= 0:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= len(marker) + 8:
        return normalized[:max_chars]
    remain = max_chars - len(marker)
    head = remain // 2
    tail = remain - head
    return f"{normalized[:head]}{marker}{normalized[-tail:]}"


def _copy_human_message(message: HumanMessage, *, content: str) -> HumanMessage:
    kwargs: dict[str, Any] = {}
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    if additional_kwargs:
        kwargs["additional_kwargs"] = additional_kwargs
    name = getattr(message, "name", None)
    if name:
        kwargs["name"] = str(name)
    return HumanMessage(content=content, **kwargs)


def _copy_system_message(message: SystemMessage, *, content: str) -> SystemMessage:
    name = getattr(message, "name", None)
    if name:
        return SystemMessage(content=content, name=str(name))
    return SystemMessage(content=content)


def _is_atomic_human_message(message: Any) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    return str(additional_kwargs.get("evidence_kind", "")).strip() in {"skill_summary", "fallback_context"}


def _minimum_message_chars(message: Any, *, default: int) -> int:
    if _is_atomic_human_message(message):
        first_line = _extract_text(getattr(message, "content", "")).partition("\n")[0].strip()
        if first_line:
            return max(default, len(first_line))
    return default


def _truncate_message_to_chars(message: Any, *, max_chars: int) -> Any:
    if max_chars <= 0:
        return message
    content = _extract_text(getattr(message, "content", ""))
    truncated = _truncate_atomic_message_text(content, max_chars=max_chars) if _is_atomic_human_message(message) else _truncate_text_middle(content, max_chars=max_chars)
    if isinstance(message, SystemMessage):
        return _copy_system_message(message, content=truncated)
    if isinstance(message, HumanMessage):
        return _copy_human_message(message, content=truncated)
    if isinstance(message, ToolMessage):
        return ToolMessage(
            tool_call_id=str(getattr(message, "tool_call_id", "")),
            content=truncated,
            name=str(getattr(message, "name", "") or ""),
        )
    if isinstance(message, AIMessage):
        return AIMessage(content=truncated, tool_calls=list(getattr(message, "tool_calls", []) or []))
    return message


def _collect_ai_tool_call_ids(message: Any) -> set[str]:
    if not isinstance(message, AIMessage):
        return set()
    tool_calls = getattr(message, "tool_calls", []) or []
    ids: set[str] = set()
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("id", "")).strip()
        if call_id:
            ids.add(call_id)
    return ids


def _drop_orphan_tool_messages(messages: list[Any]) -> list[Any]:
    if not messages:
        return []
    available_tool_call_ids: set[str] = set()
    filtered: list[Any] = []
    for message in messages:
        if isinstance(message, ToolMessage):
            tool_call_id = str(getattr(message, "tool_call_id", "")).strip()
            if not tool_call_id or tool_call_id not in available_tool_call_ids:
                continue
            filtered.append(message)
            continue
        filtered.append(message)
        available_tool_call_ids.update(_collect_ai_tool_call_ids(message))
    return filtered


def _group_message_bundles(messages: list[Any]) -> list[list[Any]]:
    bundles: list[list[Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if isinstance(message, AIMessage):
            tool_call_ids = _collect_ai_tool_call_ids(message)
            if tool_call_ids:
                bundle = [message]
                index += 1
                while index < len(messages):
                    candidate = messages[index]
                    if not isinstance(candidate, ToolMessage):
                        break
                    tool_call_id = str(getattr(candidate, "tool_call_id", "")).strip()
                    if not tool_call_id or tool_call_id not in tool_call_ids:
                        break
                    bundle.append(candidate)
                    index += 1
                bundles.append(bundle)
                continue
        bundles.append([message])
        index += 1
    return bundles


def _flatten_message_bundles(bundles: list[list[Any]]) -> list[Any]:
    flattened: list[Any] = []
    for bundle in bundles:
        flattened.extend(bundle)
    return flattened


def _build_prompt_message_stats(messages: list[Any]) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    for message in messages:
        role = "unknown"
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, HumanMessage):
            role = "human"
        elif isinstance(message, AIMessage):
            role = "assistant"
        elif isinstance(message, ToolMessage):
            role = "tool"
        stats.append(
            {
                "role": role,
                "chars": _estimate_message_chars(message),
                "has_text": bool(_extract_text(getattr(message, "content", ""))),
                "tool_call_count": len(getattr(message, "tool_calls", []) or []) if isinstance(message, AIMessage) else 0,
                "evidence_kind": str((getattr(message, "additional_kwargs", {}) or {}).get("evidence_kind", "")).strip() or None,
            }
        )
    return stats


def _estimate_prompt_tokens(messages: list[Any]) -> int:
    return estimate_token_count("\n".join(_extract_text(getattr(message, "content", "")) for message in messages))


def _compact_prompt_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 2,
    max_items: int = 6,
    max_keys: int = 8,
    max_string: int = 180,
) -> Any:
    if depth >= max_depth:
        if isinstance(value, dict):
            return {"kind": "object", "keys": sorted(str(key) for key in list(value.keys())[:max_keys])}
        if isinstance(value, list):
            return {"kind": "list", "items": len(value)}
        if isinstance(value, str):
            return _truncate_text_middle(value, max_chars=max_string)
        return value
    if isinstance(value, str):
        return _truncate_text_middle(value, max_chars=max_string)
    if isinstance(value, list):
        items = [
            _compact_prompt_value(item, depth=depth + 1, max_depth=max_depth, max_items=max_items, max_keys=max_keys, max_string=max_string)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append({"_remaining_items": len(value) - max_items})
        return items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        keys = list(value.keys())[:max_keys]
        for key in keys:
            compact[str(key)] = _compact_prompt_value(
                value[key],
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_keys=max_keys,
                max_string=max_string,
            )
        if len(value) > max_keys:
            compact["_remaining_keys"] = len(value) - max_keys
        return compact
    return value


def _json_line(value: Any) -> str:
    return json.dumps(_compact_prompt_value(_safe_jsonable(value)), ensure_ascii=False, sort_keys=True)


def _truncate_prompt_note(text: str, *, max_chars: int = 320) -> str:
    return _truncate_text_middle(str(text or "").strip(), max_chars=max_chars)


def _build_latest_conversation_note(messages: list[Any], *, query: str) -> str:
    query_text = str(query or "").strip()
    for message in reversed(list(messages)):
        if isinstance(message, (SystemMessage, ToolMessage)):
            continue
        text = _extract_text(getattr(message, "content", "")).strip()
        if not text:
            continue
        if isinstance(message, HumanMessage) and query_text and text == query_text:
            continue
        role = "assistant" if isinstance(message, AIMessage) else "human"
        return f"role={role}; content={_truncate_prompt_note(text)}"
    return ""


def _detect_data_kind(data: Any) -> str:
    if data is None:
        return "null"
    if isinstance(data, bool):
        return "bool"
    if isinstance(data, (int, float)):
        return "number"
    if isinstance(data, str):
        return "text"
    if isinstance(data, list):
        return "list"
    if isinstance(data, dict):
        return "object"
    return type(data).__name__.lower()


def _estimate_item_count(data: Any) -> int | None:
    if isinstance(data, (list, dict, str)):
        return len(data)
    return None


def _extract_output_blob(data: Any) -> str:
    if isinstance(data, dict):
        output = data.get("output")
        if output is not None:
            return str(output or "")
    return str(data or "")


def _format_counter_hotspots(text: str, *, top_k: int = 5) -> tuple[list[str], int]:
    counters: list[tuple[str, int]] = []
    for match in re.finditer(r"([A-Za-z0-9_.:-]+)\s*[:=]\s*(-?\d+)", text):
        name = str(match.group(1)).strip()
        try:
            value = int(match.group(2))
        except ValueError:
            continue
        if value <= 0:
            continue
        lowered = name.lower()
        if any(token in lowered for token in ("error", "drop", "retry", "reset", "fail", "loss", "timeout", "discard", "wqe", "cqe")):
            counters.append((name, value))
    counters.sort(key=lambda item: item[1], reverse=True)
    selected = [f"{name}={value}" for name, value in counters[:top_k]]
    return selected, len(counters)


def _parse_phase_counts(pods: list[Any]) -> tuple[dict[str, int], list[str]]:
    phases: dict[str, int] = {}
    notable: list[str] = []
    for pod in pods:
        if not isinstance(pod, dict):
            continue
        name = str(pod.get("name", "") or "").strip()
        status = pod.get("status", {})
        phase = "Unknown"
        if isinstance(status, dict):
            phase = str(status.get("phase", "Unknown") or "Unknown").strip() or "Unknown"
        phases[phase] = phases.get(phase, 0) + 1
        if phase not in {"Running", "Succeeded"} and name and len(notable) < 4:
            notable.append(name)
    return phases, notable


def _summarize_prometheus_result(data: Any) -> tuple[str, dict[str, Any] | None]:
    if isinstance(data, (int, float)):
        return f"scalar={data}", {"result_shape": "scalar", "sample_count": 1, "value": data}
    if isinstance(data, list):
        preview = [_compact_prompt_value(item, max_items=2, max_keys=4, max_string=80) for item in data[:3]]
        return f"samples={len(data)}; preview={preview}", {"result_shape": "vector", "sample_count": len(data)}
    if isinstance(data, dict):
        keys = sorted(str(key) for key in list(data.keys())[:8])
        return f"keys={','.join(keys) if keys else 'none'}", {"result_shape": "object", "keys": keys}
    text = _truncate_prompt_note(str(data or ""), max_chars=160)
    return f"value={text or 'none'}", {"result_shape": _detect_data_kind(data)}


def _summarize_k8s_pods(data: Any) -> tuple[str, int | None, dict[str, Any] | None]:
    if not isinstance(data, list):
        return f"unexpected_shape={_detect_data_kind(data)}", _estimate_item_count(data), None
    phases, notable = _parse_phase_counts(data)
    non_running = sum(count for phase, count in phases.items() if phase not in {"Running", "Succeeded"})
    phase_text = ",".join(f"{phase}:{count}" for phase, count in sorted(phases.items()))
    summary = f"pods={len(data)}; non_running={non_running}; phases={phase_text or 'none'}"
    if notable:
        summary = f"{summary}; notable={','.join(notable)}"
    return summary, len(data), {"pod_count": len(data), "non_running": non_running, "phases": phases}


def _summarize_tc_qdisc(data: Any) -> tuple[str, dict[str, Any] | None]:
    text = _extract_output_blob(data)
    findings: list[str] = []
    for pattern in ("netem", "delay", "loss", "corrupt", "reorder"):
        match = re.search(rf"{pattern}\s+([^\n]+)", text, flags=re.IGNORECASE)
        if match:
            findings.append(f"{pattern}={_truncate_prompt_note(match.group(1), max_chars=48)}")
    if not findings:
        findings.append("netem=absent")
    return "; ".join(findings), {"signals": findings[:4]}


def _summarize_link_state(data: Any) -> tuple[str, dict[str, Any] | None]:
    text = _extract_output_blob(data)
    state_match = re.search(r"\bstate\s+(UP|DOWN|UNKNOWN)\b", text)
    speed_match = re.search(r"Speed:\s*([^\n\r]+)", text)
    duplex_match = re.search(r"Duplex:\s*([^\n\r]+)", text)
    carrier_down = len(re.findall(r"NO-CARRIER|state DOWN", text, flags=re.IGNORECASE))
    fields = {
        "state": state_match.group(1) if state_match else "unknown",
        "speed": _truncate_prompt_note(speed_match.group(1), max_chars=32) if speed_match else None,
        "duplex": _truncate_prompt_note(duplex_match.group(1), max_chars=32) if duplex_match else None,
        "carrier_anomalies": carrier_down,
    }
    summary = f"state={fields['state']}; speed={fields['speed'] or 'unknown'}; duplex={fields['duplex'] or 'unknown'}; carrier_anomalies={carrier_down}"
    return summary, fields


def _summarize_counter_text(data: Any, *, label: str) -> tuple[str, dict[str, Any] | None]:
    text = _extract_output_blob(data)
    hotspots, total = _format_counter_hotspots(text)
    if hotspots:
        return f"{label}_hotspots={', '.join(hotspots)}; abnormal_count={total}", {"hotspots": hotspots, "abnormal_count": total}
    return f"{label}_hotspots=none", {"hotspots": [], "abnormal_count": 0}


def _summarize_generic_data(data: Any) -> tuple[str, int | None, dict[str, Any] | None]:
    kind = _detect_data_kind(data)
    count = _estimate_item_count(data)
    if isinstance(data, dict):
        keys = sorted(str(key) for key in list(data.keys())[:8])
        return f"kind=object; keys={','.join(keys) if keys else 'none'}", count, {"keys": keys}
    if isinstance(data, list):
        preview = [_compact_prompt_value(item, max_items=2, max_keys=4, max_string=60) for item in data[:2]]
        return f"kind=list; items={len(data)}; preview={preview}", len(data), {"preview": preview}
    if isinstance(data, str):
        lines = [line.strip() for line in data.splitlines() if line.strip()]
        preview = _truncate_prompt_note(lines[0] if lines else data, max_chars=120)
        return f"kind=text; chars={len(data)}; preview={preview}", len(data), {"chars": len(data)}
    return f"kind={kind}; value={_truncate_prompt_note(str(data), max_chars=80)}", count, None


def _build_tool_prompt_fields(
    *,
    session_id: str,
    source: str,
    step: int,
    tool: str,
    params: dict[str, Any],
    data: Any,
    error: str,
    skill_id: str | None,
) -> dict[str, Any]:
    artifact_ref = f"session:{session_id}:tool:{source}:{step}:{tool}"
    data_kind = _detect_data_kind(data)
    item_count = _estimate_item_count(data)
    key_fields: dict[str, Any] | None = None
    if error:
        prompt_summary = f"error={_truncate_prompt_note(error, max_chars=160)}"
    elif tool == "prometheus.query_instant":
        prompt_summary, key_fields = _summarize_prometheus_result(data)
    elif tool == "k8s.list_pods":
        prompt_summary, item_count, key_fields = _summarize_k8s_pods(data)
    elif tool == "network.get_tc_qdisc":
        prompt_summary, key_fields = _summarize_tc_qdisc(data)
    elif tool == "network.get_nic_link_state":
        prompt_summary, key_fields = _summarize_link_state(data)
    elif tool == "network.get_nic_counters":
        prompt_summary, key_fields = _summarize_counter_text(data, label="nic")
    elif tool == "network.get_rdma_stats":
        prompt_summary, key_fields = _summarize_counter_text(data, label="rdma")
    else:
        prompt_summary, item_count, key_fields = _summarize_generic_data(data)
    if item_count is None:
        item_count = _estimate_item_count(data)
    return {
        "artifact_ref": artifact_ref,
        "data_kind": data_kind,
        "item_count": item_count,
        "key_fields": _safe_jsonable(key_fields) if key_fields is not None else None,
        "prompt_summary": _truncate_prompt_note(prompt_summary, max_chars=220),
        "param_keys": sorted(str(key) for key in params.keys()),
        "skill_id": str(skill_id or "").strip() or None,
    }


def _render_tool_run_for_prompt(item: dict[str, Any]) -> dict[str, Any]:
    rendered = {
        "step": int(item.get("step", 0) or 0),
        "source": str(item.get("source", "") or "").strip() or "tool",
        "tool": str(item.get("tool", "unknown") or "unknown").strip() or "unknown",
        "success": bool(item.get("success", False)),
        "params": _compact_prompt_value(_safe_jsonable(item.get("params", {})), max_items=4, max_keys=6, max_string=96),
        "summary": str(item.get("prompt_summary", "") or "").strip() or "none",
        "artifact_ref": str(item.get("artifact_ref", "") or "").strip() or None,
        "data_kind": str(item.get("data_kind", "") or "").strip() or None,
        "item_count": item.get("item_count"),
        "skill_id": str(item.get("skill_id", "") or "").strip() or None,
        "key_fields": _compact_prompt_value(_safe_jsonable(item.get("key_fields")), max_items=4, max_keys=6, max_string=96),
    }
    if not rendered["key_fields"]:
        rendered.pop("key_fields")
    return rendered


def _build_skill_prompt_summary(
    *,
    skill_id: str,
    status: str,
    summary: str,
    tool_runs: list[dict[str, Any]],
) -> str:
    tool_names = [str(item.get("tool", "unknown") or "unknown").strip() for item in tool_runs]
    tool_text = ",".join(tool_names[:4]) if tool_names else "none"
    if len(tool_names) > 4:
        tool_text = f"{tool_text},+{len(tool_names) - 4} more"
    return _truncate_prompt_note(
        f"status={status}; tools={len(tool_runs)}; tool_names={tool_text}; summary={summary or 'none'}",
        max_chars=220,
    )


def _render_skill_run_for_prompt(item: dict[str, Any]) -> dict[str, Any]:
    raw_tool_runs = item.get("tool_runs", [])
    tool_runs = [dict(run) for run in raw_tool_runs if isinstance(run, dict)]
    return {
        "step": int(item.get("step", 0) or 0),
        "skill_id": str(item.get("skill_id", "") or "").strip() or "unknown",
        "status": str(item.get("status", "") or "").strip() or "unknown",
        "selection_reason": _truncate_prompt_note(str(item.get("selection_reason", "") or "").strip(), max_chars=120),
        "summary": str(item.get("prompt_summary", "") or "").strip() or _truncate_prompt_note(str(item.get("summary", "") or "").strip(), max_chars=160),
        "artifact_ref": str(item.get("artifact_ref", "") or "").strip() or None,
        "tool_steps": [rendered for rendered in (_render_tool_run_for_prompt(run) for run in tool_runs)],
    }


def _build_state_rebuilt_evidence_metadata(
    *,
    section_texts: list[tuple[str, str]],
    tool_runs: list[dict[str, Any]],
    skill_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    section_estimates = [
        {
            "section": name,
            "chars": len(text),
            "tokens": estimate_token_count(text),
        }
        for name, text in section_texts
    ]
    largest_items: list[dict[str, Any]] = []
    for item in tool_runs:
        summary = str(item.get("prompt_summary", "") or "").strip()
        largest_items.append(
            {
                "label": f"tool:{item.get('tool', 'unknown')}#{item.get('step', 0)}",
                "chars": len(summary),
                "tokens": estimate_token_count(summary),
                "artifact_ref": item.get("artifact_ref"),
            }
        )
    for item in skill_runs:
        summary = str(item.get("prompt_summary", "") or "").strip() or str(item.get("summary", "") or "").strip()
        largest_items.append(
            {
                "label": f"skill:{item.get('skill_id', 'unknown')}#{item.get('step', 0)}",
                "chars": len(summary),
                "tokens": estimate_token_count(summary),
                "artifact_ref": item.get("artifact_ref"),
            }
        )
    largest_items.sort(key=lambda entry: (int(entry["tokens"]), int(entry["chars"])), reverse=True)
    dominant_section = max(section_estimates, key=lambda entry: int(entry["tokens"]), default=None)
    dominant_item = largest_items[0] if largest_items else None
    return {
        "section_estimates": section_estimates,
        "largest_evidence_items": largest_items[:3],
        "dominant_section": dominant_section["section"] if dominant_section else None,
        "dominant_section_tokens": dominant_section["tokens"] if dominant_section else 0,
        "dominant_item": dominant_item["label"] if dominant_item else None,
    }


def _build_state_rebuilt_sections(
    state: SREAgentState,
    *,
    final_turn: bool,
    tool_binding_fallback: bool,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    query = str(state.get("query", "") or "").strip()
    variables = dict(state.get("variables", {}) or {})
    alert_snapshot = state.get("alert_snapshot")
    extra_alerts = list(state.get("extra_alerts", []) or [])
    topology_context = state.get("topology_context")
    diagnosis_result = state.get("diagnosis_result")
    remediation_plan = state.get("remediation_plan")
    skill_runs = list(state.get("skill_runs", []) or [])
    tool_runs = list(state.get("tool_runs", []) or [])
    conversation_note = _build_latest_conversation_note(list(_coerce_messages(state.get("messages", []))), query=query)
    rendered_skill_runs = [_render_skill_run_for_prompt(item) for item in skill_runs if isinstance(item, dict)]
    rendered_tool_runs = [_render_tool_run_for_prompt(item) for item in tool_runs if isinstance(item, dict)]
    skill_history_text = _json_line(rendered_skill_runs) if rendered_skill_runs else "none"
    tool_ledger_text = _json_line(rendered_tool_runs) if rendered_tool_runs else "none"

    instruction = (
        "Use the canonical evidence above and return the final JSON now. "
        "Do not call more tools. If remediation details are incomplete, set remediation_plan to null."
        if final_turn
        else (
            "Tool-binding is unavailable for the current LLM provider. "
            "Use the canonical evidence above and return final JSON directly. Do not call tools."
            if tool_binding_fallback
            else "Continue diagnosis using the canonical evidence above. "
            "Use read-only tools only if more evidence is required; otherwise return final JSON."
        )
    )

    sections = [
        (
            "request_context",
            "Original query and runtime variables:\n"
            f"query={query or '<empty>'}\n"
            f"variables={_json_line(variables)}",
        ),
        (
            "alert_context",
            "Alert context:\n"
            f"primary_alert={_json_line(alert_snapshot)}\n"
            f"extra_alerts={_json_line(extra_alerts)}",
        ),
        (
            "topology_context",
            "Topology context:\n"
            f"{_json_line(topology_context)}",
        ),
        (
            "diagnosis_state",
            "Current diagnosis and remediation state:\n"
            f"diagnosis_result={_json_line(diagnosis_result)}\n"
            f"remediation_plan={_json_line(remediation_plan)}",
        ),
        (
            "skill_history",
            "Skill execution history:\n"
            f"{skill_history_text}",
        ),
        (
            "tool_ledger",
            "Tool evidence ledger:\n"
            f"{tool_ledger_text}",
        ),
        (
            "conversation_note",
            "Latest conversational note:\n"
            f"{conversation_note or 'none'}",
        ),
        (
            "reasoning_instruction",
            instruction,
        ),
    ]
    return sections, _build_state_rebuilt_evidence_metadata(
        section_texts=sections,
        tool_runs=tool_runs,
        skill_runs=skill_runs,
    )


def _build_state_rebuilt_reason_messages(
    *,
    system_prompt: str,
    state: SREAgentState,
    final_turn: bool,
    tool_binding_fallback: bool,
) -> tuple[list[Any], dict[str, Any]]:
    sections, evidence_meta = _build_state_rebuilt_sections(
        state,
        final_turn=final_turn,
        tool_binding_fallback=tool_binding_fallback,
    )
    messages: list[Any] = [SystemMessage(content=system_prompt)]
    for section_name, section_text in sections:
        messages.append(
            HumanMessage(
                content=section_text,
                additional_kwargs={"evidence_kind": section_name},
            )
        )
    messages, prompt_fallback_used = _ensure_non_empty_reason_prompt(messages, state=state)
    return messages, {
        "prompt_mode": "state_rebuilt_full",
        "prompt_sections": [section_name for section_name, _ in sections],
        "estimated_input_tokens": _estimate_prompt_tokens(messages),
        "tool_run_count": len(list(state.get("tool_runs", []) or [])),
        "skill_run_count": len(list(state.get("skill_runs", []) or [])),
        "provider_invocation_skipped": False,
        "prompt_fallback_used": bool(prompt_fallback_used),
        **evidence_meta,
    }


def _build_transcript_compact_reason_messages(
    *,
    system_prompt: str,
    state: SREAgentState,
    final_turn: bool,
    tool_binding_fallback: bool,
) -> tuple[list[Any], dict[str, Any]]:
    messages = _coerce_messages(state.get("messages", []))
    if not messages:
        messages = [HumanMessage(content=state["query"])]
    else:
        messages = list(messages)

    reason_context_char_budget = _normalize_positive_int(
        state.get("reason_context_char_budget"),
        default=2400,
        minimum=600,
    )
    tool_message_char_limit = _normalize_positive_int(
        state.get("tool_message_char_limit"),
        default=1200,
        minimum=200,
    )
    reason_preserve_recent_messages = _normalize_positive_int(
        state.get("reason_preserve_recent_messages"),
        default=6,
        minimum=1,
    )

    if final_turn:
        compact_messages = _apply_context_window_budget(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Original query: {state['query']}"),
                HumanMessage(content=f"Collected evidence:\n{_summarize_tool_runs(state.get('tool_runs', []))}"),
                HumanMessage(
                    content=(
                        "Use the evidence already collected and return the final JSON now. "
                        "Do not call more tools. If remediation details are incomplete, set remediation_plan to null."
                    )
                ),
            ],
            char_budget=reason_context_char_budget,
            preserve_recent=3,
        )
    elif tool_binding_fallback:
        compact_messages = _apply_context_window_budget(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Original query: {state['query']}"),
                HumanMessage(content=f"Collected evidence:\n{_summarize_tool_runs(state.get('tool_runs', []))}"),
                HumanMessage(
                    content=(
                        "Tool-binding is unavailable for the current LLM provider. "
                        "Return final JSON diagnosis directly with explicit uncertainty where needed. "
                        "Do not call tools."
                    )
                ),
            ],
            char_budget=reason_context_char_budget,
            preserve_recent=3,
        )
    else:
        prepared_messages = _truncate_tool_messages(messages, max_chars=tool_message_char_limit)
        compact_messages = _apply_context_window_budget(
            [SystemMessage(content=system_prompt), *prepared_messages],
            char_budget=reason_context_char_budget,
            preserve_recent=reason_preserve_recent_messages,
        )

    compact_messages, prompt_fallback_used = _ensure_non_empty_reason_prompt(compact_messages, state=state)
    return compact_messages, {
        "prompt_mode": "transcript_compact",
        "prompt_sections": [],
        "estimated_input_tokens": _estimate_prompt_tokens(compact_messages),
        "tool_run_count": len(list(state.get("tool_runs", []) or [])),
        "skill_run_count": len(list(state.get("skill_runs", []) or [])),
        "provider_invocation_skipped": False,
        "prompt_fallback_used": bool(prompt_fallback_used),
    }


def _prepare_reason_prompt_messages(
    *,
    system_prompt: str,
    state: SREAgentState,
    final_turn: bool,
    tool_binding_fallback: bool,
) -> tuple[list[Any], dict[str, Any], bool]:
    model_family = str(state.get("reasoning_model_family", "") or "").strip() or "MiniMax-M2.7"
    strategy = _normalize_reasoning_context_strategy(
        state.get("reasoning_context_strategy"),
        model_family=model_family,
    )
    overflow_behavior = _normalize_reasoning_overflow_behavior(
        state.get("reasoning_overflow_behavior"),
        model_family=model_family,
    )
    target_tokens = _normalize_reasoning_input_target_tokens(
        state.get("reasoning_input_target_tokens"),
        model_family=model_family,
    )

    if strategy == "state_rebuilt":
        rebuilt_messages, rebuilt_meta = _build_state_rebuilt_reason_messages(
            system_prompt=system_prompt,
            state=state,
            final_turn=final_turn,
            tool_binding_fallback=tool_binding_fallback,
        )
        rebuilt_meta["reasoning_context_strategy"] = strategy
        rebuilt_meta["reasoning_overflow_behavior"] = overflow_behavior
        rebuilt_meta["reasoning_model_family"] = model_family
        rebuilt_meta["reasoning_input_target_tokens"] = target_tokens
        if int(rebuilt_meta.get("estimated_input_tokens", 0) or 0) <= target_tokens:
            return rebuilt_messages, rebuilt_meta, False
        if overflow_behavior == "compact":
            compact_messages, compact_meta = _build_transcript_compact_reason_messages(
                system_prompt=system_prompt,
                state=state,
                final_turn=final_turn,
                tool_binding_fallback=tool_binding_fallback,
            )
            compact_meta.update(
                {
                    "reasoning_context_strategy": strategy,
                    "reasoning_overflow_behavior": overflow_behavior,
                    "reasoning_model_family": model_family,
                    "reasoning_input_target_tokens": target_tokens,
                    "overflow_estimated_input_tokens": rebuilt_meta["estimated_input_tokens"],
                    "overflow_target_tokens": target_tokens,
                }
            )
            return compact_messages, compact_meta, False
        rebuilt_meta.update(
            {
                "prompt_mode": "state_rebuilt_overflow_fail",
                "provider_invocation_skipped": True,
                "overflow_estimated_input_tokens": rebuilt_meta["estimated_input_tokens"],
                "overflow_target_tokens": target_tokens,
            }
        )
        return rebuilt_messages, rebuilt_meta, True

    compact_messages, compact_meta = _build_transcript_compact_reason_messages(
        system_prompt=system_prompt,
        state=state,
        final_turn=final_turn,
        tool_binding_fallback=tool_binding_fallback,
    )
    compact_meta.update(
        {
            "reasoning_context_strategy": strategy,
            "reasoning_overflow_behavior": overflow_behavior,
            "reasoning_model_family": model_family,
            "reasoning_input_target_tokens": target_tokens,
        }
    )
    return compact_messages, compact_meta, False


def _prepare_reason_prompt_messages_for_invocation(
    *,
    system_prompt: str,
    state: SREAgentState,
    final_turn: bool,
    tool_binding_fallback: bool,
) -> tuple[list[Any], dict[str, Any], bool]:
    messages, metadata, overflow_failed = _prepare_reason_prompt_messages(
        system_prompt=system_prompt,
        state=state,
        final_turn=final_turn,
        tool_binding_fallback=tool_binding_fallback,
    )
    if overflow_failed:
        return messages, metadata, overflow_failed
    if messages and _has_non_system_prompt_text(messages):
        return messages, metadata, overflow_failed
    minimal_messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=str(state.get("query", "") or "Continue diagnosis using available evidence.")),
    ]
    prompt_fallback_used = bool(metadata.get("prompt_fallback_used"))
    return minimal_messages, {
        **metadata,
        "prompt_mode": "minimal_safe",
        "prompt_fallback_used": True,
        "prompt_empty_guard_triggered": True,
    }, False


def _build_reason_overflow_failure_state(
    state: SREAgentState,
    *,
    prompt_messages: list[Any],
    prompt_metadata: dict[str, Any],
    interaction_mode: str,
    tool_choice: str,
) -> SREAgentState:
    estimated_tokens = int(prompt_metadata.get("overflow_estimated_input_tokens") or prompt_metadata.get("estimated_input_tokens") or 0)
    target_tokens = int(prompt_metadata.get("overflow_target_tokens") or prompt_metadata.get("reasoning_input_target_tokens") or 0)
    dominant_section = str(prompt_metadata.get("dominant_section", "") or "").strip()
    dominant_item = str(prompt_metadata.get("dominant_item", "") or "").strip()
    top_evidence_items = list(prompt_metadata.get("largest_evidence_items", []) or [])[:3]
    dominant_bits = [bit for bit in (dominant_section and f"section={dominant_section}", dominant_item and f"item={dominant_item}") if bit]
    detail_suffix = f"; dominant={' '.join(dominant_bits)}" if dominant_bits else ""
    error_text = (
        "reason step failed: guaranteed-fidelity context exceeded safe model budget "
        f"({estimated_tokens} estimated tokens > target {target_tokens}{detail_suffix})"
    )
    step_index = state.get("step_count", 0) + 1
    trace_items = list(state.get("trace_items", []))
    trace_items.append(
        {
            "type": "thought",
            "step": step_index,
            "content": (
                "Reasoning was stopped before the provider call because guaranteed-fidelity context "
                "exceeded the configured safe model budget."
            ),
            "action": "conclude",
            "confidence": None,
            "tool_params": {
                "kind": "reason_context_overflow",
                "mode": "guaranteed_fidelity",
                "estimated_tokens": estimated_tokens,
                "target_tokens": target_tokens,
                "dominant_section": dominant_section or None,
                "dominant_item": dominant_item or None,
                "largest_evidence_items": top_evidence_items,
            },
        }
    )
    prompt_stats = _build_prompt_message_stats(list(prompt_messages))
    _log_llm_interaction(
        session_id=str(state.get("session_id", "unknown")),
        step=step_index,
        prompt_messages=list(prompt_messages),
        response=None,
        mode=interaction_mode,
        tool_choice=tool_choice,
        tool_calls=[],
        prompt_message_stats=prompt_stats,
        prompt_fallback_used=bool(prompt_metadata.get("prompt_fallback_used")),
        prompt_metadata=prompt_metadata,
    )
    interactions = list(state.get("llm_interactions", []))
    interactions.append(
        {
            "step": step_index,
            "mode": interaction_mode,
            "tool_choice": tool_choice,
            "bound_tool_names": [],
            "prompt_messages": messages_to_dict(prompt_messages),
            "prompt_message_stats": prompt_stats,
            "prompt_fallback_used": bool(prompt_metadata.get("prompt_fallback_used")),
            "prompt_metadata": dict(prompt_metadata),
            "response_message": None,
            "raw_response_text": "",
            "tool_calls": [],
        }
    )
    updated = {
        **state,
        "llm_interactions": interactions,
        "trace_items": trace_items,
        "pending_tool_calls": [],
        "step_count": step_index,
        "status": "failed",
        "summary": error_text,
        "error": error_text,
    }
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason-failed", updated)
    return updated


def _has_non_system_prompt_text(messages: list[Any]) -> bool:
    for message in messages[1:]:
        if _extract_text(getattr(message, "content", "")).strip():
            return True
    return False


def _compact_param_summary(params: dict[str, Any]) -> str:
    if not isinstance(params, dict) or not params:
        return "-"
    keys = [str(key).strip() for key in params.keys() if str(key).strip()]
    if not keys:
        return "-"
    return ",".join(sorted(keys)[:4])


def _build_tool_run_highlights(tool_runs: list[dict[str, Any]], *, max_items: int = 4) -> list[str]:
    highlights: list[str] = []
    for item in tool_runs[-max(1, max_items):]:
        tool = str(item.get("tool", "unknown")).strip() or "unknown"
        success = bool(item.get("success", False))
        error = str(item.get("error", "") or "").strip()
        summary = error or _extract_tool_data_summary(_safe_jsonable(item.get("data")))
        params = _compact_param_summary(_safe_jsonable(item.get("params", {})))
        source = str(item.get("source", "") or "").strip()
        skill_id = str(item.get("skill_id", "") or "").strip()
        prefix = f"source={source}; " if source else ""
        skill_part = f"; skill_id={skill_id}" if skill_id else ""
        highlights.append(
            f"- {prefix}tool={tool}; success={success}; params={params}{skill_part}; summary={summary or 'none'}"
        )
    return highlights


def _extract_query_anchor(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    quoted_alert = re.search(r"alert\s+'([^']+)'", text, flags=re.IGNORECASE)
    if quoted_alert:
        return quoted_alert.group(1).strip()
    alertname = re.search(r"alertname\s+([A-Za-z0-9_.:-]+)", text, flags=re.IGNORECASE)
    if alertname:
        return alertname.group(1).strip()
    return _truncate_text_end(text, max_chars=80)


def _build_compact_skill_result_message(
    *,
    skill_id: str,
    status: str,
    tool_runs: list[dict[str, Any]],
    max_chars: int,
    query: str = "",
) -> str:
    query_text = str(query or "").strip()
    header = f"Skill result: skill_id={skill_id}"
    if query_text:
        query_anchor = _extract_query_anchor(query_text)
        if query_anchor:
            header = f"{header}; query_anchor={query_anchor}"
    header = f"{header}; status={status}; tool_runs={len(tool_runs)}"
    lines = [header]
    lines.append("Highlights:")
    highlights = _build_tool_run_highlights(tool_runs, max_items=4)
    if highlights:
        lines.extend(highlights)
    else:
        lines.append("- none")
    message = "\n".join(lines)
    compact = _truncate_text_end(message, max_chars=max_chars)
    if compact != message:
        _llm_logger.debug(
            "Compacted skill summary for %s from %s to %s chars",
            skill_id,
            len(message),
            len(compact),
        )
    return compact


def _build_reason_prompt_fallback_message(state: SREAgentState) -> str:
    query = str(state.get("query", "") or "").strip()
    recent_skill_id = ""
    recent_skill_status = ""
    recent_skill_runs: list[dict[str, Any]] = []
    for interaction in reversed(list(state.get("llm_interactions", []))):
        if str(interaction.get("mode", "")).strip() != "skill_execution":
            continue
        recent_skill_id = str(interaction.get("skill_id", "") or "").strip()
        recent_skill_status = "success" if recent_skill_id else ""
        raw_runs = interaction.get("tool_runs", [])
        if isinstance(raw_runs, list):
            recent_skill_runs = [dict(item) for item in raw_runs if isinstance(item, dict)]
        break

    lines = ["Diagnosis context fallback:"]
    if query:
        lines.append(f"Original query: {query}")
    if recent_skill_id:
        lines.append(
            _build_compact_skill_result_message(
                skill_id=recent_skill_id,
                status=recent_skill_status or "unknown",
                tool_runs=recent_skill_runs,
                max_chars=520,
                query=query,
            )
        )
    elif state.get("tool_runs"):
        lines.append("Recent evidence:")
        lines.extend(_build_tool_run_highlights(list(state.get("tool_runs", [])), max_items=3))
    else:
        lines.append("No retained tool evidence was available; continue from the original diagnosis request.")
    fallback = _truncate_text_end("\n".join(lines), max_chars=900)
    return fallback.strip()


def _ensure_non_empty_reason_prompt(messages: list[Any], *, state: SREAgentState) -> tuple[list[Any], bool]:
    if not messages:
        raise RuntimeError("reason prompt collapsed to empty chat content before LLM call")
    if _has_non_system_prompt_text(messages):
        return messages, False
    fallback = _build_reason_prompt_fallback_message(state)
    if not fallback:
        raise RuntimeError("reason prompt collapsed to empty chat content before LLM call")
    _llm_logger.debug(
        "Injected fallback reason prompt for session %s after context trimming",
        str(state.get("session_id", "")),
    )
    return [
        messages[0],
        HumanMessage(
            content=fallback,
            additional_kwargs={"evidence_kind": "fallback_context"},
        ),
    ], True


def _apply_context_window_budget(messages: list[Any], *, char_budget: int, preserve_recent: int) -> list[Any]:
    normalized_messages = _drop_orphan_tool_messages(messages)
    if not normalized_messages:
        return []
    budget = _normalize_positive_int(char_budget, default=2400, minimum=600)
    keep_recent = _normalize_positive_int(preserve_recent, default=6, minimum=1)
    system_message = normalized_messages[0]
    bundles = _group_message_bundles(list(normalized_messages[1:]))
    keep_recent = min(keep_recent, len(bundles)) if bundles else 0
    selected_bundles = bundles[-keep_recent:] if keep_recent > 0 else []
    if bundles and not selected_bundles:
        selected_bundles = [bundles[-1]]
    if bundles and selected_bundles:
        selected_bundle_ids = {id(bundle) for bundle in selected_bundles}
        for bundle in reversed(bundles[:-keep_recent] if keep_recent > 0 else bundles[:-1]):
            candidate_messages = [system_message, *_flatten_message_bundles([bundle, *selected_bundles])]
            if sum(_estimate_message_chars(msg) for msg in candidate_messages) > budget:
                continue
            if id(bundle) in selected_bundle_ids:
                continue
            selected_bundles.insert(0, bundle)
            selected_bundle_ids.add(id(bundle))

    def _materialize(bundles_to_use: list[list[Any]]) -> list[Any]:
        rendered = [system_message, *_flatten_message_bundles(bundles_to_use)] if bundles_to_use else [system_message]
        rendered = _drop_orphan_tool_messages(rendered)
        if len(rendered) == 1 and bundles:
            rendered = _drop_orphan_tool_messages([system_message, *_flatten_message_bundles([bundles[-1]])])
        return rendered

    trimmed = _materialize(selected_bundles)
    total_chars = sum(_estimate_message_chars(msg) for msg in trimmed)
    if total_chars <= budget:
        return trimmed

    for _ in range(3):
        changed = False
        for index in range(1, len(trimmed)):
            overflow = total_chars - budget
            if overflow <= 0:
                break
            original_len = _estimate_message_chars(trimmed[index])
            minimum = _minimum_message_chars(trimmed[index], default=80)
            if original_len <= minimum:
                continue
            target = max(minimum, original_len - overflow)
            updated = _truncate_message_to_chars(trimmed[index], max_chars=target)
            if _estimate_message_chars(updated) >= original_len:
                continue
            trimmed[index] = updated
            total_chars = sum(_estimate_message_chars(msg) for msg in trimmed)
            changed = True
        if total_chars <= budget or not changed:
            break

    while len(selected_bundles) > 1 and total_chars > budget:
        selected_bundles.pop(0)
        trimmed = _materialize(selected_bundles)
        total_chars = sum(_estimate_message_chars(msg) for msg in trimmed)
        for index in range(1, len(trimmed)):
            overflow = total_chars - budget
            if overflow <= 0:
                break
            original_len = _estimate_message_chars(trimmed[index])
            minimum = _minimum_message_chars(trimmed[index], default=60)
            if original_len <= minimum:
                continue
            target = max(minimum, original_len - overflow)
            trimmed[index] = _truncate_message_to_chars(trimmed[index], max_chars=target)
            total_chars = sum(_estimate_message_chars(msg) for msg in trimmed)

    if total_chars > budget and len(trimmed) >= 2:
        for index in range(1, len(trimmed)):
            overflow = total_chars - budget
            if overflow <= 0:
                break
            original_len = _estimate_message_chars(trimmed[index])
            minimum = _minimum_message_chars(trimmed[index], default=40)
            if original_len <= minimum:
                continue
            target = max(minimum, original_len - overflow)
            trimmed[index] = _truncate_message_to_chars(trimmed[index], max_chars=target)
            total_chars = sum(_estimate_message_chars(msg) for msg in trimmed)

    if total_chars > budget and trimmed:
        trimmed[0] = _truncate_message_to_chars(trimmed[0], max_chars=max(120, budget // 2))
    return _drop_orphan_tool_messages(trimmed)


def _truncate_tool_messages(messages: list[Any], *, max_chars: int) -> list[Any]:
    limit = _normalize_positive_int(max_chars, default=1200, minimum=200)
    updated: list[Any] = []
    for message in messages:
        if isinstance(message, ToolMessage) and _estimate_message_chars(message) > limit:
            updated.append(_truncate_message_to_chars(message, max_chars=limit))
            continue
        updated.append(message)
    return updated


def _extract_tool_data_summary(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("summary", "message", "status", "reason"):
            value = data.get(key)
            text = str(value or "").strip()
            if text:
                return _truncate_text_middle(text, max_chars=180)
        keys = ", ".join(sorted(str(key) for key in data.keys())[:8])
        return f"keys={keys}" if keys else ""
    if isinstance(data, list):
        return f"items={len(data)}"
    text = str(data or "").strip()
    return _truncate_text_middle(text, max_chars=180)


def _build_tool_message_content(*, success: bool, data: Any, error: str | None, char_limit: int) -> str:
    limit = _normalize_positive_int(char_limit, default=1200, minimum=200)
    payload: dict[str, Any] = {
        "success": bool(success),
        "error": str(error or "").strip() or None,
        "summary": _extract_tool_data_summary(data),
        "data": data,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= limit:
        return serialized
    compact_payload: dict[str, Any] = {
        "success": bool(success),
        "error": payload["error"],
        "summary": payload["summary"],
        "truncated": True,
        "data_preview": _truncate_text_middle(json.dumps(_safe_jsonable(data), ensure_ascii=False), max_chars=max(120, limit // 2)),
    }
    compact = json.dumps(compact_payload, ensure_ascii=False)
    if len(compact) <= limit:
        return compact
    compact_payload["data_preview"] = _truncate_text_middle(str(compact_payload["data_preview"]), max_chars=max(60, limit // 3))
    compact = json.dumps(compact_payload, ensure_ascii=False)
    if len(compact) <= limit:
        return compact
    compact_payload.pop("summary", None)
    compact = json.dumps(compact_payload, ensure_ascii=False)
    return _truncate_text_middle(compact, max_chars=limit)


def _is_missing_required_skill_variable_error(summary: str) -> bool:
    lowered = (summary or "").strip().lower()
    return "missing required variable:" in lowered


def _build_fallback_final_output(*, query: str, content: str) -> FinalDiagnosisEnvelope:
    summary = (content or "").strip()
    if not summary:
        summary = "LLM 在生成诊断结论时返回了空响应。"
    if len(summary) > 300:
        summary = summary[:297] + "..."
    root_cause = summary.splitlines()[0].strip() if summary else "LLM 响应中的证据不足，暂无法确认根因"
    if len(root_cause) > 140:
        root_cause = root_cause[:137] + "..."
    return FinalDiagnosisEnvelope.model_validate(
        {
            "thought": "已将非 JSON 模型输出转换为结构化的低置信度诊断结果。",
            "diagnosis": {
                "root_cause": root_cause or "LLM 响应中的证据不足，暂无法确认根因",
                "root_cause_layer": "platform",
                "root_cause_entities": [],
                "confidence": 0.35,
                "impact_summary": summary,
                "affected_services": [],
                "triage_priority": "P2",
                "diagnosis_certainty": "ambiguous",
                "hypotheses": [
                    {
                        "description": root_cause or "来自非结构化模型输出的主要根因假设",
                        "status": "testing",
                        "evidence_for": [summary] if summary else [],
                        "evidence_against": [],
                        "confidence": 0.35,
                    },
                    {
                        "description": "指标采集或 tool 证据当前不可用，或与模型调用链路不兼容",
                        "status": "testing",
                        "evidence_for": ["已触发 tool binding fallback 路径"],
                        "evidence_against": [],
                        "confidence": 0.3,
                    },
                    {
                        "description": "告警也可能是瞬时波动，或当前上下文仍不完整",
                        "status": "testing",
                        "evidence_for": [f"query={query}"] if query else [],
                        "evidence_against": [],
                        "confidence": 0.25,
                    },
                ],
            },
            "remediation_plan": None,
        }
    )


def _build_fallback_reasoning_output(*, query: str, content: str) -> ReasoningEnvelope:
    fallback = _build_fallback_final_output(query=query, content=content)
    return ReasoningEnvelope(
        thought=fallback.thought,
        diagnosis=fallback.diagnosis,
        remediation_plan=fallback.remediation_plan,
    )


def _canonicalize_tool_run(
    payload: dict[str, Any],
    *,
    session_id: str,
    source: str,
) -> dict[str, Any]:
    step = _normalize_positive_int(payload.get("step"), default=1, minimum=1)
    tool = str(payload.get("tool", "") or "").strip() or "unknown"
    params = _safe_jsonable(payload.get("params", {}))
    if not isinstance(params, dict):
        params = {}
    success = bool(payload.get("success", False))
    return {
        "step": step,
        "tool": tool,
        "params": params,
        "success": success,
        "data": _safe_jsonable(payload.get("data")),
        "error": str(payload.get("error", "") or "").strip() or None,
        "session_id": str(session_id or "").strip(),
        "source": str(source or "").strip() or "tool",
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def act_node(
    state: SREAgentState,
    *,
    registry: ToolRegistry,
    context: ToolExecutionContext | None,
) -> SREAgentState:
    if context is None:
        updated = {
            **state,
            "status": "failed",
            "summary": "tool execution context is required",
            "error": "tool execution context is required",
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "act-failed", updated)
        return updated

    messages = list(_coerce_messages(state.get("messages", [])))
    tool_runs = list(state.get("tool_runs", []))
    pending = list(state.get("pending_tool_calls", []))
    variables = dict(state.get("variables", {}))
    tool_message_char_limit = _normalize_positive_int(
        state.get("tool_message_char_limit"),
        default=1200,
        minimum=200,
    )
    observation_entries: list[dict[str, Any]] = []
    for tool_call in pending:
        tool_name = str(tool_call.get("name", "")).strip()
        raw_tool_args = tool_call.get("args", {})
        tool_args = _merge_tool_args(
            registry=registry,
            tool_name=tool_name,
            tool_args=raw_tool_args if isinstance(raw_tool_args, dict) else {},
            variables=variables,
        )
        try:
            result = await asyncio.wait_for(
                registry.execute(tool_name, tool_args, context),
                timeout=state["step_timeout_sec"],
            )
        except asyncio.TimeoutError:
            result = ToolResult(tool=tool_name, success=False, data=None, error=f"tool timed out after {state['step_timeout_sec']}s")
        serialized = _canonicalize_tool_run(
            {
                "step": len(tool_runs) + 1,
                "tool": tool_name,
                "params": tool_args,
                "success": result.success,
                "data": result.data,
                "error": result.error,
            },
            session_id=str(state.get("session_id", "unknown")),
            source="tool",
        )
        # 记录工具执行结果到日志文件
        _log_tool_execution(
            session_id=str(state.get("session_id", "unknown")),
            step=serialized["step"],
            tool_name=tool_name,
            tool_args=tool_args,
            result={
                "success": result.success,
                "data": _safe_jsonable(result.data),
                "error": result.error,
            },
        )
        tool_runs.append(serialized)
        messages.append(
            ToolMessage(
                tool_call_id=str(tool_call.get("id", "")),
                content=_build_tool_message_content(
                    success=result.success,
                    data=_safe_jsonable(result.data),
                    error=result.error,
                    char_limit=tool_message_char_limit,
                ),
                name=tool_name,
            )
        )
        observation_entries.append(
            {
                "type": "observation",
                "tool": tool_name,
                "params": tool_args,
                "result": {
                    "success": result.success,
                    "data": _safe_jsonable(result.data),
                    "error": result.error,
                },
            }
        )
    updated = {
        **state,
        "messages": messages,
        "tool_runs": tool_runs,
        "pending_tool_calls": [],
        "trace_items": [*state.get("trace_items", []), *observation_entries],
        "status": "running",
        "summary": state.get("summary"),
        "error": None,
    }
    if any(not bool(run.get("result", {}).get("success", False)) for run in observation_entries):
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "act-failed", updated)
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "act", updated)
    return updated


def observe_node(state: SREAgentState) -> SREAgentState:
    persist_state_snapshot(state.get("checkpoint_dir"), state["session_id"], "observe", state)
    return {
        **state,
        "status": state.get("status") or "running",
    }


def decide_node(state: SREAgentState) -> SREAgentState:
    persist_state_snapshot(state.get("checkpoint_dir"), state["session_id"], "decide", state)
    return state


def finalize_node(state: SREAgentState) -> SREAgentState:
    trace = ThinkingTrace.from_langraph_state(state.get("trace_items", []))
    summary = state.get("summary")
    if not summary and state.get("diagnosis_result"):
        summary = str(state["diagnosis_result"].get("impact_summary", "")).strip() or None
    serialized_trace: list[dict[str, Any]] = []
    for item in trace.steps:
        if isinstance(item, ThinkingStep):
            serialized_trace.append(
                {
                    "type": "thought",
                    "step": item.step,
                    "timestamp": item.timestamp.isoformat(),
                    "content": item.thought,
                    "action": item.action_type,
                    "tool_name": item.tool_name,
                    "tool_params": item.tool_params,
                    "confidence": item.confidence,
                }
            )
        else:
            serialized_trace.append(
                {
                    "type": "observation",
                    "timestamp": item.timestamp.isoformat(),
                    "tool": item.tool,
                    "params": item.params,
                    "result": item.result,
                }
            )
    updated = {
        **state,
        "trace_items": serialized_trace,
        "summary": summary,
    }
    # 当步骤超时且没有诊断结果时，从已收集的证据合成部分诊断
    if updated.get("status") == "step_timeout" and updated.get("diagnosis_result") is None:
        tool_runs = updated.get("tool_runs", [])
        skill_runs = updated.get("skill_runs", [])
        evidence_parts: list[str] = []
        for tr in tool_runs:
            if tr.get("success"):
                tool_name = tr.get("tool", "unknown")
                data = tr.get("data")
                if data:
                    evidence_parts.append(f"{tool_name}: {_safe_jsonable(data)}")
        evidence_summary = "; ".join(evidence_parts)[:500] if evidence_parts else "no tool evidence collected"
        partial_diagnosis = {
            "root_cause": "Diagnosis timed out during analysis; partial evidence collected",
            "root_cause_layer": "service",
            "root_cause_entities": [],
            "confidence": 0.45,
            "impact_summary": f"Diagnosis incomplete due to timeout. Evidence: {evidence_summary}",
            "affected_services": [],
            "triage_priority": "P2",
            "diagnosis_certainty": "ambiguous",
            "hypotheses": [
                {
                    "description": "Diagnosis was interrupted by step timeout",
                    "status": "testing",
                    "evidence_for": [evidence_summary] if evidence_parts else [],
                    "evidence_against": [],
                    "confidence": 0.45,
                },
            ],
        }
        updated["diagnosis_result"] = partial_diagnosis
        updated["summary"] = f"Diagnosis timed out; partial evidence: {evidence_summary[:200]}"
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "finalize", updated)
    return updated


def route_after_reason(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout", "step_timeout"}:
        return "finalize"
    if state.get("pending_tool_calls"):
        return "act"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "finalize"


def route_after_decide(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout", "step_timeout"}:
        return "finalize"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "reason"


def _merge_tool_args(
    *,
    registry: ToolRegistry,
    tool_name: str,
    tool_args: dict[str, Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(tool_args)
    nested_kwargs = merged.pop("kwargs", None)
    if isinstance(nested_kwargs, dict):
        for key, value in nested_kwargs.items():
            merged.setdefault(str(key), value)
    if not variables:
        _normalize_runtime_defaults(merged, variables)
        return merged
    try:
        tool_def = registry.get_tool(tool_name)
    except Exception:  # noqa: BLE001
        tool_def = None

    required: list[str] = []
    if tool_def is not None:
        schema = tool_def.params_schema or {}
        raw_required = schema.get("required", [])
        if isinstance(raw_required, list):
            required = [str(item).strip() for item in raw_required if str(item).strip()]

    for key, value in variables.items():
        if key not in merged and (not required or key in required):
            merged[key] = value
    _normalize_runtime_defaults(merged, variables)
    return merged


def _normalize_runtime_defaults(params: dict[str, Any], variables: dict[str, Any]) -> None:
    if "node" in variables:
        params["node"] = variables["node"]
    if "namespace" in variables:
        params["namespace"] = variables["namespace"]


def _summarize_tool_runs(tool_runs: list[dict[str, Any]]) -> str:
    if not tool_runs:
        return "- none"
    lines: list[str] = []
    for item in tool_runs[-10:]:
        tool = str(item.get("tool", "unknown"))
        source = str(item.get("source", "") or "").strip()
        skill_id = str(item.get("skill_id", "") or "").strip()
        success = bool(item.get("success", False))
        params = _safe_jsonable(item.get("params", {}))
        data = _safe_jsonable(item.get("data"))
        error = str(item.get("error", "") or "").strip()
        prefix = f"source={source} " if source else ""
        skill_text = f" skill_id={skill_id}" if skill_id else ""
        lines.append(f"- {prefix}tool={tool}{skill_text} success={success} params={params}")
        if error:
            lines.append(f"  error={error}")
        else:
            lines.append(f"  data={data}")
    return "\n".join(lines)


def _coerce_messages(values: list[Any]) -> list[Any]:
    return list(values)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part.strip() for part in parts if str(part).strip()).strip()
    return str(content or "").strip()


def _parse_reasoning_output(content: str) -> ReasoningEnvelope:
    stripped = re.sub(r"(?is)<think>.*?</think>", "", content or "").strip()
    stripped = re.sub(r"^```json\s*", "", stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"final diagnosis payload is not valid JSON: {content}")
        body = match.group(0)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            try:
                payload = ast.literal_eval(body)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"final diagnosis payload is not valid JSON: {content}") from exc
    return ReasoningEnvelope.model_validate(payload)


def _parse_final_output(content: str) -> FinalDiagnosisEnvelope:
    parsed = _parse_reasoning_output(content)
    if parsed.diagnosis is None:
        raise RuntimeError(f"final diagnosis payload does not contain diagnosis: {content}")
    return FinalDiagnosisEnvelope(
        thought=parsed.thought,
        diagnosis=parsed.diagnosis,
        remediation_plan=parsed.remediation_plan,
    )


def _safe_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_jsonable(item) for item in value]
    if isinstance(value, DiagnosisResult):
        return value.model_dump(mode="json")
    if isinstance(value, RemediationPlan):
        return value.model_dump(mode="json")
    if isinstance(value, Observation):
        return value.model_dump(mode="json")
    if isinstance(value, ThinkingStep):
        return value.model_dump(mode="json")
    if isinstance(value, ThinkingTrace):
        return {"steps": [item.to_dict() for item in value.steps]}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {str(k): _safe_jsonable(v) for k, v in vars(value).items()}
    return str(value)


def _normalize_diagnosis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    confidence = normalized.get("confidence")
    certainty = str(normalized.get("diagnosis_certainty", "")).strip().lower()
    if isinstance(confidence, (int, float)) and certainty == "confirmed" and float(confidence) < 0.85:
        normalized["diagnosis_certainty"] = "probable"
    normalized["hypotheses"] = _normalize_hypotheses_payload(normalized)
    return normalized


def _normalize_hypotheses_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_hypotheses = payload.get("hypotheses")
    root_cause = str(payload.get("root_cause") or "主要根因假设").strip()
    root_cause_layer = str(payload.get("root_cause_layer") or "platform").strip()
    impact_summary = str(payload.get("impact_summary") or "").strip()
    confidence = float(payload.get("confidence") or 0.5)
    certainty = str(payload.get("diagnosis_certainty") or "probable").strip().lower()

    normalized: list[dict[str, Any]] = []
    seen_descriptions: set[str] = set()
    if isinstance(raw_hypotheses, list):
        for item in raw_hypotheses:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            status = str(item.get("status") or "testing").strip().lower()
            if status not in {"testing", "confirmed", "eliminated"}:
                status = "testing"
            evidence_for = item.get("evidence_for")
            evidence_against = item.get("evidence_against")
            hypothesis = {
                "description": description,
                "status": status,
                "evidence_for": [str(v) for v in evidence_for] if isinstance(evidence_for, list) else [],
                "evidence_against": [str(v) for v in evidence_against] if isinstance(evidence_against, list) else [],
                "confidence": _clamp_confidence(item.get("confidence"), default=confidence),
            }
            normalized.append(hypothesis)
            seen_descriptions.add(description.lower())

    primary_status = "confirmed" if certainty == "confirmed" else "testing"
    defaults: list[dict[str, Any]] = [
        {
            "description": root_cause,
            "status": primary_status,
            "evidence_for": [impact_summary] if impact_summary else [],
            "evidence_against": [],
            "confidence": _clamp_confidence(confidence, default=0.9),
        },
        {
            "description": "网络或 RDMA 退化导致时延升高",
            "status": "eliminated" if root_cause_layer != "network" else "testing",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and root_cause_layer != "network" else [],
            "confidence": _clamp_confidence(min(confidence, 0.45), default=0.35),
        },
        {
            "description": "常规工作负载上涨或 service 侧饱和",
            "status": "testing" if root_cause_layer != "service" else primary_status,
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and root_cause_layer != "service" else [],
            "confidence": _clamp_confidence(min(confidence, 0.55), default=0.4),
        },
        {
            "description": "热降频或其他硬件侧不稳定",
            "status": "testing" if root_cause_layer == "hardware" else "eliminated",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and root_cause_layer != "hardware" else [],
            "confidence": _clamp_confidence(min(confidence, 0.4), default=0.3),
        },
    ]

    for item in defaults:
        key = item["description"].lower()
        if key in seen_descriptions:
            continue
        normalized.append(item)
        seen_descriptions.add(key)
        if len(normalized) >= 3:
            break

    return normalized[: max(3, len(normalized))]


def _clamp_confidence(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except Exception:  # noqa: BLE001
        numeric = default
    return max(0.0, min(1.0, numeric))


def _normalize_remediation_plan_payload(
    *,
    raw_plan: dict[str, Any] | None,
    diagnosis: DiagnosisResult,
    session_id: str,
    registry: ToolRegistry | None = None,
) -> RemediationPlan | None:
    if raw_plan is None:
        return None
    if not isinstance(raw_plan, dict):
        return None

    candidate = dict(raw_plan)
    steps = candidate.get("steps")
    if steps is None:
        steps = candidate.get("actions")
    normalized_steps: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            normalized_step = dict(step)
            normalized_step.setdefault("step_id", index)
            normalized_step.setdefault(
                "description",
                f"针对 {diagnosis.root_cause} 的候选修复步骤 {index}",
            )
            normalized_step.setdefault("params", {})
            invalid_reason = _normalize_step_params_in_place(
                normalized_step,
                diagnosis=diagnosis,
                registry=registry,
            )
            if invalid_reason:
                _llm_logger.warning(
                    "dropping invalid remediation plan for session %s at step %s: %s",
                    session_id,
                    normalized_step.get("step_id", index),
                    invalid_reason,
                )
                return None
            if not normalized_step.get("command"):
                tool_name = normalized_step.get("tool", "")
                step_params = normalized_step.get("params", {})
                if registry is not None:
                    try:
                        tool_def = registry.get_tool(tool_name)
                        normalized_step["command"] = tool_def.build_command(step_params)
                    except Exception:  # noqa: BLE001
                        normalized_step["command"] = f"{tool_name} {json.dumps(step_params or {}, ensure_ascii=False, sort_keys=True)}"
                else:
                    normalized_step["command"] = f"{tool_name} {json.dumps(step_params or {}, ensure_ascii=False, sort_keys=True)}"
            if normalized_step.get("verification") is None:
                normalized_step["verification"] = {
                    "method": "wait",
                    "wait_seconds": 30,
                }
            normalized_step.setdefault("timeout", 60)
            if normalized_step.get("rollback_tool") is None and normalized_step.get("rollback_params") is not None:
                normalized_step.pop("rollback_params", None)
            normalized_steps.append(normalized_step)

    candidate["steps"] = normalized_steps
    candidate.pop("actions", None)
    candidate.setdefault(
        "plan_id",
        f"proposal-{(session_id or 'session')[:8]}",
    )
    candidate.setdefault("root_cause", diagnosis.root_cause)
    candidate.setdefault(
        "description",
        "基于现有诊断证据生成的 proposal-only 修复方案，当前尚未执行任何写入动作。",
    )
    candidate.setdefault("estimated_impact", diagnosis.impact_summary)
    candidate.setdefault("confidence", _normalize_plan_confidence(diagnosis.confidence))
    candidate.setdefault("priority", _normalize_plan_priority(diagnosis.triage_priority))
    candidate.setdefault("safety_level", "high")

    try:
        return RemediationPlan.model_validate(candidate)
    except Exception as exc:  # noqa: BLE001
        _llm_logger.warning(
            "dropping remediation plan for session %s because validation failed: %s",
            session_id,
            exc,
        )
        return None


def _normalize_plan_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_plan_priority(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized in {"P0", "P1", "P2"}:
        return normalized
    return "P2"


def _normalize_step_params_in_place(
    step: dict[str, Any],
    *,
    diagnosis: DiagnosisResult,
    registry: ToolRegistry | None,
) -> str | None:
    tool_name = str(step.get("tool") or "").strip()
    params = step.get("params")
    if not isinstance(params, dict):
        return "missing_required_params: params must be an object"

    if tool_name == "k8s.delete_pod":
        pod_selector = params.pop("pod_selector", None)
        if pod_selector is not None and "label_selector" not in params:
            params["label_selector"] = pod_selector

    if _step_looks_like_tc_qdisc_cleanup(step) and tool_name != "network.clear_tc_qdisc":
        return "tool_not_supported_for_intended_action: tc_qdisc_cleanup_requires_network.clear_tc_qdisc"

    if tool_name == "network.clear_tc_qdisc":
        iface = params.get("iface")
        if not isinstance(iface, str) or not iface.strip():
            inferred_iface = _infer_tc_iface(step)
            if inferred_iface:
                params["iface"] = inferred_iface
        if not params.get("parent"):
            inferred_parent = _extract_token_from_step(step, token_name="parent")
            if inferred_parent:
                params["parent"] = inferred_parent
        if not params.get("handle"):
            inferred_handle = _extract_token_from_step(step, token_name="handle")
            if inferred_handle:
                params["handle"] = inferred_handle
        if not params.get("parent") and not params.get("handle") and not params.get("kind"):
            scope = _extract_tc_scope(step)
            if scope:
                params["kind"] = scope

    if registry is None or not tool_name:
        return None

    try:
        tool_def = registry.get_tool(tool_name)
    except Exception:
        return f"tool_not_supported_for_intended_action: unknown_tool={tool_name or '<blank>'}"

    required = tool_def.params_schema.get("required", [])
    if not isinstance(required, list):
        return None

    missing = [field for field in required if field not in params or _is_blank_param(params.get(field))]
    if "node" in missing:
        inferred_node, ambiguous = _infer_step_node(step=step, diagnosis=diagnosis)
        if ambiguous:
            return "node_inference_ambiguous"
        if inferred_node:
            params["node"] = inferred_node

    missing = [field for field in required if field not in params or _is_blank_param(params.get(field))]
    if missing:
        return f"missing_required_params: {missing}"
    return None


def _is_blank_param(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _step_looks_like_tc_qdisc_cleanup(step: dict[str, Any]) -> bool:
    text = " ".join(
        str(item or "").strip()
        for item in (
            step.get("description"),
            step.get("command"),
            json.dumps(step.get("params", {}), ensure_ascii=False, sort_keys=True),
        )
        if str(item or "").strip()
    ).lower()
    return "tc qdisc" in text or "netem" in text


def _infer_tc_iface(step: dict[str, Any]) -> str | None:
    match = re.search(r"\bdev\s+([a-zA-Z0-9_.:-]+)", _step_text(step), flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_token_from_step(step: dict[str, Any], *, token_name: str) -> str | None:
    pattern = rf"\b{re.escape(token_name)}\s+([a-zA-Z0-9_.:-]+)"
    match = re.search(pattern, _step_text(step), flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _extract_tc_scope(step: dict[str, Any]) -> str | None:
    text = _step_text(step).lower()
    for candidate in ("root", "ingress", "clsact"):
        if re.search(rf"\b{candidate}\b", text):
            return candidate
    return None


def _step_text(step: dict[str, Any]) -> str:
    parts = [
        str(step.get("description") or "").strip(),
        str(step.get("command") or "").strip(),
    ]
    params = step.get("params", {})
    if isinstance(params, dict):
        parts.append(json.dumps(params, ensure_ascii=False, sort_keys=True))
    return "\n".join(part for part in parts if part)


def _infer_step_node(*, step: dict[str, Any], diagnosis: DiagnosisResult) -> tuple[str | None, bool]:
    inventory_names, host_to_name = _load_inventory_node_mapping()
    node_candidates: set[str] = set()
    ip_candidates: set[str] = set()

    for raw in diagnosis.root_cause_entities:
        _collect_node_candidates(str(raw or ""), inventory_names, node_candidates, ip_candidates)

    text = _step_text(step)
    for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        ip_candidates.add(ip)
    for name in inventory_names:
        if name and name in text:
            node_candidates.add(name)

    resolved_nodes = {host_to_name[ip] for ip in ip_candidates if ip in host_to_name}
    all_candidates = set(node_candidates) | resolved_nodes
    if len(all_candidates) == 1:
        return next(iter(all_candidates)), False

    if len(all_candidates) > 1:
        return None, True

    if not inventory_names and len(node_candidates) == 1:
        return next(iter(node_candidates)), False
    return None, False


def _collect_node_candidates(
    raw: str,
    inventory_names: set[str],
    node_candidates: set[str],
    ip_candidates: set[str],
) -> None:
    value = raw.strip()
    if not value:
        return
    lower = value.lower()
    if lower.startswith("node:"):
        candidate = value.split(":", 1)[1].strip()
        if candidate:
            node_candidates.add(candidate)
        return
    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", value):
        ip_candidates.add(value)
        return
    if value in inventory_names:
        node_candidates.add(value)
        return
    if ":" in value:
        return
    if re.search(r"[a-zA-Z]", value) and re.search(r"[-\d]", value):
        node_candidates.add(value)


def _load_inventory_node_mapping() -> tuple[set[str], dict[str, str]]:
    names: set[str] = set()
    host_to_name: dict[str, str] = {}
    for path in _inventory_candidates():
        if not path.exists() or yaml is None:
            continue
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        workers = payload.get("inventory", {}).get("workers", [])
        if not isinstance(workers, list):
            continue
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            name = str(worker.get("name") or "").strip()
            ssh = worker.get("ssh")
            host = str(ssh.get("host") or "").strip() if isinstance(ssh, dict) else ""
            if name:
                names.add(name)
            if name and host:
                host_to_name[host] = name
        if names or host_to_name:
            break
    return names, host_to_name


def _inventory_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_inventory = os.getenv("SRE_SSH_INVENTORY_PATH", "").strip()
    if env_inventory:
        candidates.append(Path(env_inventory))

    config_candidates = [
        Path(os.getenv("SRE_AGENT_CONFIG", "").strip()) if os.getenv("SRE_AGENT_CONFIG", "").strip() else None,
        Path("sre_agent/conf/config.yaml"),
    ]
    for config_path in config_candidates:
        if config_path is None or not config_path.exists() or yaml is None:
            continue
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        raw_inventory_path = (
            payload.get("ontology", {})
            .get("discovery", {})
            .get("live_inventory_path")
        )
        if not raw_inventory_path:
            continue
        inventory_path = Path(str(raw_inventory_path))
        if not inventory_path.is_absolute():
            inventory_path = (config_path.parent.parent.parent / inventory_path).resolve() if not inventory_path.exists() else inventory_path
        candidates.append(inventory_path)

    candidates.append(Path("sre_agent/conf/live_inventory.lab.yaml"))

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        deduped.append(candidate)
        seen.add(key)
    return deduped
