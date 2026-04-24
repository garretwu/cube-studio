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

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage, ToolMessage, message_chunk_to_message, messages_to_dict
from pydantic import BaseModel, Field

from sre_agent.agent.checkpoint import persist_state_snapshot
from sre_agent.agent.prompts import build_system_prompt
from sre_agent.agent.state import SREAgentState
from sre_agent.models.diagnosis import DiagnosisResult, Observation, ThinkingStep, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
from sre_agent.runtime.node_mapping import load_inventory_node_mapping, normalize_node_identifier
from sre_agent.runtime.token_estimation import estimate_token_count
from sre_agent.ttft_process_policy import (
    TTFT_STRICT_PROCESS_FIND_PATTERN,
    extract_ttft_verification_pattern,
    is_ttft_suspect_process,
)
from sre_agent.tools import SafetyLevel, ToolExecutionContext, ToolRegistry, ToolResult

# LLM 交互日志记录器
_llm_logger = logging.getLogger("sre_agent.llm")
_llm_logger.setLevel(logging.DEBUG)
_llm_logger.addHandler(logging.NullHandler())  # default null handler to avoid warnings

LLM_LOG_DIR = Path("./data/llm_logs")
_TTFT_PROMETHEUS_TOTAL_BUDGET = 2
_TTFT_PROMETHEUS_FAMILY_BUDGET = 1


class FinalDiagnosisEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any]
    remediation_plan: dict[str, Any] | None = None


class ReasoningEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any] | None = None
    remediation_plan: dict[str, Any] | None = None


class ReasonStepTimeoutError(asyncio.TimeoutError):
    def __init__(self, *, retry_record: dict[str, Any]) -> None:
        super().__init__("reason step timed out")
        self.reason_timeout_retry = retry_record


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
    reasoning_context_strategy: str | None = None,
    reasoning_overflow_behavior: str | None = None,
    reasoning_input_target_tokens: int | None = None,
    reasoning_model_family: str | None = None,
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
        "reasoning_context_strategy": reasoning_context_strategy,
        "reasoning_overflow_behavior": reasoning_overflow_behavior,
        "reasoning_input_target_tokens": reasoning_input_target_tokens,
        "reasoning_model_family": reasoning_model_family,
        "evidence_signals": {},
        "loop_guard": {
            "recent_fingerprint": None,
            "repeat_count": 0,
            "threshold": 2,
            "triggered": False,
            "trigger_step": None,
        },
        "force_final_turn": False,
    }


def _log_tool_execution(
    *,
    session_id: str,
    step: int,
    tool_name: str,
    tool_args: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Write tool execution results to a log file.

    Log format: JSONL (one JSON object per line)
    Log path: ./data/llm_logs/{session_id}.jsonl
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
    """Write LLM interaction logs to file for debugging and analysis.

    Log format: JSONL (one JSON object per line)
    Log path: ./data/llm_logs/{session_id}.jsonl
    """
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LLM_LOG_DIR / f"{session_id}.jsonl"

        # 构建日志内容
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


def log_stream_lifecycle_event(
    *,
    session_id: str,
    step: int | None,
    mode: str,
    stage: str = "",
    status: str = "",
    reason: str = "",
    node: str = "",
    event_type: str = "",
    pending_tool_calls_count: int | None = None,
    step_count: int | None = None,
    max_steps: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write stream lifecycle diagnostics to the per-session JSONL log."""
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LLM_LOG_DIR / f"{session_id}.jsonl"
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": str(session_id or "").strip(),
            "step": int(step or 0),
            "mode": str(mode or "").strip(),
            "stage": str(stage or "").strip(),
            "status": str(status or "").strip(),
            "reason": str(reason or "").strip(),
            "node": str(node or "").strip(),
            "event_type": str(event_type or "").strip(),
            "pending_tool_calls_count": (
                int(pending_tool_calls_count) if pending_tool_calls_count is not None else None
            ),
            "step_count": int(step_count) if step_count is not None else None,
            "max_steps": int(max_steps) if max_steps is not None else None,
            "extra": _safe_jsonable(extra or {}),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        _llm_logger.exception("Failed to log stream lifecycle event")


async def _invoke_llm_message(llm: Any, messages: list[Any], *, timeout: float) -> AIMessage:
    async def _run() -> AIMessage:
        stream = getattr(llm, "astream", None)
        if callable(stream):
            accumulated: AIMessageChunk | None = None
            fallback_text_parts: list[str] = []
            async for chunk in stream(messages):
                if isinstance(chunk, AIMessage):
                    return chunk
                if isinstance(chunk, AIMessageChunk):
                    accumulated = chunk if accumulated is None else accumulated + chunk
                    continue
                text = _extract_text(getattr(chunk, "content", chunk))
                if text:
                    fallback_text_parts.append(text)

            if accumulated is not None:
                message = message_chunk_to_message(accumulated)
                if isinstance(message, AIMessage):
                    return message
                return AIMessage(content=_extract_text(getattr(message, "content", message)))
            if fallback_text_parts:
                return AIMessage(content="".join(fallback_text_parts))

        invoke = getattr(llm, "ainvoke", None)
        if not callable(invoke):
            raise RuntimeError(f"LLM object {type(llm).__name__} does not support ainvoke or astream")
        response = await invoke(messages)
        if not isinstance(response, AIMessage):
            raise RuntimeError(f"expected AIMessage from LLM, got {type(response).__name__}")
        return response

    return await asyncio.wait_for(_run(), timeout=timeout)


def _extract_reason_prompt_metadata_snapshot(prompt_metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_mode": str(prompt_metadata.get("prompt_mode", "") or "").strip() or None,
        "estimated_input_tokens": int(prompt_metadata.get("estimated_input_tokens") or 0),
        "reasoning_context_strategy": str(prompt_metadata.get("reasoning_context_strategy", "") or "").strip() or None,
        "reasoning_overflow_behavior": str(prompt_metadata.get("reasoning_overflow_behavior", "") or "").strip() or None,
        "reasoning_input_target_tokens": int(prompt_metadata.get("reasoning_input_target_tokens") or 0),
        "prompt_fallback_used": bool(prompt_metadata.get("prompt_fallback_used")),
    }


def _build_reason_timeout_retry_record(
    *,
    retry_count: int,
    timeout_sec: float,
    previous_prompt_metadata: dict[str, Any],
    retry_prompt_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "retry_count": max(0, int(retry_count)),
        "timeout_sec": float(timeout_sec),
        "previous_prompt_metadata": _extract_reason_prompt_metadata_snapshot(previous_prompt_metadata),
        "retry_prompt_metadata": _extract_reason_prompt_metadata_snapshot(retry_prompt_metadata),
    }


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

    active_skill_id, active_skill_content = _extract_active_skill_guidance(
        [dict(item) for item in list(state.get("tool_runs", []) or []) if isinstance(item, dict)]
    )
    system_prompt = build_system_prompt(
        registry,
        allowed_tool_names=state.get("allowed_tool_names"),
        active_skill_id=active_skill_id or None,
        active_skill_content=active_skill_content or None,
    )
    messages = list(_coerce_messages(state.get("messages", [])))
    if not messages:
        messages = [HumanMessage(content=state["query"])]

    invoked_messages: list[Any] = []
    prompt_metadata: dict[str, Any] = {}
    prompt_fallback_used = False
    bound_tool_names: list[str] = []
    tool_choice = "auto"
    step_index = state.get("step_count", 0) + 1
    timeout_retry_record: dict[str, Any] | None = None
    final_turn = bool(state.get("force_final_turn", False)) or (
        bool(state.get("tool_runs")) and state.get("step_count", 0) >= max(state.get("max_steps", 10) - 1, 1)
    )
    llm_supports_message_invocation = hasattr(llm, "ainvoke") or hasattr(llm, "astream")
    interaction_mode = "final_json" if final_turn else "tool_bound"

    async def _invoke_reason_with_timeout_retry(call_model: Any) -> AIMessage:
        nonlocal invoked_messages
        nonlocal prompt_metadata
        nonlocal prompt_fallback_used
        nonlocal interaction_mode
        nonlocal tool_choice
        nonlocal bound_tool_names
        nonlocal timeout_retry_record

        step_timeout_sec = float(state["step_timeout_sec"])
        try:
            return await _invoke_llm_message(call_model, invoked_messages, timeout=step_timeout_sec)
        except asyncio.TimeoutError:
            if not llm_supports_message_invocation:
                raise

            retry_messages, retry_prompt_metadata = _build_transcript_compact_reason_messages(
                system_prompt=system_prompt,
                state=state,
                final_turn=True,
                tool_binding_fallback=True,
            )
            retry_prompt_metadata = {
                **retry_prompt_metadata,
                "reasoning_context_strategy": "transcript_compact",
                "reasoning_overflow_behavior": "compact",
                "reason_timeout_retry": True,
                "reason_timeout_retry_count": 1,
                "reason_timeout_retry_timeout_sec": step_timeout_sec,
                "reason_timeout_retry_trigger_mode": interaction_mode,
                "reason_timeout_retry_trigger_tool_choice": tool_choice,
            }
            timeout_retry_record = _build_reason_timeout_retry_record(
                retry_count=1,
                timeout_sec=step_timeout_sec,
                previous_prompt_metadata=prompt_metadata,
                retry_prompt_metadata=retry_prompt_metadata,
            )
            try:
                retry_response = await _invoke_llm_message(llm, retry_messages, timeout=step_timeout_sec)
            except asyncio.TimeoutError as retry_exc:
                raise ReasonStepTimeoutError(retry_record=timeout_retry_record) from retry_exc

            invoked_messages = retry_messages
            prompt_metadata = retry_prompt_metadata
            prompt_fallback_used = bool(prompt_metadata.get("prompt_fallback_used"))
            interaction_mode = "timeout_retry_final_compact"
            tool_choice = "none"
            bound_tool_names = []
            return retry_response

    if final_turn and llm_supports_message_invocation:
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
        response = await _invoke_reason_with_timeout_retry(llm)
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
            tool_names=_select_bound_tool_names_for_turn(state),
        )
        bound_tool_names = [str(getattr(tool, "name", "")) for tool in bound_tools]
        tool_choice = "required" if not state.get("tool_runs") else "auto"
        try:
            call_model = llm.bind_tools(
                bound_tools,
                tool_choice=tool_choice,
            )
            response = await _invoke_reason_with_timeout_retry(call_model)
        except Exception as exc:  # noqa: BLE001
            if llm_supports_message_invocation and _is_reason_prompt_empty_error(exc):
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
                    response = await _invoke_reason_with_timeout_retry(call_model)
                except Exception as retry_exc:  # noqa: BLE001
                    exc = retry_exc
            if not llm_supports_message_invocation or not (
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
            response = await _invoke_reason_with_timeout_retry(llm)
    if not isinstance(response, AIMessage):
        raise RuntimeError(f"expected AIMessage from LLM, got {type(response).__name__}")

    updated_messages = [*messages, response]
    updated_trace = list(state.get("trace_items", []))
    updated_interactions = list(state.get("llm_interactions", []))
    if timeout_retry_record is not None:
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": "reason step hit timeout; retried once with compact final prompt",
                "action": "conclude",
                "confidence": None,
                "tool_params": {
                    "kind": "reason_timeout_retry",
                    **_safe_jsonable(timeout_retry_record),
                },
            }
        )
    pending_tool_calls = _auto_load_high_score_skill(
        state,
        list(response.tool_calls or []),
    )
    pending_tool_calls = _auto_run_single_script_skill(
        state,
        pending_tool_calls,
    )
    pending_tool_calls = _auto_follow_loaded_skill_recommended_tools(
        state,
        pending_tool_calls,
    )
    # 忽略无参数的 tool_call，避免模型给出空 args 导致无效执行循环。
    dropped_empty_arg_calls = [
        call
        for call in pending_tool_calls
        if not isinstance(call.get("args"), dict) or len(call.get("args") or {}) == 0
    ]
    if dropped_empty_arg_calls:
        _llm_logger.info(
            "Ignoring %d tool_calls with empty/missing args: %s",
            len(dropped_empty_arg_calls),
            [str(call.get("name", "")).strip() for call in dropped_empty_arg_calls],
        )
        pending_tool_calls = [
            call
            for call in pending_tool_calls
            if isinstance(call.get("args"), dict) and len(call.get("args") or {}) > 0
        ]
    raw_response_text = _extract_text(response.content)
    forced_trace_tool_call_emitted = False

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

    # 检测诊断完成信号：如果 LLM 明确表示诊断已完成，忽略后续 tool_calls
    # 使用正则匹配诊断完成的关键表达
    _DIAGNOSIS_COMPLETE_PATTERN = re.compile(
        r"(诊断结论|诊断结果|诊断)(已经|已)?明确|"
        r"(诊断|根因)(已经|已)?(很|非常)?清晰|"
        r"(诊断|根因)(已经|已)?(很|非常)?清楚|"
        r"诊断(已经|已)?完成|"
        r"(基于|根据)(已有|已收集)?证据(，|,)?(完成|结束)?诊断|"
        r"(可以|现在|即将)(给出|提供)?最终诊断|"
        r"最终诊断(结论|结果)?|"
        r"无需(进一步|更多|继续)?(证据|信息)?收集|"
        r"(证据|信息)收集(已经|已)?完成|"
        r"(no\s+need|not\s+need)\s+(for\s+)?(further|additional|more)\s+(evidence|information|data)|"
        r"(diagnosis|diagnostic)(\s+is|\s+has)?\s+(complete|concluded|final|clear)|"
        r"final\s+diagnosis",
        re.IGNORECASE,
    )
    diagnosis_complete_detected = False
    if raw_response_text:
        if _DIAGNOSIS_COMPLETE_PATTERN.search(raw_response_text):
            diagnosis_complete_detected = True

    parsed_final_json: ReasoningEnvelope | None = None
    # 在 final_json 轮，如果已经能解析出 diagnosis/remediation，则忽略后续工具调用并直接收敛。
    if interaction_mode == "final_json" and raw_response_text:
        try:
            candidate = _parse_reasoning_output(raw_response_text)
        except Exception:  # noqa: BLE001
            candidate = _build_fallback_reasoning_output(
                query=str(state.get("query", "")).strip(),
                content=raw_response_text,
            )
        if candidate.diagnosis is not None or candidate.remediation_plan is not None:
            parsed_final_json = candidate
            if pending_tool_calls:
                _llm_logger.info(
                    "Final JSON parse succeeded; ignoring %d trailing tool_calls and finalizing directly",
                    len(pending_tool_calls),
                )
                pending_tool_calls = []

    # 如果检测到诊断完成信号，忽略 tool_calls，直接进入 finalize
    if diagnosis_complete_detected and pending_tool_calls:
        # TTFT sessions often include a "final verification" tool call (for example
        # prometheus.query_instant). Do not drop those calls just because the model
        # used a completion-like phrase in natural language.
        if _is_ttft_alert_state(state):
            _llm_logger.info(
                "Diagnosis complete signal detected but TTFT tool calls are preserved: %d",
                len(pending_tool_calls),
            )
        else:
            _llm_logger.info(
                "Diagnosis complete signal detected, ignoring %d tool_calls",
                len(pending_tool_calls),
            )
            pending_tool_calls = []

    if pending_tool_calls and _is_ttft_alert_state(state):
        tool_runs = list(state.get("tool_runs", []) or [])
        coverage_met, coverage_missing = _evaluate_ttft_min_coverage(
            state=state,
            tool_runs=tool_runs,
        )
        if not coverage_met:
            forced_calls: list[dict[str, Any]] = []
            pending_names = {
                str(item.get("name", "")).strip()
                for item in pending_tool_calls
                if isinstance(item, dict)
            }
            variables = dict(state.get("variables", {}) or {})
            alert_snapshot = state.get("alert_snapshot")
            snapshot = alert_snapshot if isinstance(alert_snapshot, dict) else {}
            if "gpu_processes" in coverage_missing and "gpu.get_processes" not in pending_names:
                probe_node = (
                    str(variables.get("node") or "").strip()
                    or str(variables.get("node_ip") or "").strip()
                    or str(snapshot.get("labels", {}).get("node", "") if isinstance(snapshot.get("labels"), dict) else "").strip()
                )
                if probe_node:
                    forced_calls.append(
                        {
                            "name": "gpu.get_processes",
                            "args": {"node": probe_node},
                            "id": f"ttft-forced-gpu-processes-{step_index}",
                        }
                    )
            if "external_process_find" in coverage_missing and "process.find" not in pending_names:
                ext_node = _get_ttft_external_node(state)
                find_args: dict[str, Any] = {"pattern": TTFT_STRICT_PROCESS_FIND_PATTERN}
                if ext_node:
                    find_args["node"] = ext_node
                forced_calls.append(
                    {
                        "name": "process.find",
                        "args": find_args,
                        "id": f"ttft-forced-process-find-{step_index}",
                    }
                )
            if forced_calls:
                pending_tool_calls = forced_calls
                updated_trace.append(
                    {
                        "type": "thought",
                        "step": step_index,
                        "content": "TTFT 覆盖未完成，优先执行 GPU 进程与外部负载取证。",
                        "action": "tool_call",
                        "tool_name": pending_tool_calls[0]["name"],
                        "tool_params": _render_trace_tool_params(
                            state=state,
                            tool_name=str(pending_tool_calls[0].get("name", "") or "").strip(),
                            tool_args=pending_tool_calls[0].get("args", {}),
                        ),
                        "confidence": None,
                        "meta": {
                            "ttft_coverage_met": False,
                            "coverage_missing": coverage_missing,
                        },
                    }
                )
                forced_trace_tool_call_emitted = True

    if pending_tool_calls:
        if not forced_trace_tool_call_emitted:
            updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": raw_response_text or "正在调用只读工具补充诊断证据。",
                "action": "tool_call",
                "tool_name": pending_tool_calls[0]["name"],
                "tool_params": _render_trace_tool_params(
                    state=state,
                    tool_name=str(pending_tool_calls[0].get("name", "") or "").strip(),
                    tool_args=pending_tool_calls[0].get("args", {}),
                ),
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

    if parsed_final_json is not None:
        parsed = parsed_final_json
    else:
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
    final_thought = parsed.thought
    raw_remediation_plan = parsed.remediation_plan
    evidence_signals = _extract_evidence_signals(list(state.get("tool_runs", []) or []))
    diagnosis_payload = _normalize_diagnosis_payload(parsed.diagnosis)
    if _is_ttft_alert_state(state):
        _enrich_ttft_root_causes_from_evidence(diagnosis_payload, evidence_signals)

    tc_strong_evidence = bool(
        evidence_signals.get("tc_netem_present", False) or evidence_signals.get("tc_process_present", False)
    )
    if tc_strong_evidence and not _diagnosis_mentions_tc_evidence(diagnosis_payload):
        correction_messages = _build_tc_consistency_retry_messages(
            system_prompt=system_prompt,
            query=str(state.get("query", "") or "").strip(),
            evidence_signals=evidence_signals,
            diagnosis_payload=diagnosis_payload,
        )
        correction_prompt_stats = _build_prompt_message_stats(correction_messages)
        correction_response: AIMessage | None = None
        correction_raw_response_text = ""
        correction_error = ""
        corrected_payload: dict[str, Any] | None = None
        corrected_remediation_plan: dict[str, Any] | None = None
        corrected_thought = ""
        if llm_supports_message_invocation:
            try:
                retry_response = await _invoke_llm_message(llm, correction_messages, timeout=state["step_timeout_sec"])
                if isinstance(retry_response, AIMessage):
                    correction_response = retry_response
                    correction_raw_response_text = _extract_text(retry_response.content)
                    correction_parsed = _parse_reasoning_output(correction_raw_response_text)
                    if correction_parsed.diagnosis is not None:
                        corrected_payload = _normalize_diagnosis_payload(correction_parsed.diagnosis)
                    corrected_remediation_plan = correction_parsed.remediation_plan
                    corrected_thought = correction_parsed.thought
                else:
                    correction_raw_response_text = _extract_text(getattr(retry_response, "content", retry_response))
            except Exception as exc:  # noqa: BLE001
                correction_error = _normalized_exception_message(exc)

        _log_llm_interaction(
            session_id=str(state.get("session_id", "unknown")),
            step=step_index,
            prompt_messages=correction_messages,
            response=correction_response,
            mode="tc_consistency_retry",
            tool_choice="none",
            tool_calls=[],
            prompt_message_stats=correction_prompt_stats,
            prompt_fallback_used=False,
            prompt_metadata={
                "evidence_signals": _safe_jsonable(evidence_signals),
                "correction_error": correction_error or None,
                "provider_invocation_skipped": not llm_supports_message_invocation,
            },
        )
        updated_interactions.append(
            {
                "step": step_index,
                "mode": "tc_consistency_retry",
                "tool_choice": "none",
                "bound_tool_names": [],
                "prompt_messages": messages_to_dict(correction_messages),
                "prompt_message_stats": correction_prompt_stats,
                "prompt_fallback_used": False,
                "prompt_metadata": {
                    "evidence_signals": _safe_jsonable(evidence_signals),
                    "correction_error": correction_error or None,
                    "provider_invocation_skipped": not llm_supports_message_invocation,
                },
                "response_message": messages_to_dict([correction_response])[0] if correction_response is not None else None,
                "raw_response_text": correction_raw_response_text,
                "tool_calls": [],
            }
        )

        if corrected_payload is not None and _diagnosis_mentions_tc_evidence(corrected_payload):
            diagnosis_payload = corrected_payload
            if corrected_remediation_plan is not None:
                raw_remediation_plan = corrected_remediation_plan
            if corrected_thought:
                final_thought = corrected_thought
        else:
            diagnosis_payload = _normalize_diagnosis_payload(
                _build_tc_fallback_diagnosis_payload(
                    original=diagnosis_payload,
                    evidence_signals=evidence_signals,
                    tool_runs=list(state.get("tool_runs", []) or []),
                )
            )
            final_thought = f"{final_thought}\n已根据 tc/netem 强证据执行一致性纠偏。"

    diagnosis = DiagnosisResult.model_validate(diagnosis_payload)
    remediation_plan = _normalize_remediation_plan_payload(
        raw_plan=raw_remediation_plan,
        diagnosis=diagnosis,
        session_id=str(state.get("session_id", "")),
        registry=registry,
        tool_runs=list(state.get("tool_runs", []) or []),
        variables=dict(state.get("variables", {}) or {}),
        alert_name=_get_alert_name_from_state(state),
    )
    plan_missing_reason: str | None = None
    if remediation_plan is None:
        plan_completion_messages = _build_plan_completion_messages(
            system_prompt=system_prompt,
            query=str(state.get("query", "") or "").strip(),
            diagnosis_payload=diagnosis_payload,
            tool_runs=list(state.get("tool_runs", []) or []),
        )
        plan_completion_stats = _build_prompt_message_stats(plan_completion_messages)
        plan_completion_response: AIMessage | None = None
        plan_completion_raw_response_text = ""
        plan_completion_error = ""
        completion_raw_plan: dict[str, Any] | None = None

        if llm_supports_message_invocation:
            try:
                retry_response = await _invoke_llm_message(llm, plan_completion_messages, timeout=state["step_timeout_sec"])
                if isinstance(retry_response, AIMessage):
                    plan_completion_response = retry_response
                    plan_completion_raw_response_text = _extract_text(retry_response.content)
                    completion_parsed = _parse_reasoning_output(plan_completion_raw_response_text)
                    completion_raw_plan = completion_parsed.remediation_plan
                else:
                    plan_completion_raw_response_text = _extract_text(getattr(retry_response, "content", retry_response))
            except Exception as exc:  # noqa: BLE001
                plan_completion_error = _normalized_exception_message(exc)

        _log_llm_interaction(
            session_id=str(state.get("session_id", "unknown")),
            step=step_index,
            prompt_messages=plan_completion_messages,
            response=plan_completion_response,
            mode="plan_completion_retry",
            tool_choice="none",
            tool_calls=[],
            prompt_message_stats=plan_completion_stats,
            prompt_fallback_used=False,
            prompt_metadata={
                "top_candidate": _safe_jsonable(_select_primary_root_cause_item(diagnosis_payload) or {}),
                "provider_invocation_skipped": not llm_supports_message_invocation,
                "plan_completion_error": plan_completion_error or None,
            },
        )
        updated_interactions.append(
            {
                "step": step_index,
                "mode": "plan_completion_retry",
                "tool_choice": "none",
                "bound_tool_names": [],
                "prompt_messages": messages_to_dict(plan_completion_messages),
                "prompt_message_stats": plan_completion_stats,
                "prompt_fallback_used": False,
                "prompt_metadata": {
                    "top_candidate": _safe_jsonable(_select_primary_root_cause_item(diagnosis_payload) or {}),
                    "provider_invocation_skipped": not llm_supports_message_invocation,
                    "plan_completion_error": plan_completion_error or None,
                },
                "response_message": messages_to_dict([plan_completion_response])[0] if plan_completion_response is not None else None,
                "raw_response_text": plan_completion_raw_response_text,
                "tool_calls": [],
            }
        )

        if completion_raw_plan is not None:
            remediation_plan = _normalize_remediation_plan_payload(
                raw_plan=completion_raw_plan,
                diagnosis=diagnosis,
                session_id=str(state.get("session_id", "")),
                registry=registry,
                tool_runs=list(state.get("tool_runs", []) or []),
                variables=dict(state.get("variables", {}) or {}),
                alert_name=_get_alert_name_from_state(state),
            )
            if remediation_plan is not None:
                diagnosis = diagnosis.model_copy(update={"recommended_fix": remediation_plan})
                final_thought = (
                    f"{final_thought}\n已基于主根因补全 proposal-only 修复方案，等待人工审批。"
                )
            else:
                plan_missing_reason = "诊断已完成，但自动补全修复方案未通过参数/安全校验。"
        else:
            if plan_completion_error:
                plan_missing_reason = f"诊断已完成，但自动补全修复方案失败：{plan_completion_error}"
            else:
                plan_missing_reason = "诊断已完成，但模型未返回可执行修复方案。"
    if _is_ttft_alert_state(state):
        auto_ttft_plan = _build_ttft_kill_process_plan_candidate(
            diagnosis=diagnosis,
            evidence_signals=evidence_signals,
            session_id=str(state.get("session_id", "")),
            tool_runs=list(state.get("tool_runs", []) or []),
            variables=dict(state.get("variables", {}) or {}),
        )
        if auto_ttft_plan is not None:
            auto_normalized = _normalize_remediation_plan_payload(
                raw_plan=auto_ttft_plan,
                diagnosis=diagnosis,
                session_id=str(state.get("session_id", "")),
                registry=registry,
                tool_runs=list(state.get("tool_runs", []) or []),
                variables=dict(state.get("variables", {}) or {}),
                alert_name=_get_alert_name_from_state(state),
            )
            if auto_normalized is not None:
                remediation_plan = auto_normalized
                plan_missing_reason = None
                diagnosis = diagnosis.model_copy(update={"recommended_fix": remediation_plan})
                final_thought = (
                    f"{final_thought}\n已根据 GPU 进程证据生成进程级灰度修复方案（需审批后执行）。"
                )

    # ── TTFT forced external node probe (fail-safe) ──
    # 只要 TTFT 最小取证覆盖未满足（尤其 external process.find 缺失），
    # 即注入 process.find 并继续诊断，不依赖 remediation_plan 是否已生成。
    if _is_ttft_alert_state(state):
        diagnosis, remediation_plan = _attach_per_root_cause_recommended_fixes(
            diagnosis=diagnosis,
            primary_plan=remediation_plan,
            evidence_signals=evidence_signals,
            session_id=str(state.get("session_id", "")),
            registry=registry,
            tool_runs=list(state.get("tool_runs", []) or []),
            variables=dict(state.get("variables", {}) or {}),
            alert_name=_get_alert_name_from_state(state),
        )
    if _is_ttft_alert_state(state) and not state.get("_ttft_external_probe_injected"):
        _tool_runs = list(state.get("tool_runs", []) or [])
        coverage_met, coverage_missing = _evaluate_ttft_min_coverage(
            state=state,
            tool_runs=_tool_runs,
        )
        _ext_node = _get_ttft_external_node(state)
        if (
            not coverage_met
            and "external_process_find" in coverage_missing
            and _ext_node
            and not _has_probed_ttft_external_node(_tool_runs, _ext_node)
        ):
            updated_trace.append(
                {
                    "type": "thought",
                    "step": step_index,
                    "content": (
                        f"TTFT 证据覆盖未完成（缺少 external process.find），"
                        f"强制探测外部压测源节点 {_ext_node}。"
                    ),
                    "action": "tool_call",
                    "confidence": None,
                    "tool_params": {
                        "ttft_coverage_met": False,
                        "coverage_missing": coverage_missing,
                        "force_canary_reason": "ttft_match",
                    },
                }
            )
            forced_call = {
                "name": "process.find",
                "args": {"pattern": TTFT_STRICT_PROCESS_FIND_PATTERN},
                "id": f"ttft-forced-external-probe-{step_index}",
            }
            updated = {
                **state,
                "messages": updated_messages,
                "llm_interactions": updated_interactions,
                "trace_items": updated_trace,
                "pending_tool_calls": [forced_call],
                "step_count": step_index,
                "diagnosis_result": None,
                "remediation_plan": None,
                "status": "running",
                "summary": None,
                "error": None,
                "evidence_signals": evidence_signals,
                "_ttft_external_probe_injected": True,
            }
            persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
            return updated

    if _is_ttft_alert_state(state):
        tool_runs = list(state.get("tool_runs", []) or [])
        variables = dict(state.get("variables", {}) or {})
        external_node = _get_ttft_external_node(state)
        auth_error = _find_ttft_external_probe_auth_error(tool_runs, external_node)
        blocked_reason = str(variables.get("ttft_external_probe_blocked_reason", "") or "").strip()
        if blocked_reason or auth_error:
            reason_text = blocked_reason or (
                f"SSH precheck failed for TTFT external node {external_node}: {auth_error}"
            )
            diagnosis_payload = _normalize_diagnosis_payload(
                {
                    "root_cause": (
                        f"外部压测源节点 {external_node} SSH 认证失败，无法完成 process.find 取证，"
                        "当前诊断证据不足。"
                    ),
                    "root_cause_layer": "platform",
                    "root_cause_entities": [external_node] if external_node else [],
                    "confidence": 0.45,
                    "hypotheses": [
                        {
                            "description": (
                                f"外部压测源节点 {external_node} 因 SSH 认证失败无法执行 process.find，"
                                "导致关键证据链缺失。"
                            ),
                            "status": "testing",
                            "evidence_for": [reason_text],
                            "evidence_against": [],
                            "confidence": 0.45,
                        }
                    ],
                    "impact_summary": (
                        f"TTFT 诊断被外部压测源节点 {external_node or 'unknown'} 的 SSH 认证失败阻断，"
                        "不能将其判定为“未发现可疑进程”。"
                    ),
                    "affected_services": [],
                    "triage_priority": "P1",
                    "diagnosis_certainty": "ambiguous",
                }
            )
            # Force canonical output contract for this fallback branch:
            # even if prompt text above used transitional keys, we converge to root_cause[] here.
            diagnosis_payload["root_cause"] = [
                {
                    "id": "rc-ttft-external-ssh-auth",
                    "title": (
                        f"External probe node {external_node or 'unknown'} failed SSH authentication, "
                        "so process evidence collection is blocked."
                    ),
                    "layer": "platform",
                    "entities": [external_node] if external_node else [],
                    "confidence": 0.45,
                    "certainty": "ambiguous",
                    "status": "suspected",
                    "evidence_summary": reason_text,
                    "impact_summary": str(diagnosis_payload.get("impact_summary") or "").strip(),
                    "distinguishing_verification": "Restore SSH access and rerun process.find evidence collection.",
                    "recommended_fix": None,
                }
            ]
            diagnosis_payload.pop("root_cause_layer", None)
            diagnosis_payload.pop("root_cause_entities", None)
            diagnosis = DiagnosisResult.model_validate(diagnosis_payload)
            remediation_plan = None
            plan_missing_reason = f"TTFT external probe blocked by SSH authentication failure: {reason_text}"
            final_thought = (
                f"{final_thought}\n外部压测源 SSH 认证失败，已将本轮结论降级为“证据不足”，"
                "并阻断“未发现可疑进程”结论。"
            )

    if remediation_plan is not None:
        diagnosis = diagnosis.model_copy(update={"recommended_fix": remediation_plan})
    updated_trace.append(
        {
            "type": "thought",
            "step": step_index,
            "content": final_thought,
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
        "evidence_signals": evidence_signals,
        "force_final_turn": False,
        "plan_missing_reason": plan_missing_reason,
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


def _select_bound_tool_names_for_turn(state: SREAgentState) -> list[str] | None:
    allowed_tool_names = state.get("allowed_tool_names")

    # 1) If state already carries an explicit allowed list, honour it first.
    if allowed_tool_names:
        names = [str(name).strip() for name in allowed_tool_names if str(name).strip()]
        if names:
            return names

    # 2) First turn (no tool_runs yet) — allow pending tool calls if present.
    if not state.get("tool_runs"):
        pending = state.get("pending_tool_calls") or []
        if pending:
            pending_names = list({
                str(c.get("name", "")).strip()
                for c in pending
                if isinstance(c, dict) and str(c.get("name", "")).strip()
            })
            if pending_names:
                return pending_names
        # Truly first turn with no pending — fall back to skills.list_skills only.
        return ["skills.list_skills"]

    # 3) Subsequent turns with no explicit allowed list — unrestrict (None).
    return None


def _find_latest_successful_skill_listing(tool_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in reversed(tool_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "skills.list_skills":
            continue
        if not bool(run.get("success", False)):
            continue
        data = run.get("data")
        if not isinstance(data, dict):
            continue
        skills = data.get("skills")
        if isinstance(skills, list) and skills:
            return run
    return None


_AUTO_LOAD_SKILL_MATCH_THRESHOLD = 0.6
_SKILL_LIST_COOLDOWN_STEPS = 6


def _choose_skill_id_to_load_from_listing(listing_run: dict[str, Any], tool_runs: list[dict[str, Any]]) -> str:
    data = listing_run.get("data")
    if not isinstance(data, dict):
        return ""
    skills = data.get("skills")
    if not isinstance(skills, list):
        return ""

    attempted = {
        str((run.get("params") or {}).get("skill_id") or "").strip()
        for run in tool_runs
        if isinstance(run, dict) and str(run.get("tool", "")).strip() == "skills.load_skill"
    }

    for item in skills:
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "").strip()
        if not skill_id or skill_id in attempted:
            continue
        try:
            match_score = float(item.get("match_score", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            match_score = 0.0
        if match_score < _AUTO_LOAD_SKILL_MATCH_THRESHOLD:
            continue
        return skill_id
    return ""


def _auto_load_high_score_skill(
    state: SREAgentState,
    pending_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(
        str(call.get("name", "")).strip() in {"skills.load_skill", "skills.read_skill_ref", "skills.run_skill"}
        for call in pending_tool_calls
        if isinstance(call, dict)
    ):
        return pending_tool_calls

    tool_runs = [dict(item) for item in list(state.get("tool_runs", []) or []) if isinstance(item, dict)]
    latest_listing = _find_latest_successful_skill_listing(tool_runs)
    if latest_listing is None:
        return pending_tool_calls

    skill_id = _choose_skill_id_to_load_from_listing(latest_listing, tool_runs)
    if not skill_id:
        return pending_tool_calls

    call_id = ""
    if pending_tool_calls:
        call_id = str((pending_tool_calls[0] or {}).get("id", "")).strip()
    call_id = call_id or "call-skill-load"
    return [
        {
            "name": "skills.load_skill",
            "args": {"skill_id": skill_id},
            "id": call_id,
            "type": "tool_call",
        }
    ]


def _find_latest_successful_skill_load(tool_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in reversed(tool_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "skills.load_skill":
            continue
        if not bool(run.get("success", False)):
            continue
        data = run.get("data")
        if isinstance(data, dict):
            return run
    return None


def _extract_active_skill_guidance(tool_runs: list[dict[str, Any]]) -> tuple[str, str]:
    for run in reversed(tool_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "skills.load_skill":
            continue
        if not bool(run.get("success", False)):
            continue
        key_fields = run.get("key_fields")
        if not isinstance(key_fields, dict):
            key_fields = {}
        data = run.get("data")
        if not isinstance(data, dict):
            data = {}

        skill_id = str(
            key_fields.get("skill_id")
            or data.get("skill_id")
            or (run.get("params") or {}).get("skill_id")
            or ""
        ).strip()
        content = str(key_fields.get("content") or data.get("content") or "").strip()
        if content:
            return skill_id, content
    return "", ""


def _choose_skill_script_to_run(load_run: dict[str, Any], tool_runs: list[dict[str, Any]]) -> tuple[str, str]:
    data = load_run.get("data")
    if not isinstance(data, dict):
        return "", ""

    skill_id = str(data.get("skill_id") or "").strip() or str((load_run.get("params") or {}).get("skill_id") or "").strip()
    scripts = data.get("scripts")
    if not skill_id or not isinstance(scripts, list) or len(scripts) != 1:
        return "", ""

    script = str(scripts[0] or "").strip()
    if not script:
        return "", ""

    already_ran = any(
        isinstance(run, dict)
        and str(run.get("tool", "")).strip() == "skills.run_skill"
        and str((run.get("params") or {}).get("skill_id") or "").strip() == skill_id
        and str((run.get("params") or {}).get("script") or "").strip() == script
        for run in tool_runs
    )
    if already_ran:
        return "", ""
    return skill_id, script


def _extract_recommended_tool_calls_from_skill_content(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not str(content or "").strip():
        return calls

    for snippet in re.findall(r"`([^`]+)`", content):
        text = str(snippet or "").strip()
        if not text:
            continue
        match = re.match(r"^([a-z0-9_]+\.[a-z0-9_]+)(?:\((.*)\))?$", text)
        if not match:
            continue
        tool_name = str(match.group(1) or "").strip()
        if not tool_name or tool_name == "skills.run_skill":
            continue

        args: dict[str, Any] = {}
        raw_args = str(match.group(2) or "").strip()
        if raw_args:
            for key, value in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"', raw_args):
                args[str(key).strip()] = value
            for key, value in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'([^']*)'", raw_args):
                args[str(key).strip()] = value

        calls.append({"name": tool_name, "args": args})
    return calls


def _interpolate_params(params: dict[str, Any], variables: dict[str, Any] | None) -> dict[str, Any]:
    """Interpolate ${variable} syntax in params dict with values from variables.

    Supports:
    - ${host_ip} -> variables.host_ip
    - ${labels.instance} -> variables.labels.instance
    - ${gpu_uuid} -> variables.gpu_uuid
    """
    if not params:
        return {}
    if not variables:
        return dict(params)

    result: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            # Extract variable path: ${host_ip} -> "host_ip"
            var_path = value[2:-1].strip()
            interpolated = _resolve_variable_path(var_path, variables)
            result[key] = interpolated if interpolated is not None else value
        else:
            result[key] = value
    return result


def _resolve_variable_path(path: str, variables: dict[str, Any]) -> Any:
    """Resolve a dotted path like 'labels.instance' from variables dict."""
    parts = path.split(".")
    current: Any = variables
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _choose_recommended_tool_call_from_skill_load(
    load_run: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    variables: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    data = load_run.get("data")
    if not isinstance(data, dict):
        return None

    scripts = data.get("scripts")
    if isinstance(scripts, list) and scripts:
        return None

    # Only count successful tool executions as "attempted"
    attempted_runs: list[tuple[str, dict[str, Any]]] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        if not bool(run.get("success", False)):
            continue
        tool_name = str(run.get("tool", "")).strip()
        params = run.get("params")
        attempted_runs.append((tool_name, params if isinstance(params, dict) else {}))

    recommended_tools = data.get("recommended_tools")
    candidates: list[dict[str, Any]] = []
    if isinstance(recommended_tools, list):
        for item in recommended_tools:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool", "")).strip()
            params = item.get("params", {})
            # Interpolate ${variable} syntax in params
            interpolated_params = _interpolate_params(params, variables)
            candidates.append(
                {
                    "name": tool_name,
                    "args": interpolated_params,
                }
            )
    if not candidates:
        content = str(data.get("content") or "").strip()
        if not content:
            return None
        candidates = _extract_recommended_tool_calls_from_skill_content(content)

    for candidate in candidates:
        tool_name = str(candidate.get("name", "")).strip()
        args_dict = candidate.get("args") or {}

        already_attempted = False
        for attempted_tool_name, attempted_params in attempted_runs:
            if attempted_tool_name != tool_name:
                continue
            if not args_dict:
                already_attempted = True
                break
            if all(attempted_params.get(key) == value for key, value in args_dict.items()):
                already_attempted = True
                break
        if already_attempted:
            continue
        return {"name": tool_name, "args": args_dict}
    return None


def _auto_run_single_script_skill(
    state: SREAgentState,
    pending_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if any(
        str(call.get("name", "")).strip() in {"skills.read_skill_ref", "skills.run_skill"}
        for call in pending_tool_calls
        if isinstance(call, dict)
    ):
        return pending_tool_calls

    tool_runs = [dict(item) for item in list(state.get("tool_runs", []) or []) if isinstance(item, dict)]
    latest_load = _find_latest_successful_skill_load(tool_runs)
    if latest_load is None:
        return pending_tool_calls

    skill_id, script = _choose_skill_script_to_run(latest_load, tool_runs)
    if not skill_id or not script:
        return pending_tool_calls

    call_id = ""
    if pending_tool_calls:
        call_id = str((pending_tool_calls[0] or {}).get("id", "")).strip()
    call_id = call_id or "call-skill-run"
    return [
        {
            "name": "skills.run_skill",
            "args": {"skill_id": skill_id, "script": script},
            "id": call_id,
            "type": "tool_call",
        }
    ]


def _auto_follow_loaded_skill_recommended_tools(
    state: SREAgentState,
    pending_tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Check if LLM is trying to run_skill on a skill without scripts
    # If so, replace with recommended_tools instead
    tool_runs = [dict(item) for item in list(state.get("tool_runs", []) or []) if isinstance(item, dict)]
    latest_load = _find_latest_successful_skill_load(tool_runs)

    if latest_load is not None:
        data = latest_load.get("data", {})
        if isinstance(data, dict):
            scripts = data.get("scripts")
            # If skill has no scripts, check if LLM is calling skills.run_skill
            if not (isinstance(scripts, list) and scripts):
                for call in pending_tool_calls:
                    if isinstance(call, dict) and str(call.get("name", "")).strip() == "skills.run_skill":
                        # Replace with recommended_tools instead
                        variables = dict(state.get("variables", {}) or {})
                        candidate = _choose_recommended_tool_call_from_skill_load(latest_load, tool_runs, variables=variables)
                        if candidate:
                            call_id = str(call.get("id", "") or "").strip() or "call-skill-followup"
                            return [
                                {
                                    "name": str(candidate.get("name", "")).strip(),
                                    "args": dict(candidate.get("args") or {}),
                                    "id": call_id,
                                    "type": "tool_call",
                                }
                            ]

    # Normal flow: if pending_tool_calls already has non-list_skills calls, return them
    if any(
        str(call.get("name", "")).strip() not in {"skills.list_skills"}
        for call in pending_tool_calls
        if isinstance(call, dict)
    ):
        return pending_tool_calls

    if latest_load is None:
        return pending_tool_calls

    variables = dict(state.get("variables", {}) or {})
    candidate = _choose_recommended_tool_call_from_skill_load(latest_load, tool_runs, variables=variables)
    if not candidate:
        return pending_tool_calls

    call_id = ""
    if pending_tool_calls:
        call_id = str((pending_tool_calls[0] or {}).get("id", "")).strip()
    call_id = call_id or "call-skill-followup"
    return [
        {
            "name": str(candidate.get("name", "")).strip(),
            "args": dict(candidate.get("args") or {}),
            "id": call_id,
            "type": "tool_call",
        }
    ]


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
    max_keys: int = 50,  # Increased to show full labels (host_ip etc.)
    max_string: int = 180,
) -> Any:
    if depth >= max_depth:
        if isinstance(value, dict):
            if max_keys <= 0:
                return {"kind": "object", "keys": sorted(str(key) for key in value.keys())}
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
        keys = list(value.keys()) if max_keys <= 0 else list(value.keys())[:max_keys]
        for key in keys:
            compact[str(key)] = _compact_prompt_value(
                value[key],
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_keys=max_keys,
                max_string=max_string,
            )
        if max_keys > 0 and len(value) > max_keys:
            compact["_remaining_keys"] = len(value) - max_keys
        return compact
    return value


def _json_line(value: Any) -> str:
    """Convert value to JSON, with special handling for skills.load_skill output_summary."""
    safe_value = _safe_jsonable(value)
    # Preserve output_summary for skills.load_skill entries (don't compress)
    if isinstance(safe_value, list):
        preserved_output_summaries: dict[int, Any] = {}
        for i, item in enumerate(safe_value):
            if isinstance(item, dict) and item.get("tool") == "skills.load_skill" and "output_summary" in item:
                preserved_output_summaries[i] = item["output_summary"]
                # Temporarily remove to prevent compression
                item.pop("output_summary")
        # Now compress
        compacted = _compact_prompt_value(safe_value)
        # Restore preserved output_summaries
        for i, preserved in preserved_output_summaries.items():
            if isinstance(compacted, list) and i < len(compacted) and isinstance(compacted[i], dict):
                compacted[i]["output_summary"] = preserved
        return json.dumps(compacted, ensure_ascii=False, sort_keys=True)
    return json.dumps(_compact_prompt_value(safe_value), ensure_ascii=False, sort_keys=True)


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
    netem_present = bool(re.search(r"\bnetem\b", text, flags=re.IGNORECASE))
    delay_ms: float | None = None
    delay_match = re.search(r"\bdelay\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|us|s)?", text, flags=re.IGNORECASE)
    if delay_match:
        raw = float(delay_match.group(1))
        unit = str(delay_match.group(2) or "ms").strip().lower()
        if unit == "us":
            delay_ms = raw / 1000.0
        elif unit == "s":
            delay_ms = raw * 1000.0
        else:
            delay_ms = raw

    parent_match = re.search(r"\bparent\s+([^\s]+)", text, flags=re.IGNORECASE)
    handle_match = re.search(r"\bhandle\s+([^\s]+)", text, flags=re.IGNORECASE)
    loss_match = re.search(r"\bloss\s+([^\s]+(?:\s+[^\s]+)?)", text, flags=re.IGNORECASE)

    findings: list[str] = [f"netem_present={str(netem_present).lower()}"]
    if delay_ms is not None:
        findings.append(f"delay_ms={round(delay_ms, 3)}")
    if loss_match:
        findings.append(f"loss={_truncate_prompt_note(loss_match.group(1), max_chars=32)}")
    if parent_match:
        findings.append(f"parent={_truncate_prompt_note(parent_match.group(1), max_chars=24)}")
    if handle_match:
        findings.append(f"handle={_truncate_prompt_note(handle_match.group(1), max_chars=24)}")

    # Extract iface: prefer data.iface, fallback to dev <iface> in text
    raw_iface = data.get("iface") if isinstance(data, dict) else None
    iface_match = re.search(r"\bdev\s+([a-zA-Z0-9_.:-]+)", text, flags=re.IGNORECASE)
    resolved_iface = str(raw_iface or "").strip() or (iface_match.group(1).strip() if iface_match else None) or None
    if resolved_iface:
        findings.append(f"iface={_truncate_prompt_note(resolved_iface, max_chars=24)}")

    return "; ".join(findings), {
        "netem_present": netem_present,
        "delay_ms": delay_ms,
        "parent": parent_match.group(1) if parent_match else None,
        "handle": handle_match.group(1) if handle_match else None,
        "loss": loss_match.group(1) if loss_match else None,
        "iface": resolved_iface,
        "signals": findings[:5],
    }


def _summarize_find_process(data: Any) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(data, dict):
        summary, _, fields = _summarize_generic_data(data)
        return summary, fields

    raw_count = data.get("count")
    try:
        count = max(0, int(raw_count))
    except Exception:  # noqa: BLE001
        count = 0
    matches = data.get("matches")
    normalized_matches = matches if isinstance(matches, list) else []
    process_names: list[str] = []
    sample_pids: list[int] = []
    for item in normalized_matches[:8]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("process", "") or "").strip()
        if name and name not in process_names:
            process_names.append(name)
        pid = item.get("pid")
        try:
            parsed_pid = int(pid)
        except Exception:  # noqa: BLE001
            continue
        if parsed_pid not in sample_pids:
            sample_pids.append(parsed_pid)
    tc_process_present = count > 0 and any(
        token in " ".join(process_names).lower()
        for token in ("tc", "netem", "fault_injector", "fi_")
    )
    summary = f"matches={count}; processes={','.join(process_names[:4]) if process_names else 'none'}"
    return summary, {
        "match_count": count,
        "process_names": process_names[:6],
        "sample_pids": sample_pids[:6],
        "tc_process_present": tc_process_present,
    }


def _summarize_process_find(data: Any) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(data, dict):
        summary, _, fields = _summarize_generic_data(data)
        return summary, fields

    raw_count = data.get("count")
    try:
        count = max(0, int(raw_count))
    except Exception:  # noqa: BLE001
        count = 0

    node = str(data.get("node", "") or "").strip()
    matches = data.get("matches")
    normalized_matches = matches if isinstance(matches, list) else []
    process_names: list[str] = []
    sample_pids: list[int] = []
    suspicious_processes: list[dict[str, Any]] = []
    seen_pid: set[int] = set()
    filtered_count = 0
    filtered_preview: list[str] = []

    for item in normalized_matches:
        if not isinstance(item, dict):
            continue
        process = str(item.get("process", "") or "").strip()
        command = str(item.get("command", "") or "").strip()
        candidate_name = command or process
        if process and process not in process_names and len(process_names) < 6:
            process_names.append(process)
        pid = item.get("pid")
        parsed_pid: int | None = None
        try:
            parsed_pid = int(pid)
        except Exception:  # noqa: BLE001
            parsed_pid = None
        if parsed_pid is not None and parsed_pid not in sample_pids and len(sample_pids) < 6:
            sample_pids.append(parsed_pid)

        merged_for_filter = f"{process} {command}".strip()
        if not is_ttft_suspect_process(merged_for_filter):
            filtered_count += 1
            if len(filtered_preview) < 3:
                filtered_preview.append(_truncate_prompt_note(merged_for_filter or process, max_chars=48))
            continue
        if parsed_pid is None:
            continue
        if parsed_pid in seen_pid:
            continue
        seen_pid.add(parsed_pid)
        if len(suspicious_processes) < 6:
            suspicious_processes.append(
                {
                    "pid": parsed_pid,
                    "process_name": candidate_name,
                    "memory_mib": None,
                    "node": node,
                }
            )

    suspicious_present = bool(suspicious_processes)
    summary = (
        f"matches={count}; node={node or 'unknown'}; "
        f"suspicious_load_present={str(suspicious_present).lower()}"
    )
    if suspicious_present:
        preview = ",".join(
            f"{item.get('pid')}:{_truncate_prompt_note(str(item.get('process_name', '')), max_chars=40)}"
            for item in suspicious_processes[:3]
        )
        summary = f"{summary}; suspicious={preview}"
    return summary, {
        "match_count": count,
        "node": node or None,
        "process_names": process_names[:6],
        "sample_pids": sample_pids[:6],
        "suspicious_load_present": suspicious_present,
        "suspicious_load_processes": suspicious_processes[:6],
        "suspicious_filtered_count": filtered_count,
        "suspicious_filter_reason": "not_whitelisted_or_denied",
        "suspicious_filter_preview": filtered_preview,
        "suspect_source_tool": "process.find",
    }


def _parse_gpu_process_rows(text: str, *, max_rows: int = 32) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = [segment.strip() for segment in stripped.split(",")]
        if len(parts) < 2:
            continue
        pid_text = parts[0]
        process_name = parts[1] if len(parts) > 1 else ""
        memory_text = parts[3] if len(parts) > 3 else ""
        try:
            pid = int(pid_text)
        except Exception:  # noqa: BLE001
            continue
        memory_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", memory_text)
        memory_mib = float(memory_match.group(1)) if memory_match else None
        rows.append(
            {
                "pid": pid,
                "process_name": process_name,
                "memory_mib": memory_mib,
            }
        )
        if len(rows) >= max_rows:
            break
    return rows


def _summarize_gpu_processes(data: Any) -> tuple[str, dict[str, Any] | None]:
    text = _extract_output_blob(data)
    rows = _parse_gpu_process_rows(text)
    if not rows:
        return (
            "process_count=0; suspicious_load_present=false",
            {
                "process_count": 0,
                "sample_pids": [],
                "sample_process_names": [],
                "suspicious_load_present": False,
                "suspicious_load_processes": [],
            },
        )

    sorted_rows = sorted(rows, key=lambda item: float(item.get("memory_mib") or 0.0), reverse=True)
    suspicious_rows = [
        item
        for item in sorted_rows
        if is_ttft_suspect_process(str(item.get("process_name", "")))
    ]
    sample_names = [
        _truncate_prompt_note(str(item.get("process_name", "")), max_chars=48)
        for item in sorted_rows[:4]
        if str(item.get("process_name", "")).strip()
    ]
    summary_parts = [
        f"process_count={len(sorted_rows)}",
        f"suspicious_load_present={str(bool(suspicious_rows)).lower()}",
    ]
    if sample_names:
        summary_parts.append(f"top_processes={','.join(sample_names)}")
    if suspicious_rows:
        suspicious_preview = ",".join(
            f"{item.get('pid')}:{_truncate_prompt_note(str(item.get('process_name', '')), max_chars=36)}"
            for item in suspicious_rows[:3]
        )
        summary_parts.append(f"suspicious={suspicious_preview}")
    return (
        "; ".join(summary_parts),
        {
            "process_count": len(sorted_rows),
            "sample_pids": [int(item.get("pid", 0) or 0) for item in sorted_rows[:8]],
            "sample_process_names": [
                str(item.get("process_name", "")).strip()
                for item in sorted_rows[:8]
                if str(item.get("process_name", "")).strip()
            ],
            "suspicious_load_present": bool(suspicious_rows),
            "suspicious_load_processes": [
                {
                    "pid": int(item.get("pid", 0) or 0),
                    "process_name": str(item.get("process_name", "")).strip(),
                    "memory_mib": item.get("memory_mib"),
                }
                for item in suspicious_rows[:6]
            ],
        },
    )


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
    elif tool == "network.find_process":
        prompt_summary, key_fields = _summarize_find_process(data)
    elif tool == "process.find":
        prompt_summary, key_fields = _summarize_process_find(data)
    elif tool == "bmc.get_fan_status":
        prompt_summary, key_fields = _summarize_bmc_fan_status(
            data,
            node_hint=str(params.get("node", "") or "").strip(),
        )
    elif tool == "gpu.get_metrics":
        prompt_summary, key_fields = _summarize_gpu_metrics(
            data,
            node_hint=str(params.get("node", "") or "").strip(),
        )
    elif tool == "gpu.get_processes":
        prompt_summary, key_fields = _summarize_gpu_processes(data)
    elif tool == "skills.load_skill":
        prompt_summary, key_fields = _summarize_skill_load(data)
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


def _summarize_bmc_fan_status(data: Any, *, node_hint: str = "") -> tuple[str, dict[str, Any]]:
    """Summarize BMC fan status for prompt."""
    if not isinstance(data, dict):
        return "kind=unknown", None
    summary = data.get("fan_status_summary") or {}
    mode_name = str(summary.get("mode_name", "Unknown") or "Unknown")
    is_manual = bool(summary.get("is_manual", False))
    is_fixed_pwm = bool(summary.get("is_fixed_pwm", False))
    fixed_pwm = summary.get("fixed_pwm")
    pwm_values = summary.get("unique_pwm_values", [])
    fan_count = summary.get("fan_count", 0)
    bmc_host = str(data.get("bmc_host", "") or "").strip()
    node = str(data.get("node", "") or "").strip() or node_hint

    key_fields = {
        "node": node or None,
        "mode": mode_name,
        "is_manual": is_manual,
        "is_fixed_pwm": is_fixed_pwm,
        "fixed_pwm": fixed_pwm,
        "pwm_values": pwm_values[:4] if pwm_values else None,
        "fan_count": fan_count,
        "bmc_host": bmc_host,
    }
    prompt_summary = (
        f"node={node or 'unknown'}; mode={mode_name}; manual={is_manual}; "
        f"fixed_pwm={is_fixed_pwm}; pwm={fixed_pwm or pwm_values}; fans={fan_count}"
    )
    return prompt_summary, key_fields


def _summarize_gpu_metrics(data: Any, *, node_hint: str = "") -> tuple[str, dict[str, Any]]:
    """Summarize GPU metrics (nvidia-smi output) for prompt."""
    output = _extract_output_blob(data)
    if not output:
        return "output=empty", None
    node = node_hint
    if isinstance(data, dict):
        node = str(data.get("node", "") or "").strip() or node_hint
    # Parse CSV output: index, name, util, mem_used, mem_total, temp
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    gpu_info: list[dict[str, Any]] = []
    max_temp = 0
    max_util = 0
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 6:
            try:
                idx = int(parts[0])
                name = parts[1][:30]  # truncate GPU name
                util = int(parts[2])
                mem_used = int(parts[3])
                mem_total = int(parts[4])
                temp = int(parts[5])
                gpu_info.append({"idx": idx, "temp": temp, "util": util, "mem": f"{mem_used}/{mem_total}"})
                max_temp = max(max_temp, temp)
                max_util = max(max_util, util)
            except (ValueError, IndexError):
                continue
    if not gpu_info:
        return f"lines={len(lines)}; parse_failed", None
    temps = [g["temp"] for g in gpu_info]
    prompt_summary = (
        f"node={node or 'unknown'}; gpu_count={len(gpu_info)}; temps={temps}; "
        f"max_temp={max_temp}C; max_util={max_util}%"
    )
    key_fields = {
        "node": node or None,
        "gpu_count": len(gpu_info),
        "temps": temps,
        "max_temp": max_temp,
        "max_util": max_util,
        "gpu_info": gpu_info[:4],
    }
    return prompt_summary, key_fields


def _summarize_gpu_processes(data: Any) -> tuple[str, dict[str, Any]]:
    """Summarize GPU processes for prompt."""
    output = _extract_output_blob(data)
    if not output:
        return "output=empty", None
    # Parse CSV output: pid, process_name, gpu_uuid, mem_used
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    processes: list[dict[str, Any]] = []
    process_names: set[str] = set()
    total_mem = 0
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            try:
                pid = int(parts[0])
                name = parts[1][:20]  # truncate process name
                gpu_uuid = parts[2][:20]
                mem = int(parts[3])
                processes.append({"pid": pid, "name": name, "mem": mem})
                process_names.add(name)
                total_mem += mem
            except (ValueError, IndexError):
                continue
    if not processes:
        return f"lines={len(lines)}; no_processes", None
    prompt_summary = f"process_count={len(processes)}; names={list(process_names)}; total_mem={total_mem}MiB"
    key_fields = {"process_count": len(processes), "process_names": list(process_names)[:3], "total_mem": total_mem, "processes": processes[:4]}
    return prompt_summary, key_fields


def _summarize_skill_load(data: Any) -> tuple[str, dict[str, Any]]:
    """Summarize skills.load_skill result for prompt - pass full SKILL.md content to LLM."""
    if not isinstance(data, dict):
        return "kind=unknown", None

    skill_id = str(data.get("skill_id", "") or "").strip()
    skill_name = str(data.get("name", "") or "").strip()
    content = str(data.get("content", "") or "").strip()  # Full SKILL.md content
    recommended_tools = data.get("recommended_tools", [])
    scripts = data.get("scripts", [])
    references = data.get("references", [])
    description = str(data.get("description", "") or "").strip()

    # Build key_fields with full content (no truncation)
    key_fields: dict[str, Any] = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "description": description,
        "recommended_tools": recommended_tools if isinstance(recommended_tools, list) else [],
        "scripts": scripts if isinstance(scripts, list) else [],
        "references": references if isinstance(references, list) else [],
        "content": content,  # Full SKILL.md content, no truncation
    }

    # Build prompt summary
    tool_count = len(recommended_tools) if isinstance(recommended_tools, list) else 0
    script_count = len(scripts) if isinstance(scripts, list) else 0
    prompt_summary = f"skill={skill_id or skill_name}; tools={tool_count}; scripts={script_count}; content_len={len(content)}"

    return prompt_summary, key_fields


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
    }
    # Include key_fields for specific tools that have useful data summaries
    key_fields = _safe_jsonable(item.get("key_fields"))
    if key_fields and isinstance(key_fields, dict):
        tool = str(item.get("tool", "") or "").strip()
        if tool == "skills.load_skill":
            content = str(key_fields.get("content", "") or "")
            rendered["output_summary"] = {
                "skill_id": str(key_fields.get("skill_id", "") or ""),
                "skill_name": str(key_fields.get("skill_name", "") or ""),
                "recommended_tools": key_fields.get("recommended_tools") if isinstance(key_fields.get("recommended_tools"), list) else [],
                "scripts": key_fields.get("scripts") if isinstance(key_fields.get("scripts"), list) else [],
                "references": key_fields.get("references") if isinstance(key_fields.get("references"), list) else [],
                "content_len": len(content),
            }
        # For BMC/GPU tools, include full key_fields as output_summary (no compression)
        elif tool.startswith("bmc.") or tool.startswith("gpu."):
            rendered["output_summary"] = key_fields  # Pass full content, no truncation
        else:
            rendered["key_fields"] = _compact_prompt_value(key_fields, max_items=4, max_keys=6, max_string=96)
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
    evidence_signals = state.get("evidence_signals")
    loop_guard = state.get("loop_guard")
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
            f"remediation_plan={_json_line(remediation_plan)}\n"
            f"evidence_signals={_json_line(evidence_signals)}\n"
            f"loop_guard={_json_line(loop_guard)}",
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


def _normalize_loop_guard_state(raw: Any, *, threshold_default: int = 2) -> dict[str, Any]:
    payload = dict(raw) if isinstance(raw, dict) else {}
    threshold = _normalize_positive_int(payload.get("threshold"), default=threshold_default, minimum=1)
    repeat_count = _normalize_positive_int(payload.get("repeat_count"), default=0, minimum=0)
    family_threshold = _normalize_positive_int(payload.get("family_threshold"), default=threshold_default, minimum=1)
    family_repeat_count = _normalize_positive_int(payload.get("family_repeat_count"), default=0, minimum=0)
    return {
        "recent_fingerprint": str(payload.get("recent_fingerprint", "") or "").strip() or None,
        "repeat_count": repeat_count,
        "threshold": threshold,
        "recent_family_fingerprint": str(payload.get("recent_family_fingerprint", "") or "").strip() or None,
        "family_repeat_count": family_repeat_count,
        "family_threshold": family_threshold,
        "triggered": bool(payload.get("triggered", False)),
        "trigger_step": payload.get("trigger_step"),
    }


def _build_tool_call_fingerprint(item: dict[str, Any]) -> str:
    tool = str(item.get("tool", "") or "").strip() or "unknown"
    params = _safe_jsonable(item.get("params", {}))
    if not isinstance(params, dict):
        params = {}
    summary = _truncate_prompt_note(str(item.get("prompt_summary", "") or "").strip(), max_chars=200)
    params_json = json.dumps(params, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{tool}|{params_json}|{summary}"


def _canonicalize_tool_args(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any]:
    canonical = _safe_jsonable(tool_args)
    if not isinstance(canonical, dict):
        canonical = {}
    # Normalize wrapped kwargs payload emitted by some providers.
    nested_kwargs = canonical.get("kwargs")
    if isinstance(nested_kwargs, dict):
        merged = dict(canonical)
        merged.pop("kwargs", None)
        for key, value in nested_kwargs.items():
            merged.setdefault(str(key), value)
        canonical = merged
    # Reduce noise for skills.list_skills queries so semantically-same calls can be deduped.
    if tool_name == "skills.list_skills":
        query = str(canonical.get("query", "") or "").strip().lower()
        query = re.sub(r"\s+", " ", query)
        canonical["query"] = query
    return canonical


def _build_pending_tool_call_dedupe_key(tool_name: str, tool_args: dict[str, Any]) -> str:
    canonical = _canonicalize_tool_args(tool_name, tool_args)
    canonical_json = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"{tool_name}|{canonical_json}"


def _normalize_promql_family(promql: str) -> str:
    text = str(promql or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r'"[^"]*"', '"?"', text)
    text = re.sub(r"\b\d+(\.\d+)?\b", "?", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tool_family_fingerprint(item: dict[str, Any]) -> str:
    tool = str(item.get("tool", "") or "").strip() or "unknown"
    params = item.get("params")
    if not isinstance(params, dict):
        params = {}
    if tool == "prometheus.query_instant":
        promql = _normalize_promql_family(str(params.get("promql", "") or ""))
        if promql:
            return f"{tool}|{promql}"
    if tool == "k8s.list_pods":
        namespace = str(params.get("namespace", "") or "").strip()
        selector = str(params.get("label_selector", "") or "").strip()
        return f"{tool}|ns={namespace}|selector={selector}"
    if tool == "skills.list_skills":
        return tool
    return tool


def _is_ttft_alert_state(state: SREAgentState) -> bool:
    snapshot = state.get("alert_snapshot")
    if not isinstance(snapshot, dict):
        return False
    alert_name = str(snapshot.get("alert_name", "") or "").strip().lower()
    return "ttft" in alert_name or alert_name.startswith("aiservicettft")


def _is_force_canary_alert_name(alert_name: str) -> bool:
    normalized = str(alert_name or "").strip().lower()
    return "ttft" in normalized


def _get_alert_name_from_state(state: SREAgentState) -> str:
    snapshot = state.get("alert_snapshot")
    if isinstance(snapshot, dict):
        return str(snapshot.get("alert_name", "") or "").strip()
    return ""


def _render_trace_tool_params(
    *,
    state: SREAgentState,
    tool_name: str,
    tool_args: Any,
) -> dict[str, Any]:
    """Render display-friendly params for trace visualization only."""
    rendered = dict(tool_args) if isinstance(tool_args, dict) else {}
    if tool_name == "process.find":
        node = str(rendered.get("node", "") or "").strip()
        if not node:
            ext_node = _get_ttft_external_node(state)
            if ext_node:
                rendered["node"] = ext_node
    return rendered


def _get_ttft_external_node(state: SREAgentState) -> str:
    """从 alert_snapshot 或 variables 获取 TTFT 外部压测源节点地址。"""
    snapshot = state.get("alert_snapshot")
    if isinstance(snapshot, dict):
        node = str(snapshot.get("ttft_external_process_default_node", "") or "").strip()
        if node:
            return node
    variables = state.get("variables")
    if isinstance(variables, dict):
        node = str(variables.get("ttft_external_process_default_node", "") or "").strip()
        if node:
            return node
    return ""


def _has_probed_ttft_external_node(
    tool_runs: list[dict[str, Any]],
    external_node: str,
) -> bool:
    """检查是否已对外部节点执行过 process.find。"""
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "process.find":
            continue
        params = run.get("params")
        if isinstance(params, dict):
            run_node = str(params.get("node", "") or "").strip()
            if run_node == external_node:
                return True
        data = run.get("data")
        if isinstance(data, dict):
            data_node = str(data.get("node", "") or "").strip()
            if data_node == external_node:
                return True
    return False


def _evaluate_ttft_min_coverage(
    *,
    state: SREAgentState,
    tool_runs: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """TTFT 最小取证覆盖：gpu.get_processes + 外部 process.find。"""
    gpu_probe_done = _find_latest_successful_tool_run(
        tool_runs,
        tool_name="gpu.get_processes",
    ) is not None
    external_node = _get_ttft_external_node(state)
    if external_node:
        external_probe_done = _has_probed_ttft_external_node(tool_runs, external_node)
    else:
        external_probe_done = _count_tool_runs(tool_runs, "process.find") > 0

    missing: list[str] = []
    if not gpu_probe_done:
        missing.append("gpu_processes")
    if not external_probe_done:
        missing.append("external_process_find")
    return len(missing) == 0, missing


def _find_ttft_external_probe_auth_error(
    tool_runs: list[dict[str, Any]],
    external_node: str,
) -> str:
    if not external_node:
        return ""
    seen_external_probe = False
    successful_external_probe = False
    errors: list[str] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "process.find":
            continue
        node = ""
        params = run.get("params")
        if isinstance(params, dict):
            node = str(params.get("node", "") or "").strip()
        if not node:
            data = run.get("data")
            if isinstance(data, dict):
                node = str(data.get("node", "") or "").strip()
        if node != external_node:
            continue
        seen_external_probe = True
        if bool(run.get("success", False)):
            successful_external_probe = True
            continue
        error_text = str(run.get("error", "") or "").strip()
        if not error_text:
            data = run.get("data")
            if isinstance(data, dict):
                error_text = str(data.get("error", "") or "").strip()
        if error_text:
            errors.append(error_text)
    if not seen_external_probe or successful_external_probe:
        return ""
    for message in errors:
        lowered = message.lower()
        if (
            "permission denied" in lowered
            or "authentication failed" in lowered
            or "auth failed" in lowered
        ):
            return message
    return ""


def _count_tool_runs(tool_runs: list[dict[str, Any]], tool_name: str) -> int:
    return sum(1 for run in tool_runs if isinstance(run, dict) and str(run.get("tool", "")).strip() == tool_name)


def _count_tool_family_runs(tool_runs: list[dict[str, Any]], family_fingerprint: str) -> int:
    if not family_fingerprint:
        return 0
    return sum(
        1
        for run in tool_runs
        if isinstance(run, dict) and _tool_family_fingerprint(run) == family_fingerprint
    )


def _find_latest_successful_tool_run(
    tool_runs: list[dict[str, Any]],
    *,
    tool_name: str,
    family_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    for run in reversed(tool_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != tool_name:
            continue
        if family_fingerprint and _tool_family_fingerprint(run) != family_fingerprint:
            continue
        if not bool(run.get("success", False)):
            continue
        return run
    return None


def _build_tool_run_dedupe_key(run: dict[str, Any]) -> str:
    tool_name = str(run.get("tool", "") or "").strip()
    params = run.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return _build_pending_tool_call_dedupe_key(tool_name, params)


def _find_latest_tool_run_by_dedupe_key(
    tool_runs: list[dict[str, Any]],
    dedupe_key: str,
) -> dict[str, Any] | None:
    if not dedupe_key:
        return None
    for run in reversed(tool_runs):
        if not isinstance(run, dict):
            continue
        if _build_tool_run_dedupe_key(run) != dedupe_key:
            continue
        return run
    return None


def _extract_evidence_signals(tool_runs: list[dict[str, Any]]) -> dict[str, Any]:
    signals: dict[str, Any] = {
        "tc_netem_present": False,
        "tc_process_present": False,
        "tc_delay_value": None,
        "tc_parent": None,
        "tc_handle": None,
        "tc_iface_candidates": [],
        "qdisc_evidence_steps": [],
        "process_evidence_steps": [],
        "ttft_suspect_process_present": False,
        "ttft_suspect_processes": [],
        "ttft_gpu_process_steps": [],
    }
    for run in tool_runs:
        if not isinstance(run, dict) or not bool(run.get("success", False)):
            continue
        tool = str(run.get("tool", "") or "").strip()
        key_fields = run.get("key_fields")
        fields = key_fields if isinstance(key_fields, dict) else {}
        summary = str(run.get("prompt_summary", "") or "").strip().lower()
        if tool == "network.get_tc_qdisc":
            netem_present = bool(fields.get("netem_present")) or "netem" in summary
            if netem_present:
                signals["tc_netem_present"] = True
                signals["qdisc_evidence_steps"].append(int(run.get("step", 0) or 0))
            delay_value = fields.get("delay_ms")
            if delay_value is not None and signals.get("tc_delay_value") is None:
                signals["tc_delay_value"] = delay_value
            parent = str(fields.get("parent", "") or "").strip()
            handle = str(fields.get("handle", "") or "").strip()
            if parent and not signals.get("tc_parent"):
                signals["tc_parent"] = parent
            if handle and not signals.get("tc_handle"):
                signals["tc_handle"] = handle
            iface = str(fields.get("iface", "") or "").strip()
            if iface and iface.lower() not in _PLACEHOLDER_VALUES:
                existing = signals.get("tc_iface_candidates")
                if not isinstance(existing, list):
                    existing = []
                    signals["tc_iface_candidates"] = existing
                if iface not in existing:
                    existing.append(iface)
        elif tool == "network.find_process":
            tc_process_present = bool(fields.get("tc_process_present"))
            if not tc_process_present:
                names = fields.get("process_names")
                joined = " ".join(str(name) for name in names) if isinstance(names, list) else summary
                tc_process_present = any(
                    token in joined.lower()
                    for token in ("tc", "netem", "fault_injector", "fi_")
                ) and int(fields.get("match_count") or 0) > 0
            if tc_process_present:
                signals["tc_process_present"] = True
                signals["process_evidence_steps"].append(int(run.get("step", 0) or 0))
        elif tool == "gpu.get_processes":
            suspect_items: list[dict[str, Any]] = []
            run_params = run.get("params")
            run_node = ""
            if isinstance(run_params, dict):
                run_node = str(run_params.get("node", "") or "").strip()
            key_suspects = fields.get("suspicious_load_processes")
            if isinstance(key_suspects, list):
                for item in key_suspects[:6]:
                    if not isinstance(item, dict):
                        continue
                    process_name = str(item.get("process_name", "") or "").strip()
                    if not process_name:
                        continue
                    suspect_items.append(
                        {
                            "pid": item.get("pid"),
                            "process_name": process_name,
                            "memory_mib": item.get("memory_mib"),
                            "node": run_node,
                        }
                    )
            if not suspect_items:
                sample_names = fields.get("sample_process_names")
                names = sample_names if isinstance(sample_names, list) else []
                sample_pids = fields.get("sample_pids")
                pids = sample_pids if isinstance(sample_pids, list) else []
                for idx, raw_name in enumerate(names[:6]):
                    name = str(raw_name or "").strip()
                    if not name:
                        continue
                    if not (is_ttft_suspect_process(name) or _is_gpu_contention_suspect_process(name)):
                        continue
                    suspect_items.append(
                        {
                            "pid": pids[idx] if idx < len(pids) else None,
                            "process_name": name,
                            "memory_mib": None,
                            "node": run_node,
                        }
                    )
            if not suspect_items and any(token in summary for token in ("stress", "benchmark", "load", "simulator")):
                suspect_items.append(
                    {
                        "pid": None,
                        "process_name": "suspected_load_process",
                        "memory_mib": None,
                        "node": run_node,
                    }
                )
            if suspect_items:
                signals["ttft_suspect_process_present"] = True
                signals["ttft_gpu_process_steps"].append(int(run.get("step", 0) or 0))
                target = signals.get("ttft_suspect_processes")
                if not isinstance(target, list):
                    target = []
                    signals["ttft_suspect_processes"] = target
                for item in suspect_items:
                    if item not in target:
                        target.append(item)
        elif tool == "process.find":
            suspect_items = []
            run_params = run.get("params")
            run_node = ""
            if isinstance(run_params, dict):
                run_node = str(run_params.get("node", "") or "").strip()
            if not run_node and isinstance(fields.get("node"), str):
                run_node = str(fields.get("node") or "").strip()
            filtered_count = int(fields.get("suspicious_filtered_count") or 0)
            if filtered_count > 0:
                _llm_logger.info(
                    "ttft suspect process filter applied: suspect_filter_reason=%s suspect_source_tool=%s filtered_count=%s node=%s preview=%s",
                    str(fields.get("suspicious_filter_reason") or "not_whitelisted_or_denied"),
                    "process.find",
                    filtered_count,
                    run_node or "unknown",
                    _safe_jsonable(fields.get("suspicious_filter_preview")),
                )
            key_suspects = fields.get("suspicious_load_processes")
            if isinstance(key_suspects, list):
                for item in key_suspects[:8]:
                    if not isinstance(item, dict):
                        continue
                    process_name = str(item.get("process_name", "") or "").strip()
                    if not process_name:
                        continue
                    suspect_items.append(
                        {
                            "pid": item.get("pid"),
                            "process_name": process_name,
                            "memory_mib": item.get("memory_mib"),
                            "node": str(item.get("node", "") or "").strip() or run_node,
                        }
                    )
            if not suspect_items:
                run_data = run.get("data")
                matches = run_data.get("matches") if isinstance(run_data, dict) else None
                if isinstance(matches, list):
                    for item in matches[:8]:
                        if not isinstance(item, dict):
                            continue
                        process = str(item.get("process", "") or "").strip()
                        command = str(item.get("command", "") or "").strip()
                        merged = f"{process} {command}".strip()
                        if not is_ttft_suspect_process(merged):
                            continue
                        suspect_items.append(
                            {
                                "pid": item.get("pid"),
                                "process_name": command or process,
                                "memory_mib": None,
                                "node": run_node,
                            }
                        )
            if suspect_items:
                signals["ttft_suspect_process_present"] = True
                target = signals.get("ttft_suspect_processes")
                if not isinstance(target, list):
                    target = []
                    signals["ttft_suspect_processes"] = target
                for item in suspect_items:
                    if item not in target:
                        target.append(item)
    return signals


def _diagnosis_mentions_tc_evidence(payload: dict[str, Any]) -> bool:
    """Detect tc/netem evidence mentions from diagnosis payload text fields."""
    texts: list[str] = []
    root_cause_payload = payload.get("root_cause")
    if isinstance(root_cause_payload, list):
        for item in root_cause_payload:
            if isinstance(item, dict):
                texts.append(str(item.get("title", "") or ""))
    elif isinstance(root_cause_payload, str):
        texts.append(root_cause_payload)
    texts.append(str(payload.get("impact_summary", "") or ""))
    hypotheses = payload.get("hypotheses")
    if isinstance(hypotheses, list):
        for item in hypotheses:
            if not isinstance(item, dict):
                continue
            texts.append(str(item.get("description", "") or ""))
            for evidence in item.get("evidence_for", []) if isinstance(item.get("evidence_for"), list) else []:
                texts.append(str(evidence))
    combined = " ".join(texts).lower()
    return any(token in combined for token in ("tc ", "tc/", "netem", "qdisc", "fault_injector", "fi_"))


def _build_tc_consistency_retry_messages(
    *,
    system_prompt: str,
    query: str,
    evidence_signals: dict[str, Any],
    diagnosis_payload: dict[str, Any],
) -> list[Any]:
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=(
                "Network Jitter evidence consistency correction:\n"
                f"query={query or '<empty>'}\n"
                f"evidence_signals={_json_line(evidence_signals)}\n"
                f"current_diagnosis={_json_line(diagnosis_payload)}\n\n"
                "Rules:\n"
                "- If tc/netem evidence exists, diagnosis root cause and hypotheses must explicitly reflect tc/netem.\n"
                "- Do not output '璇佹嵁涓嶈冻' when tc/netem evidence is present.\n"
                "- Return JSON only with keys: thought, diagnosis, remediation_plan."
            )
        ),
    ]


def _select_primary_root_cause_item(diagnosis_payload: dict[str, Any]) -> dict[str, Any]:
    """Select primary root cause item from payload for first-root-cause remediation flow.

    Purpose:
    - return the root-cause item used by plan-completion and remediation prompts.
    Input/Output:
    - input: diagnosis payload that may contain normalized or malformed root-cause data;
    - output: a dict-like primary root-cause item with at least `title`, `layer`, and `confidence`.
    Compatibility rationale:
    - keeps temporary parser tolerance for old payloads while enforcing new first-item semantics.
    Why:
    - current remediation workflow intentionally uses a single plan driven by `root_cause[0]`.
    """
    raw_root_cause = diagnosis_payload.get("root_cause")
    if isinstance(raw_root_cause, list):
        for item in raw_root_cause:
            if isinstance(item, dict):
                return {
                    "title": str(item.get("title") or item.get("root_cause") or "").strip(),
                    "layer": str(item.get("layer") or item.get("root_cause_layer") or "platform").strip(),
                    "confidence": item.get("confidence", diagnosis_payload.get("confidence")),
                }
    if isinstance(raw_root_cause, str) and raw_root_cause.strip():
        # Model-output tolerance: accept legacy string root cause as a temporary parse fallback.
        return {
            "title": raw_root_cause.strip(),
            "layer": "platform",
            "confidence": diagnosis_payload.get("confidence"),
        }
    return {
        "title": "",
        "layer": "platform",
        "confidence": diagnosis_payload.get("confidence"),
    }


def _build_plan_completion_messages(
    *,
    system_prompt: str,
    query: str,
    diagnosis_payload: dict[str, Any],
    tool_runs: list[dict[str, Any]],
) -> list[Any]:
    """Build focused remediation-plan completion prompt using the primary root cause only."""
    primary = _select_primary_root_cause_item(diagnosis_payload)
    root_cause = str(primary.get("title") or "").strip()
    root_layer = str(primary.get("layer") or "").strip()
    confidence = primary.get("confidence", diagnosis_payload.get("confidence"))
    prompt_lines = [
        "Plan completion request:",
        f"query={query or '<empty>'}",
        f"root_cause={root_cause or '<unknown>'}",
        f"root_cause_layer={root_layer or '<unknown>'}",
        f"confidence={confidence if confidence is not None else '<unknown>'}",
        f"diagnosis={_json_line(diagnosis_payload)}",
        f"tool_evidence={_json_line(tool_runs[-5:]) if tool_runs else 'none'}",
        "",
        "Rules:",
        "- Keep diagnosis unchanged; only complete remediation_plan.",
        "- Prefer one conservative proposal-only step first.",
        "- Use a real write-tool name from schema and include all required params.",
        "- If no safe executable proposal can be formed, set remediation_plan to null.",
        "- Return JSON only with keys: thought, diagnosis, remediation_plan.",
    ]
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n".join(prompt_lines)),
    ]


def _build_tc_fallback_diagnosis_payload_legacy(
    *,
    original: dict[str, Any],
    evidence_signals: dict[str, Any],
    tool_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Legacy snapshot kept for diff/reference only.

    Purpose:
    - preserve historical implementation for audit comparison during migration.
    Input/Output:
    - same signature as active function; output should not be used by current runtime.
    Compatibility logic:
    - this function is intentionally unreferenced after the strict `root_cause[]` migration.
    Why:
    - keeping the snapshot reduces risk while the migration stabilizes, then it can be removed.
    """
    fallback = dict(original)
    delay_value = evidence_signals.get("tc_delay_value")
    delay_text = f"{delay_value}ms" if delay_value is not None else "unknown"
    entities: list[str] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        params = run.get("params")
        if not isinstance(params, dict):
            continue
        node = str(params.get("node", "") or "").strip()
        if node and node not in entities:
            entities.append(node)

    fallback["confidence"] = max(_clamp_confidence(fallback.get("confidence"), default=0.6), 0.72)
    fallback["diagnosis_certainty"] = "probable"
    fallback["impact_summary"] = f"已命中 tc/netem 强证据（delay≈{delay_text}），当前时延异常与 tc qdisc 注入/残留高度相关。"
    fallback["root_cause"] = [
        {
            "id": "rc-1",
            "title": "检测到 tc/netem 注入或残留规则导致网络时延抖动",
            "layer": "network",
            "entities": entities,
            "confidence": fallback["confidence"],
            "certainty": "probable",
            "status": "suspected",
            "evidence_summary": f"命中 tc/netem 强证据（delay≈{delay_text}）",
            "impact_summary": fallback["impact_summary"],
            "distinguishing_verification": "检查 qdisc 规则是否存在注入残留并重新验证时延",
            "recommended_fix": None,
        }
    ]
    # Transitional cleanup: remove legacy keys to ensure all paths converge to root_cause[].
    fallback.pop("ranked_candidates", None)
    fallback.pop("root_cause_layer", None)
    fallback.pop("root_cause_entities", None)
    priority = str(fallback.get("triage_priority") or "P1").strip().upper()
    fallback["triage_priority"] = priority if priority in {"P0", "P1", "P2", "P3"} else "P1"
    return fallback

    fallback = dict(original)
    delay_value = evidence_signals.get("tc_delay_value")
    delay_text = f"{delay_value}ms" if delay_value is not None else "鏈煡"
    entities: list[str] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        params = run.get("params")
        if not isinstance(params, dict):
            continue
        node = str(params.get("node", "") or "").strip()
        if node and node not in entities:
            entities.append(node)
    if not entities:
        entities = [str(item) for item in (fallback.get("root_cause_entities") or []) if str(item).strip()]
    fallback["root_cause"] = "检测到 tc/netem 注入或残留规则导致网络时延抖动"
    fallback["root_cause_layer"] = "network"
    fallback["root_cause_entities"] = entities
    fallback["confidence"] = max(_clamp_confidence(fallback.get("confidence"), default=0.6), 0.72)
    fallback["diagnosis_certainty"] = "probable"
    fallback["impact_summary"] = (
        f"已命中 tc/netem 强证据（delay≈{delay_text}），当前时延异常与 tc qdisc 注入/残留高度相关。"
    )
    priority = str(fallback.get("triage_priority") or "P1").strip().upper()
    fallback["triage_priority"] = priority if priority in {"P0", "P1", "P2", "P3"} else "P1"
    return fallback


def _build_tc_fallback_diagnosis_payload(
    *,
    original: dict[str, Any],
    evidence_signals: dict[str, Any],
    tool_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build tc/netem fallback diagnosis in the canonical `root_cause[]` contract.

    Purpose:
    - produce a deterministic fallback diagnosis when tc/netem strong evidence is detected.
    Input/Output:
    - input: original diagnosis payload, evidence signals, and recent tool-run records.
    - output: normalized diagnosis payload with one primary root-cause object.
    Compatibility logic:
    - strips removed legacy keys from the final payload.
    Why:
    - all diagnosis consumers now read only from `root_cause[]`.
    """
    fallback = dict(original)
    delay_value = evidence_signals.get("tc_delay_value")
    delay_text = f"{delay_value}ms" if delay_value is not None else "unknown"
    entities: list[str] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        params = run.get("params")
        if not isinstance(params, dict):
            continue
        node = str(params.get("node", "") or "").strip()
        if node and node not in entities:
            entities.append(node)

    confidence = max(_clamp_confidence(fallback.get("confidence"), default=0.6), 0.72)
    impact_summary = (
        f"Strong tc/netem evidence detected (delay >= {delay_text}); the latency anomaly is "
        "highly correlated with qdisc injection or residual shaping rules."
    )

    fallback["confidence"] = confidence
    fallback["diagnosis_certainty"] = "probable"
    fallback["impact_summary"] = impact_summary
    fallback["root_cause"] = [
        {
            "id": "rc-tc-netem-injection",
            "title": "tc/netem injection or residual qdisc rule causes network latency jitter",
            "layer": "network",
            "entities": entities,
            "confidence": confidence,
            "certainty": "probable",
            "status": "suspected",
            "evidence_summary": f"Matched tc/netem strong evidence (delay >= {delay_text}).",
            "impact_summary": impact_summary,
            "distinguishing_verification": (
                "Inspect qdisc state for injected or residual rules and re-check latency."
            ),
            "recommended_fix": None,
        }
    ]

    # Transitional cleanup: tolerate legacy input fields but never expose them in final output.
    fallback.pop("ranked_candidates", None)
    fallback.pop("root_cause_layer", None)
    fallback.pop("root_cause_entities", None)
    priority = str(fallback.get("triage_priority") or "P1").strip().upper()
    fallback["triage_priority"] = priority if priority in {"P0", "P1", "P2", "P3"} else "P1"
    return fallback


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


def _build_fallback_final_output_legacy(*, query: str, content: str) -> FinalDiagnosisEnvelope:
    """Legacy snapshot kept for migration traceability and rollback diffing.

    Purpose:
    - retain the pre-migration fallback behavior as a historical reference.
    Input/Output:
    - same as active fallback builder, but not used by the current execution path.
    Compatibility logic:
    - active runtime uses the non-legacy `_build_fallback_final_output`.
    Why:
    - avoids accidental behavior drift during staged cleanup of legacy branches.
    """
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


def _build_fallback_final_output(*, query: str, content: str) -> FinalDiagnosisEnvelope:
    """Build a contract-safe fallback envelope when model output is malformed.

    Purpose:
    - guarantee a parseable diagnosis result even if the model returned non-JSON text.
    Input/Output:
    - input: original user query and raw model text content.
    - output: `FinalDiagnosisEnvelope` with normalized `diagnosis.root_cause[]`.
    Compatibility logic:
    - does not emit removed legacy diagnosis fields.
    Why:
    - downstream validators and UI rely on strict `root_cause[]` semantics.
    """
    summary = (content or "").strip()
    if not summary:
        summary = "Model returned empty content while generating diagnosis."
    if len(summary) > 300:
        summary = summary[:297] + "..."

    title = summary.splitlines()[0].strip() if summary else "Insufficient evidence for a confirmed root cause"
    if len(title) > 140:
        title = title[:137] + "..."

    return FinalDiagnosisEnvelope.model_validate(
        {
            "thought": "Converted non-JSON model output into a low-confidence structured diagnosis.",
            "diagnosis": {
                "root_cause": [
                    {
                        "id": "rc-fallback-non-json",
                        "title": title or "Insufficient evidence for a confirmed root cause",
                        "layer": "platform",
                        "entities": [],
                        "confidence": 0.35,
                        "certainty": "ambiguous",
                        "status": "suspected",
                        "evidence_summary": summary,
                        "impact_summary": summary,
                        "distinguishing_verification": (
                            "Collect additional metrics and tool evidence to refine diagnosis."
                        ),
                        "recommended_fix": None,
                    }
                ],
                "confidence": 0.35,
                "impact_summary": summary,
                "affected_services": [],
                "triage_priority": "P2",
                "diagnosis_certainty": "ambiguous",
                "hypotheses": [
                    {
                        "description": title or "Fallback primary hypothesis from malformed model output",
                        "status": "testing",
                        "evidence_for": [summary] if summary else [],
                        "evidence_against": [],
                        "confidence": 0.35,
                    },
                    {
                        "description": "Tool evidence may be incomplete or unavailable in current context",
                        "status": "testing",
                        "evidence_for": ["Fallback path triggered for non-JSON response"],
                        "evidence_against": [],
                        "confidence": 0.3,
                    },
                    {
                        "description": "Alert may be transient or context may still be incomplete",
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
    data = _safe_jsonable(payload.get("data"))
    error = str(payload.get("error", "") or "").strip()
    skill_id = str(payload.get("skill_id", "") or "").strip() or None
    prompt_fields = _build_tool_prompt_fields(
        session_id=str(session_id or "").strip(),
        source=str(source or "").strip() or "tool",
        step=step,
        tool=tool,
        params=params,
        data=data,
        error=error,
        skill_id=skill_id,
    )
    return {
        "step": step,
        "tool": tool,
        "params": params,
        "success": success,
        "data": data,
        "error": error or None,
        "session_id": str(session_id or "").strip(),
        "source": str(source or "").strip() or "tool",
        "skill_id": skill_id,
        "prompt_summary": prompt_fields.get("prompt_summary") or "",
        "artifact_ref": prompt_fields.get("artifact_ref"),
        "data_kind": prompt_fields.get("data_kind"),
        "item_count": prompt_fields.get("item_count"),
        "key_fields": prompt_fields.get("key_fields"),
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
    loop_guard = _normalize_loop_guard_state(state.get("loop_guard"), threshold_default=2)
    force_final_turn = bool(state.get("force_final_turn", False))
    loop_guard_triggered = False
    tool_message_char_limit = _normalize_positive_int(
        state.get("tool_message_char_limit"),
        default=1200,
        minimum=200,
    )
    allowed_tool_names_for_turn = set(_select_bound_tool_names_for_turn(state) or [])
    is_ttft_alert = _is_ttft_alert_state(state)

    def _ttft_coverage_state(
        runs: list[dict[str, Any]],
    ) -> tuple[bool, list[str]]:
        if not is_ttft_alert:
            return True, []
        return _evaluate_ttft_min_coverage(state=state, tool_runs=runs)
    deduped_pending: list[dict[str, Any]] = []
    seen_pending_keys: set[str] = set()
    suppressed_in_round = 0
    suppressed_reasons: list[dict[str, Any]] = []
    for raw_call in pending:
        if not isinstance(raw_call, dict):
            continue
        pending_tool_name = str(raw_call.get("name", "")).strip()
        pending_tool_args = raw_call.get("args", {})
        if not isinstance(pending_tool_args, dict):
            pending_tool_args = {}
        pending_key = _build_pending_tool_call_dedupe_key(pending_tool_name, pending_tool_args)
        if pending_key in seen_pending_keys:
            suppressed_in_round += 1
            continue
        seen_pending_keys.add(pending_key)
        deduped_pending.append(raw_call)
    pending = deduped_pending

    # Inject variables into context.metadata for skill execution
    # This allows SkillExecutor to extract SSH info from alert labels
    if variables:
        context.metadata["variables"] = variables
    observation_entries: list[dict[str, Any]] = []
    for tool_call in pending:
        tool_name = str(tool_call.get("name", "")).strip()
        if allowed_tool_names_for_turn and tool_name not in allowed_tool_names_for_turn:
            result = ToolResult(
                tool=tool_name,
                success=False,
                data=None,
                error=(
                    f"tool {tool_name} is not allowed in the current turn; "
                    f"allowed tools: {sorted(allowed_tool_names_for_turn)}"
                ),
            )
            tool_args = raw_tool_args = tool_call.get("args", {})
            if not isinstance(tool_args, dict):
                tool_args = {}
            serialized_source = "tool"
        else:
            raw_tool_args = tool_call.get("args", {})
            tool_args = _merge_tool_args(
                registry=registry,
                tool_name=tool_name,
                tool_args=raw_tool_args if isinstance(raw_tool_args, dict) else {},
                variables=variables,
            )
            cached_skill_listing: dict[str, Any] | None = None
            latest_listing = _find_latest_successful_skill_listing([item for item in tool_runs if isinstance(item, dict)])
            if tool_name == "skills.list_skills" and latest_listing is not None:
                latest_listing_step = int(latest_listing.get("step", 0) or 0)
                current_step = len(tool_runs) + 1
                if latest_listing_step > 0 and current_step - latest_listing_step <= _SKILL_LIST_COOLDOWN_STEPS:
                    listing_data = latest_listing.get("data")
                    if isinstance(listing_data, dict):
                        cached_skill_listing = listing_data
            merged_call_key = _build_pending_tool_call_dedupe_key(tool_name, tool_args)
            latest_same_call = _find_latest_tool_run_by_dedupe_key(tool_runs, merged_call_key)
            can_reuse_session_call = (
                latest_same_call is not None
                and bool(latest_same_call.get("success", False))
            )
            if cached_skill_listing is not None:
                result = ToolResult(
                    tool=tool_name,
                    success=True,
                    data=cached_skill_listing,
                    error="",
                )
                serialized_source = "cooldown_cache"
            elif can_reuse_session_call:
                reused_step = int(latest_same_call.get("step", 0) or 0) if isinstance(latest_same_call, dict) else 0
                reused_data = _safe_jsonable(latest_same_call.get("data")) if isinstance(latest_same_call, dict) else None
                result = ToolResult(
                    tool=tool_name,
                    success=True,
                    data={
                        "duplicate_suppressed": True,
                        "reused_previous_result": True,
                        "reused_from_step": reused_step,
                        "summary": "duplicate suppressed, reused previous successful result summary",
                        "original_data": reused_data,
                    },
                    error="",
                )
                serialized_source = "duplicate_suppressed"
                coverage_met, coverage_missing = _ttft_coverage_state(tool_runs)
                if coverage_met:
                    force_final_turn = True
                suppressed_reasons.append(
                    {
                        "reason": "cross_round_duplicate",
                        "tool": tool_name,
                        "dedupe_key": merged_call_key,
                        "fingerprint": _build_tool_call_fingerprint(latest_same_call),
                        "reused_from_step": reused_step,
                        "reused": True,
                        "ttft_coverage_met": coverage_met,
                        "coverage_missing": coverage_missing,
                    }
                )
            elif is_ttft_alert and tool_name == "prometheus.query_instant":
                family_fingerprint = _tool_family_fingerprint(
                    {
                        "tool": tool_name,
                        "params": tool_args,
                    }
                )
                total_prometheus_calls = _count_tool_runs(tool_runs, "prometheus.query_instant")
                family_prometheus_calls = _count_tool_family_runs(tool_runs, family_fingerprint)
                latest_family_success = _find_latest_successful_tool_run(
                    tool_runs,
                    tool_name="prometheus.query_instant",
                    family_fingerprint=family_fingerprint,
                )
                latest_any_success = _find_latest_successful_tool_run(
                    tool_runs,
                    tool_name="prometheus.query_instant",
                )

                if family_prometheus_calls >= _TTFT_PROMETHEUS_FAMILY_BUDGET:
                    coverage_met, coverage_missing = _ttft_coverage_state(tool_runs)
                    if coverage_met:
                        force_final_turn = True
                    if latest_family_success is not None:
                        result = ToolResult(
                            tool=tool_name,
                            success=True,
                            data=_safe_jsonable(latest_family_success.get("data")),
                            error="",
                        )
                        serialized_source = "ttft_family_cache_reuse"
                        suppressed_reasons.append(
                            {
                                "reason": "family_budget_exceeded",
                                "family_fingerprint": family_fingerprint,
                                "family_calls": family_prometheus_calls,
                                "total_calls": total_prometheus_calls,
                                "reused": True,
                                "ttft_coverage_met": coverage_met,
                                "coverage_missing": coverage_missing,
                            }
                        )
                    else:
                        result = ToolResult(
                            tool=tool_name,
                            success=False,
                            data=None,
                            error="prometheus.query_instant suppressed by TTFT family budget (no cached success)",
                        )
                        serialized_source = "ttft_family_budget_blocked"
                        suppressed_reasons.append(
                            {
                                "reason": "family_budget_exceeded",
                                "family_fingerprint": family_fingerprint,
                                "family_calls": family_prometheus_calls,
                                "total_calls": total_prometheus_calls,
                                "reused": False,
                                "ttft_coverage_met": coverage_met,
                                "coverage_missing": coverage_missing,
                            }
                        )
                elif total_prometheus_calls >= _TTFT_PROMETHEUS_TOTAL_BUDGET:
                    coverage_met, coverage_missing = _ttft_coverage_state(tool_runs)
                    if coverage_met:
                        force_final_turn = True
                    if latest_any_success is not None:
                        result = ToolResult(
                            tool=tool_name,
                            success=True,
                            data=_safe_jsonable(latest_any_success.get("data")),
                            error="",
                        )
                        serialized_source = "ttft_total_cache_reuse"
                        suppressed_reasons.append(
                            {
                                "reason": "total_budget_exceeded",
                                "family_fingerprint": family_fingerprint,
                                "family_calls": family_prometheus_calls,
                                "total_calls": total_prometheus_calls,
                                "reused": True,
                                "ttft_coverage_met": coverage_met,
                                "coverage_missing": coverage_missing,
                            }
                        )
                    else:
                        result = ToolResult(
                            tool=tool_name,
                            success=False,
                            data=None,
                            error="prometheus.query_instant suppressed by TTFT total budget (no cached success)",
                        )
                        serialized_source = "ttft_total_budget_blocked"
                        suppressed_reasons.append(
                            {
                                "reason": "total_budget_exceeded",
                                "family_fingerprint": family_fingerprint,
                                "family_calls": family_prometheus_calls,
                                "total_calls": total_prometheus_calls,
                                "reused": False,
                                "ttft_coverage_met": coverage_met,
                                "coverage_missing": coverage_missing,
                            }
                        )
                else:
                    try:
                        result = await asyncio.wait_for(
                            registry.execute(tool_name, tool_args, context),
                            timeout=state["step_timeout_sec"],
                        )
                    except asyncio.TimeoutError:
                        result = ToolResult(tool=tool_name, success=False, data=None, error=f"tool timed out after {state['step_timeout_sec']}s")
                    serialized_source = "tool"
            else:
                try:
                    result = await asyncio.wait_for(
                        registry.execute(tool_name, tool_args, context),
                        timeout=state["step_timeout_sec"],
                    )
                except asyncio.TimeoutError:
                    result = ToolResult(tool=tool_name, success=False, data=None, error=f"tool timed out after {state['step_timeout_sec']}s")
                serialized_source = "tool"
        if serialized_source != "duplicate_suppressed":
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
                source=serialized_source,
            )
            # 璁板綍宸ュ叿鎵ц缁撴灉鍒版棩蹇楁枃浠?
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
            counts_for_loop_guard = str(serialized_source).strip() not in {
                "cooldown_cache",
                "repeat_cache_reuse",
                "ttft_family_cache_reuse",
                "ttft_total_cache_reuse",
            }
            if counts_for_loop_guard:
                fingerprint = _build_tool_call_fingerprint(serialized)
                if fingerprint and fingerprint == loop_guard.get("recent_fingerprint"):
                    loop_guard["repeat_count"] = int(loop_guard.get("repeat_count", 0) or 0) + 1
                else:
                    loop_guard["recent_fingerprint"] = fingerprint
                    loop_guard["repeat_count"] = 1
                family_fingerprint = _tool_family_fingerprint(serialized)
                if family_fingerprint and family_fingerprint == loop_guard.get("recent_family_fingerprint"):
                    loop_guard["family_repeat_count"] = int(loop_guard.get("family_repeat_count", 0) or 0) + 1
                else:
                    loop_guard["recent_family_fingerprint"] = family_fingerprint
                    loop_guard["family_repeat_count"] = 1
                if int(loop_guard.get("repeat_count", 0) or 0) > int(loop_guard.get("threshold", 2) or 2):
                    if not bool(loop_guard.get("triggered", False)):
                        loop_guard["triggered"] = True
                        loop_guard["trigger_step"] = serialized["step"]
                        loop_guard_triggered = True
                    coverage_met, _ = _ttft_coverage_state(tool_runs)
                    if coverage_met:
                        force_final_turn = True
                if int(loop_guard.get("family_repeat_count", 0) or 0) > int(loop_guard.get("family_threshold", 2) or 2):
                    if not bool(loop_guard.get("triggered", False)):
                        loop_guard["triggered"] = True
                        loop_guard["trigger_step"] = serialized["step"]
                        loop_guard_triggered = True
                    coverage_met, _ = _ttft_coverage_state(tool_runs)
                    if coverage_met:
                        force_final_turn = True
        else:
            log_stream_lifecycle_event(
                session_id=str(state.get("session_id", "unknown")),
                step=int(state.get("step_count", 0) or 0) + 1,
                mode="duplicate_suppressed",
                stage="act_node",
                status="suppressed",
                reason="identical tool call with normalized args already succeeded in this session",
                node=tool_name,
                event_type="tool_call_suppressed",
                pending_tool_calls_count=len(pending),
                step_count=int(state.get("step_count", 0) or 0),
                max_steps=int(state.get("max_steps", 0) or 0),
                extra={
                    "dedupe_key": merged_call_key,
                    "tool_args": _safe_jsonable(tool_args),
                },
            )
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
        # Always append one observation so frontend can close the "tool loading" state,
        # including duplicate-suppressed calls that intentionally do not enter tool_runs.
        observation_params = dict(tool_args) if isinstance(tool_args, dict) else {}
        if tool_name == "process.find" and "node" not in observation_params and isinstance(result.data, dict):
            resolved_node = str(result.data.get("node", "") or "").strip()
            if resolved_node:
                observation_params["node"] = resolved_node
        observation_entries.append(
            {
                "type": "observation",
                "tool": tool_name,
                "params": observation_params,
                "result": {
                    "success": result.success,
                    "data": _safe_jsonable(result.data),
                    "error": result.error,
                },
            }
        )
        if serialized_source != "duplicate_suppressed" and tool_name == "skills.load_skill" and bool(result.success):
            load_data = result.data if isinstance(result.data, dict) else {}
            skill_name = str(load_data.get("name", "") or "").strip()
            # Use metadata description directly; keep full text for frontend trace visibility.
            skill_description = " ".join(str(load_data.get("description", "") or "").split()).strip()
            skill_id = str(load_data.get("skill_id", "") or "").strip()
            summary_parts: list[str] = []
            if skill_name:
                summary_parts.append(f"名称：{skill_name}。")
            if skill_description:
                summary_parts.append(f"描述：{skill_description}")
            if summary_parts:
                observation_entries.append(
                    {
                        "type": "thought",
                        "step": state.get("step_count", 0) + 1,
                        "content": "已加载技能。".join(summary_parts),
                        "action": "observe",
                        "confidence": None,
                        "tool_params": {
                            "kind": "skill_load_summary",
                            "skill_id": skill_id,
                            "skill_name": skill_name,
                            "description": skill_description,
                        },
                    }
                )
    updated_trace_items = [*state.get("trace_items", []), *observation_entries]
    ttft_coverage_met, ttft_coverage_missing = _ttft_coverage_state(tool_runs)
    if is_ttft_alert and not ttft_coverage_met:
        force_final_turn = False

    if loop_guard_triggered:
        updated_trace_items.append(
            {
                "type": "thought",
                "step": state.get("step_count", 0) + 1,
                "content": (
                    "已停止重复指标查询，切换下一工具继续取证。"
                    if is_ttft_alert and not ttft_coverage_met
                    else "已停止重复查询，进入总结阶段。"
                ),
                "action": (
                    "tool_call"
                    if is_ttft_alert and not ttft_coverage_met
                    else "conclude"
                ),
                "confidence": None,
                "tool_params": {
                    "kind": "loop_guard_triggered",
                    "repeat_count": loop_guard.get("repeat_count"),
                    "threshold": loop_guard.get("threshold"),
                    "fingerprint": loop_guard.get("recent_fingerprint"),
                    "family_repeat_count": loop_guard.get("family_repeat_count"),
                    "family_threshold": loop_guard.get("family_threshold"),
                    "family_fingerprint": loop_guard.get("recent_family_fingerprint"),
                    "ttft_coverage_met": ttft_coverage_met,
                    "coverage_missing": ttft_coverage_missing if is_ttft_alert else [],
                },
            }
        )
    if suppressed_in_round > 0:
        updated_trace_items.append(
            {
                "type": "thought",
                "step": state.get("step_count", 0) + 1,
                "content": (
                    f"已停止 {suppressed_in_round} 次重复指标查询，切换下一工具继续取证。"
                    if is_ttft_alert and not ttft_coverage_met
                    else f"已停止 {suppressed_in_round} 次重复查询，进入总结阶段。"
                ),
                "action": (
                    "tool_call"
                    if is_ttft_alert and not ttft_coverage_met
                    else "conclude"
                ),
                "confidence": None,
                "tool_params": {
                    "kind": "duplicate_tool_suppressed",
                    "count": suppressed_in_round,
                    "ttft_coverage_met": ttft_coverage_met,
                    "coverage_missing": ttft_coverage_missing if is_ttft_alert else [],
                },
            }
        )
    for suppressed in suppressed_reasons:
        reason = str(suppressed.get("reason", "budget_exceeded")).strip()
        reused = bool(suppressed.get("reused", False))
        if reason == "cross_round_duplicate":
            suppressed_coverage_met = bool(suppressed.get("ttft_coverage_met", True))
            suppressed_coverage_missing = suppressed.get("coverage_missing", [])
            updated_trace_items.append(
                {
                    "type": "thought",
                    "step": state.get("step_count", 0) + 1,
                    "content": (
                        "已停止重复指标查询，切换下一工具继续取证。"
                        if is_ttft_alert and not suppressed_coverage_met
                        else "已停止重复查询，进入总结阶段。"
                    ),
                    "action": (
                        "tool_call"
                        if is_ttft_alert and not suppressed_coverage_met
                        else "conclude"
                    ),
                    "confidence": None,
                    "tool_params": {
                        "kind": "duplicate_tool_suppressed",
                        "reason": reason,
                        "tool": suppressed.get("tool"),
                        "fingerprint": suppressed.get("fingerprint"),
                        "dedupe_key": suppressed.get("dedupe_key"),
                        "reused_from_step": suppressed.get("reused_from_step"),
                        "reused": reused,
                        "ttft_coverage_met": suppressed_coverage_met if is_ttft_alert else True,
                        "coverage_missing": suppressed_coverage_missing if is_ttft_alert else [],
                        "force_canary_reason": "ttft_match" if is_ttft_alert else None,
                    },
                }
            )
            continue
        suppressed_coverage_met = bool(suppressed.get("ttft_coverage_met", True))
        suppressed_coverage_missing = suppressed.get("coverage_missing", [])
        updated_trace_items.append(
            {
                "type": "thought",
                "step": state.get("step_count", 0) + 1,
                "content": (
                    "TTFT 路径下已抑制额外 Prometheus 查询，"
                    f"原因={reason}，复用缓存={reused}。"
                    if not (is_ttft_alert and not suppressed_coverage_met)
                    else (
                        "TTFT 路径下已抑制重复 Prometheus 查询，"
                        f"原因={reason}；覆盖未完成，切换下一工具继续取证。"
                    )
                ),
                "action": (
                    "tool_call"
                    if is_ttft_alert and not suppressed_coverage_met
                    else "conclude"
                ),
                "confidence": None,
                "tool_params": {
                    "kind": "prometheus_query_suppressed",
                    "reason": reason,
                    "reused": reused,
                    "family_fingerprint": suppressed.get("family_fingerprint"),
                    "family_calls": suppressed.get("family_calls"),
                    "total_calls": suppressed.get("total_calls"),
                    "ttft_coverage_met": suppressed_coverage_met if is_ttft_alert else True,
                    "coverage_missing": suppressed_coverage_missing if is_ttft_alert else [],
                    "force_canary_reason": "ttft_match" if is_ttft_alert else None,
                },
            }
        )
    updated = {
        **state,
        "messages": messages,
        "tool_runs": tool_runs,
        "pending_tool_calls": [],
        "trace_items": updated_trace_items,
        "status": "running",
        "summary": state.get("summary"),
        "error": None,
        "evidence_signals": _extract_evidence_signals(tool_runs),
        "loop_guard": loop_guard,
        "force_final_turn": force_final_turn,
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


def _build_step_timeout_evidence_summary(
    tool_runs: list[dict[str, Any]],
    *,
    sample_limit: int = 3,
    per_item_chars: int = 160,
    total_chars: int = 380,
) -> str:
    successful_runs: list[dict[str, Any]] = []
    for run in tool_runs:
        if not isinstance(run, dict):
            continue
        if bool(run.get("success")):
            successful_runs.append(run)

    sampled_runs = successful_runs[-max(1, sample_limit) :]
    evidence_parts: list[str] = []
    for run in sampled_runs:
        tool_name = str(run.get("tool", "unknown") or "").strip() or "unknown"
        prompt_summary = _truncate_prompt_note(
            str(run.get("prompt_summary", "") or "").strip(),
            max_chars=max(80, per_item_chars),
        )
        key_fields = run.get("key_fields")
        key_summary = ""
        if isinstance(key_fields, dict) and key_fields:
            compact_fields = _compact_prompt_value(_safe_jsonable(key_fields))
            key_summary = _truncate_prompt_note(
                json.dumps(compact_fields, ensure_ascii=False, sort_keys=True),
                max_chars=max(80, per_item_chars - 20),
            )

        if prompt_summary and key_summary:
            evidence_parts.append(f"{tool_name}: {prompt_summary}; key={key_summary}")
        elif prompt_summary:
            evidence_parts.append(f"{tool_name}: {prompt_summary}")
        elif key_summary:
            evidence_parts.append(f"{tool_name}: key={key_summary}")
        else:
            evidence_parts.append(f"{tool_name}: success")

    if not evidence_parts:
        return "no successful tool evidence collected before timeout"
    return _truncate_prompt_note("; ".join(evidence_parts), max_chars=max(120, total_chars))


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
    # When step times out with no diagnosis result, synthesize partial diagnosis from collected evidence
    if updated.get("status") == "step_timeout" and updated.get("diagnosis_result") is None:
        raw_tool_runs = updated.get("tool_runs", [])
        tool_runs = [run for run in raw_tool_runs if isinstance(run, dict)]
        evidence_summary = _build_step_timeout_evidence_summary(tool_runs)
        summary_preview = _truncate_prompt_note(evidence_summary, max_chars=220)
        partial_diagnosis = {
            "root_cause": [
                {
                    "id": "rc-1",
                    "title": "Diagnosis timed out during analysis; partial evidence collected",
                    "layer": "service",
                    "entities": [],
                    "confidence": 0.45,
                    "certainty": "ambiguous",
                    "status": "suspected",
                    "evidence_summary": evidence_summary or "Diagnosis interrupted by timeout",
                    "impact_summary": f"Diagnosis incomplete due to timeout. Evidence: {evidence_summary}",
                    "distinguishing_verification": "Retry diagnosis with additional runtime budget",
                    "recommended_fix": None,
                }
            ],
            "confidence": 0.45,
            "impact_summary": f"Diagnosis incomplete due to timeout. Evidence: {evidence_summary}",
            "affected_services": [],
            "triage_priority": "P2",
            "diagnosis_certainty": "ambiguous",
            "hypotheses": [
                {
                    "description": "Diagnosis was interrupted by step timeout",
                    "status": "testing",
                    "evidence_for": [evidence_summary] if tool_runs else [],
                    "evidence_against": [],
                    "confidence": 0.45,
                },
            ],
        }
        updated["diagnosis_result"] = partial_diagnosis
        updated["summary"] = f"Diagnosis timed out; partial evidence: {summary_preview}"
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
    """Merge model tool args with runtime defaults and TTFT pattern normalization.

    This keeps non-TTFT behavior unchanged while tightening TTFT process probes:
    broad exact alternatives like `load`/`simulator` are rewritten to
    `load_simulator` to reduce noisy false matches (for example `--reload`).
    """
    merged = dict(tool_args)
    nested_kwargs = merged.pop("kwargs", None)
    if isinstance(nested_kwargs, dict):
        for key, value in nested_kwargs.items():
            merged.setdefault(str(key), value)
    if not variables:
        _normalize_runtime_defaults(merged, variables, tool_name=tool_name)
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
    if tool_name == "process.find":
        alert_name = str(variables.get("alert_name", "") or "").strip()
        if _is_force_canary_alert_name(alert_name):
            merged["pattern"] = _normalize_ttft_process_find_pattern(merged.get("pattern"))
    _normalize_runtime_defaults(merged, variables, tool_name=tool_name)
    return merged


_NODE_SELF_RESOLVING_TOOLS: frozenset[str] = frozenset({"process.find"})


def _normalize_ttft_process_find_pattern(raw_pattern: Any) -> str:
    """Normalize TTFT process.find regex alternatives for high-signal matching."""
    raw = str(raw_pattern or "").strip()
    if not raw:
        return TTFT_STRICT_PROCESS_FIND_PATTERN

    parts = [segment.strip() for segment in raw.split("|") if str(segment).strip()]
    if not parts:
        return TTFT_STRICT_PROCESS_FIND_PATTERN

    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        lowered = part.lower()
        if lowered in {"load", "simulator"}:
            part = "load_simulator"
            lowered = part
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(part)

    if not normalized:
        return TTFT_STRICT_PROCESS_FIND_PATTERN
    return "|".join(normalized)


def _normalize_runtime_defaults(
    params: dict[str, Any],
    variables: dict[str, Any],
    *,
    tool_name: str = "",
) -> None:
    if "node" in variables and tool_name not in _NODE_SELF_RESOLVING_TOOLS:
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


def _sanitize_inline_canary_payload(raw_canary: Any, *, step_count: int) -> dict[str, Any] | None:
    """Sanitize embedded canary payload so schema validation never receives non-positive percentages."""
    if not isinstance(raw_canary, dict):
        return None
    allowed_keys = {
        "enabled",
        "target_percentage",
        "monitor_duration",
        "success_criteria",
        "criteria_mode",
        "max_batches",
        "auto_rollback_on_regression",
        "progressive",
    }
    canary = {k: v for k, v in raw_canary.items() if k in allowed_keys}
    canary.setdefault("enabled", True)
    canary.setdefault("progressive", True)
    canary.setdefault("success_criteria", [])
    if not isinstance(canary.get("success_criteria"), list):
        canary["success_criteria"] = []

    criteria_mode = str(canary.get("criteria_mode") or "all").strip().lower()
    canary["criteria_mode"] = criteria_mode if criteria_mode in {"all", "any"} else "all"

    try:
        monitor_duration = int(canary.get("monitor_duration") or 0)
    except Exception:  # noqa: BLE001
        monitor_duration = 0
    canary["monitor_duration"] = monitor_duration if monitor_duration > 0 else 60

    try:
        max_batches = int(canary.get("max_batches") or 0)
    except Exception:  # noqa: BLE001
        max_batches = 0
    if max_batches <= 0:
        max_batches = step_count if step_count > 0 else 1
    canary["max_batches"] = max_batches

    try:
        target_percentage = float(canary.get("target_percentage"))
    except Exception:  # noqa: BLE001
        target_percentage = 0.0
    if target_percentage <= 0.0:
        if step_count > 0:
            target_percentage = round(1.0 / float(step_count), 4)
        else:
            target_percentage = 0.1
    canary["target_percentage"] = max(0.0001, min(1.0, target_percentage))

    if "auto_rollback_on_regression" not in canary:
        canary["auto_rollback_on_regression"] = bool(canary["max_batches"] >= 2)
    else:
        canary["auto_rollback_on_regression"] = bool(canary.get("auto_rollback_on_regression"))
    return canary


def _normalize_inline_recommended_fix_payload(raw_plan: Any) -> dict[str, Any] | None:
    """Normalize inline recommended_fix payload for diagnosis schema safety.

    This runs before `DiagnosisResult.model_validate`, so we keep the logic defensive:
    - repair canary values that violate strict constraints (for example `target_percentage=0`);
    - drop the inline plan when the shape is still invalid after sanitation.
    """
    if not isinstance(raw_plan, dict):
        return None
    candidate = dict(raw_plan)
    steps_payload = candidate.get("steps")
    if steps_payload is None and isinstance(candidate.get("actions"), list):
        candidate["steps"] = list(candidate.get("actions") or [])
        candidate.pop("actions", None)
        steps_payload = candidate.get("steps")
    step_count = len(steps_payload) if isinstance(steps_payload, list) else 0

    if "canary" in candidate:
        sanitized_canary = _sanitize_inline_canary_payload(candidate.get("canary"), step_count=step_count)
        candidate["canary"] = sanitized_canary

    try:
        normalized_plan = RemediationPlan.model_validate(candidate)
    except Exception as exc:  # noqa: BLE001
        _llm_logger.info("dropping invalid inline recommended_fix payload: %s", exc)
        return None
    return normalized_plan.model_dump(mode="json")


def _normalize_root_cause_item_payload(
    raw_item: Any,
    *,
    index: int,
    default_confidence: float,
    default_certainty: str,
    default_impact_summary: str,
    default_layer: str = "platform",
) -> dict[str, Any] | None:
    """Normalize one root-cause item into the formal array object shape.

    Purpose:
    - coerce loose LLM payloads into one valid `root_cause[]` item.
    Input/Output:
    - input: arbitrary raw item plus default values from diagnosis payload;
    - output: normalized root-cause dict or None when content is empty.
    Compatibility rationale:
    - supports tolerant parsing for malformed model output, but still emits only the new structure.
    Why:
    - centralizing item normalization avoids field drift across retry/fallback branches.
    """
    if isinstance(raw_item, str):
        title = raw_item.strip()
        if not title:
            return None
        return {
            "id": f"rc-{index}",
            "title": title,
            "layer": default_layer,
            "entities": [],
            "confidence": default_confidence,
            "certainty": default_certainty,
            "status": "suspected",
            "evidence_summary": default_impact_summary or title,
            "impact_summary": default_impact_summary or title,
            "distinguishing_verification": None,
            "recommended_fix": None,
        }
    if not isinstance(raw_item, dict):
        return None
    title = str(raw_item.get("title") or raw_item.get("root_cause") or "").strip()
    if not title:
        return None
    layer = str(raw_item.get("layer") or raw_item.get("root_cause_layer") or default_layer).strip()
    entities = raw_item.get("entities")
    if not isinstance(entities, list):
        entities = raw_item.get("root_cause_entities")
    normalized_entities = [str(item).strip() for item in (entities or []) if str(item).strip()]
    confidence = _clamp_confidence(raw_item.get("confidence"), default=default_confidence)
    certainty = str(raw_item.get("certainty") or default_certainty).strip().lower()
    if certainty not in {"confirmed", "probable", "ambiguous"}:
        certainty = default_certainty
    status = str(raw_item.get("status") or "suspected").strip().lower()
    if status not in {"confirmed", "contributing", "suspected", "monitoring"}:
        status = "suspected"
    evidence_summary = str(raw_item.get("evidence_summary") or default_impact_summary or title).strip()
    impact_summary = str(raw_item.get("impact_summary") or default_impact_summary or evidence_summary).strip()
    recommended_fix = _normalize_inline_recommended_fix_payload(raw_item.get("recommended_fix"))
    return {
        "id": str(raw_item.get("id") or f"rc-{index}").strip() or f"rc-{index}",
        "title": title,
        "layer": layer if layer in {"hardware", "network", "os", "platform", "service"} else default_layer,
        "entities": normalized_entities,
        "confidence": confidence,
        "certainty": certainty,
        "status": status,
        "evidence_summary": evidence_summary or title,
        "impact_summary": impact_summary or evidence_summary or title,
        "distinguishing_verification": (
            str(raw_item.get("distinguishing_verification")).strip()
            if raw_item.get("distinguishing_verification") is not None
            else None
        ),
        "recommended_fix": recommended_fix,
    }


def _normalize_root_cause_array_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize diagnosis payload into the formal `root_cause[]` array contract.

    Purpose:
    - enforce root-cause array output regardless of model response variants.
    Input/Output:
    - input: diagnosis payload dict emitted by LLM/fallback code;
    - output: normalized non-empty list of root-cause item dicts.
    Compatibility rationale:
    - accepts legacy shapes only as parser tolerance, not as public API compatibility.
    Why:
    - all downstream backend/frontend logic is now standardized on `root_cause[]`.
    """
    default_confidence = _clamp_confidence(payload.get("confidence"), default=0.5)
    default_certainty = str(payload.get("diagnosis_certainty") or "probable").strip().lower()
    if default_certainty not in {"confirmed", "probable", "ambiguous"}:
        default_certainty = "probable"
    default_impact_summary = str(payload.get("impact_summary") or "").strip()
    normalized_items: list[dict[str, Any]] = []

    raw_root_cause = payload.get("root_cause")
    if isinstance(raw_root_cause, list):
        for index, raw_item in enumerate(raw_root_cause, start=1):
            normalized_item = _normalize_root_cause_item_payload(
                raw_item,
                index=index,
                default_confidence=default_confidence,
                default_certainty=default_certainty,
                default_impact_summary=default_impact_summary,
            )
            if normalized_item is not None:
                normalized_items.append(normalized_item)
    elif isinstance(raw_root_cause, dict):
        normalized_item = _normalize_root_cause_item_payload(
            raw_root_cause,
            index=1,
            default_confidence=default_confidence,
            default_certainty=default_certainty,
            default_impact_summary=default_impact_summary,
        )
        if normalized_item is not None:
            normalized_items.append(normalized_item)
    elif isinstance(raw_root_cause, str) and raw_root_cause.strip():
        # Model-output tolerance: if model returns a legacy single-string root cause, wrap it into one-item array.
        # This branch is parsing robustness only and is not a formal compatibility commitment.
        normalized_item = _normalize_root_cause_item_payload(
            raw_root_cause,
            index=1,
            default_confidence=default_confidence,
            default_certainty=default_certainty,
            default_impact_summary=default_impact_summary,
        )
        if normalized_item is not None:
            normalized_items.append(normalized_item)

    if not normalized_items:
        raw_candidates = payload.get("ranked_candidates")
        if isinstance(raw_candidates, list):
            # Transitional parser tolerance: convert legacy ranked candidates only when root_cause[] is missing.
            # This conversion is temporary fault tolerance and does not define official API semantics.
            for index, raw_item in enumerate(raw_candidates, start=1):
                normalized_item = _normalize_root_cause_item_payload(
                    raw_item,
                    index=index,
                    default_confidence=default_confidence,
                    default_certainty=default_certainty,
                    default_impact_summary=default_impact_summary,
                )
                if normalized_item is not None:
                    normalized_items.append(normalized_item)

    if not normalized_items:
        fallback_title = default_impact_summary or "证据不足，暂无法确认根因"
        normalized_items.append(
            {
                "id": "rc-1",
                "title": fallback_title,
                "layer": "platform",
                "entities": [],
                "confidence": default_confidence,
                "certainty": default_certainty,
                "status": "suspected",
                "evidence_summary": fallback_title,
                "impact_summary": default_impact_summary or fallback_title,
                "distinguishing_verification": None,
                "recommended_fix": None,
            }
        )
    return normalized_items


def _sync_primary_recommended_fix_from_root_cause(payload: dict[str, Any]) -> None:
    """Synchronize top-level recommended_fix with root_cause[0].recommended_fix in-place.

    Purpose:
    - keep one canonical remediation plan source while preserving current top-level consumers.
    Input/Output:
    - input: diagnosis payload dict with normalized `root_cause[]`;
    - output: no return; payload is updated in place.
    Compatibility rationale:
    - current remediation flow remains single-plan and reads top-level `recommended_fix`.
    Why:
    - we intentionally keep first-root-cause-first remediation behavior for this migration phase.
    """
    root_causes = payload.get("root_cause")
    if not isinstance(root_causes, list) or not root_causes:
        payload["recommended_fix"] = None
        return
    primary = root_causes[0]
    if not isinstance(primary, dict):
        payload["recommended_fix"] = None
        return
    top_level_plan = _normalize_inline_recommended_fix_payload(payload.get("recommended_fix"))
    primary_plan = _normalize_inline_recommended_fix_payload(primary.get("recommended_fix"))
    payload["recommended_fix"] = top_level_plan
    primary["recommended_fix"] = primary_plan

    if isinstance(top_level_plan, dict) and not isinstance(primary_plan, dict):
        primary["recommended_fix"] = top_level_plan
        payload["recommended_fix"] = top_level_plan
        return
    if not isinstance(top_level_plan, dict) and isinstance(primary_plan, dict):
        payload["recommended_fix"] = primary_plan
        return
    if not isinstance(top_level_plan, dict) and not isinstance(primary_plan, dict):
        payload["recommended_fix"] = None
        return
    if (
        isinstance(top_level_plan, dict)
        and isinstance(primary_plan, dict)
        and str(top_level_plan.get("plan_id") or "").strip() != str(primary_plan.get("plan_id") or "").strip()
    ):
        payload["recommended_fix"] = primary_plan


def _diagnosis_primary_root_cause(diagnosis: DiagnosisResult) -> dict[str, Any]:
    """Extract the primary root cause from a validated DiagnosisResult."""
    if diagnosis.root_cause:
        primary = diagnosis.root_cause[0]
        return {
            "title": primary.title,
            "layer": primary.layer,
            "entities": list(primary.entities),
            "confidence": primary.confidence,
        }
    return {"title": "", "layer": "platform", "entities": [], "confidence": diagnosis.confidence}


def _normalize_diagnosis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize diagnosis payload to the new root_cause[]-first contract.

    Purpose:
    - clean model output and enforce canonical fields for `DiagnosisResult` validation.
    Input/Output:
    - input: raw diagnosis dict from model/fallback/retry branches;
    - output: normalized dict ready for `DiagnosisResult.model_validate`.
    Compatibility rationale:
    - keeps tolerant parsing for malformed legacy model output but removes legacy fields from final payload.
    Why:
    - all backend and frontend consumers are migrated to read from `root_cause[]`.
    """
    normalized = dict(payload)
    confidence = normalized.get("confidence")
    certainty = str(normalized.get("diagnosis_certainty", "")).strip().lower()
    if isinstance(confidence, (int, float)) and certainty == "confirmed" and float(confidence) < 0.85:
        normalized["diagnosis_certainty"] = "probable"
    next_action = str(normalized.get("next_action") or "").strip()
    if next_action:
        normalized["next_action"] = next_action
    else:
        normalized.pop("next_action", None)
    normalized["root_cause"] = _normalize_root_cause_array_payload(normalized)
    normalized["hypotheses"] = _normalize_hypotheses_payload(normalized)
    _promote_confirmed_hypotheses_to_root_causes(normalized)
    _decouple_root_cause_evidence_summaries(normalized)
    _sync_primary_recommended_fix_from_root_cause(normalized)
    normalized.pop("ranked_candidates", None)
    normalized.pop("root_cause_layer", None)
    normalized.pop("root_cause_entities", None)
    return normalized


def _normalize_hypotheses_payload_legacy(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize hypotheses with primary root-cause context from `root_cause[]`.

    Purpose:
    - keep hypothesis output consistent when diagnosis migrated from single root cause to array form.
    Input/Output:
    - input: diagnosis payload that already passed root-cause normalization;
    - output: normalized hypothesis list with at least one default entry.
    Compatibility rationale:
    - legacy branches remain below as dead fallback, but active path only reads `root_cause[]`.
    Why:
    - we need deterministic hypotheses for UI rendering and plan guardrails.
    """
    raw_hypotheses = payload.get("hypotheses")
    root_causes = payload.get("root_cause")
    primary_root_cause = ""
    primary_layer = "platform"
    if isinstance(root_causes, list):
        for item in root_causes:
            if isinstance(item, dict):
                primary_root_cause = str(item.get("title") or item.get("root_cause") or "").strip()
                primary_layer = str(item.get("layer") or item.get("root_cause_layer") or "platform").strip()
                break
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
            "description": primary_root_cause or "主要根因假设",
            "status": primary_status,
            "evidence_for": [impact_summary] if impact_summary else [],
            "evidence_against": [],
            "confidence": _clamp_confidence(confidence, default=0.9),
        },
        {
            "description": "网络或 RDMA 退化导致时延抬升",
            "status": "eliminated" if primary_layer != "network" else "testing",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "network" else [],
            "confidence": _clamp_confidence(min(confidence, 0.45), default=0.35),
        },
        {
            "description": "业务侧负载突增导致服务排队",
            "status": "testing" if primary_layer != "service" else primary_status,
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "service" else [],
            "confidence": _clamp_confidence(min(confidence, 0.55), default=0.4),
        },
        {
            "description": "硬件热降频或硬件不稳定",
            "status": "testing" if primary_layer == "hardware" else "eliminated",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "hardware" else [],
            "confidence": _clamp_confidence(min(confidence, 0.4), default=0.3),
        },
    ]
    for item in defaults:
        key = item["description"].lower()
        if key in seen_descriptions:
            continue
        normalized.append(item)
        seen_descriptions.add(key)
        if len(normalized) >= 1:
            break
    return normalized

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
        if len(normalized) >= 1:
            break

    return normalized


def _normalize_hypotheses_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize hypotheses using primary context from canonical `root_cause[]`.

    Purpose:
    - ensure hypothesis output remains stable after migrating to multi-root-cause diagnosis.
    Input/Output:
    - input: diagnosis payload that already normalized root-cause data.
    - output: normalized hypotheses list with one guaranteed primary hypothesis.
    Compatibility logic:
    - tolerates malformed hypothesis entries but does not depend on removed legacy root-cause fields.
    Why:
    - report rendering and guardrail checks require deterministic hypothesis structure.
    """
    raw_hypotheses = payload.get("hypotheses")
    root_causes = payload.get("root_cause")
    primary_title = ""
    primary_layer = "platform"
    if isinstance(root_causes, list):
        for item in root_causes:
            if not isinstance(item, dict):
                continue
            primary_title = str(item.get("title") or "").strip()
            primary_layer = str(item.get("layer") or "platform").strip()
            break

    impact_summary = str(payload.get("impact_summary") or "").strip()
    confidence = _clamp_confidence(payload.get("confidence"), default=0.5)
    certainty = str(payload.get("diagnosis_certainty") or "probable").strip().lower()

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
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
            normalized.append(
                {
                    "description": description,
                    "status": status,
                    "evidence_for": [str(v) for v in evidence_for] if isinstance(evidence_for, list) else [],
                    "evidence_against": [str(v) for v in evidence_against] if isinstance(evidence_against, list) else [],
                    "confidence": _clamp_confidence(item.get("confidence"), default=confidence),
                }
            )
            seen.add(description.lower())

    default_status = "confirmed" if certainty == "confirmed" else "testing"
    defaults = [
        {
            "description": primary_title or "Primary root-cause hypothesis",
            "status": default_status,
            "evidence_for": [impact_summary] if impact_summary else [],
            "evidence_against": [],
            "confidence": _clamp_confidence(confidence, default=0.9),
        },
        {
            "description": "Network or RDMA degradation contributes to latency symptoms",
            "status": "eliminated" if primary_layer != "network" else "testing",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "network" else [],
            "confidence": _clamp_confidence(min(confidence, 0.45), default=0.35),
        },
        {
            "description": "Service-side load surge causes queuing and response slowdown",
            "status": "testing" if primary_layer != "service" else default_status,
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "service" else [],
            "confidence": _clamp_confidence(min(confidence, 0.55), default=0.4),
        },
        {
            "description": "Hardware thermal or stability issue amplifies performance variance",
            "status": "testing" if primary_layer == "hardware" else "eliminated",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and primary_layer != "hardware" else [],
            "confidence": _clamp_confidence(min(confidence, 0.4), default=0.3),
        },
    ]
    for item in defaults:
        key = item["description"].lower()
        if key in seen:
            continue
        normalized.append(item)
        seen.add(key)
        if len(normalized) >= 1:
            break

    return normalized


_ROOT_CAUSE_HYPOTHESIS_PROMOTION_CONFIDENCE = 0.8
_CAUSAL_SIGNAL_TOKEN_RE = re.compile(r"[a-z0-9_.:/-]{3,}")
_NETWORK_LAYER_HINTS = ("network", "rdma", "switch", "tc", "netem", "packet", "mtu")
_HARDWARE_LAYER_HINTS = ("temperature", "fan", "thermal", "ecc", "power", "throttle")
_PLATFORM_LAYER_HINTS = ("kubernetes", "k8s", "pod", "node", "scheduler")


def _canonical_cause_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def _extract_causal_signal_tokens(*segments: Any) -> set[str]:
    tokens: set[str] = set()
    for segment in segments:
        text = str(segment or "").lower()
        if not text:
            continue
        for token in _CAUSAL_SIGNAL_TOKEN_RE.findall(text):
            normalized = token.strip("._-:/")
            if len(normalized) < 3:
                continue
            tokens.add(normalized)
    return tokens


def _is_duplicate_root_cause_candidate(
    *,
    description: str,
    evidence_for: list[str],
    existing_root_causes: list[dict[str, Any]],
) -> bool:
    candidate_text = _canonical_cause_text(description)
    candidate_tokens = _extract_causal_signal_tokens(description, *evidence_for)
    for root_item in existing_root_causes:
        if not isinstance(root_item, dict):
            continue
        existing_title = str(root_item.get("title") or root_item.get("root_cause") or "").strip()
        existing_evidence = str(root_item.get("evidence_summary") or "").strip()
        existing_text = _canonical_cause_text(existing_title)
        if candidate_text and existing_text:
            if candidate_text == existing_text:
                return True
            if len(candidate_text) >= 10 and (candidate_text in existing_text or existing_text in candidate_text):
                return True
        existing_tokens = _extract_causal_signal_tokens(existing_title, existing_evidence)
        if candidate_tokens and existing_tokens:
            overlap = candidate_tokens & existing_tokens
            if overlap:
                overlap_ratio_candidate = len(overlap) / max(1, len(candidate_tokens))
                overlap_ratio_existing = len(overlap) / max(1, len(existing_tokens))
                strong_overlap = overlap_ratio_candidate >= 0.7 or overlap_ratio_existing >= 0.7
                has_specific_signal = any(
                    ("_" in token) or any(ch.isdigit() for ch in token) or len(token) >= 8
                    for token in overlap
                )
                if strong_overlap and has_specific_signal:
                    return True
    return False


def _infer_root_cause_layer_from_hypothesis(
    *,
    description: str,
    evidence_for: list[str],
    default_layer: str,
) -> str:
    merged = " ".join([description, *evidence_for]).lower()
    if any(keyword in merged for keyword in _NETWORK_LAYER_HINTS):
        return "network"
    if any(keyword in merged for keyword in _HARDWARE_LAYER_HINTS):
        return "hardware"
    if any(keyword in merged for keyword in _PLATFORM_LAYER_HINTS):
        return "platform"
    return default_layer or "service"


def _promote_confirmed_hypotheses_to_root_causes(payload: dict[str, Any]) -> None:
    """Promote strong confirmed hypotheses into additional root-cause candidates.

    Purpose:
    - keep diagnosis output consistent when the model confirms multiple abnormal factors
      but emits only one root-cause item.
    Input/Output:
    - input: normalized diagnosis payload containing `root_cause[]` and `hypotheses[]`;
    - output: no return; payload is updated in-place by appending extra root causes.
    Compatibility rationale:
    - preserves current first-root-cause remediation behavior while enriching root-cause coverage.
    Why:
    - `hypotheses[].confidence` is not a promotion trigger by default, which can hide
      independently actionable contributing factors.
    """
    root_causes = payload.get("root_cause")
    hypotheses = payload.get("hypotheses")
    if not isinstance(root_causes, list) or not isinstance(hypotheses, list):
        return

    default_confidence = _clamp_confidence(payload.get("confidence"), default=0.5)
    default_impact_summary = str(payload.get("impact_summary") or "").strip()
    default_layer = "service"
    for item in root_causes:
        if isinstance(item, dict):
            candidate_layer = str(item.get("layer") or "").strip().lower()
            if candidate_layer in {"hardware", "network", "os", "platform", "service"}:
                default_layer = candidate_layer
                break

    next_index = len(root_causes) + 1
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        status = str(hypothesis.get("status") or "").strip().lower()
        if status != "confirmed":
            continue
        description = str(hypothesis.get("description") or "").strip()
        if not description:
            continue
        evidence_for_raw = hypothesis.get("evidence_for")
        evidence_for = [str(item).strip() for item in evidence_for_raw] if isinstance(evidence_for_raw, list) else []
        evidence_for = [item for item in evidence_for if item]
        if not evidence_for:
            continue
        confidence = _clamp_confidence(hypothesis.get("confidence"), default=default_confidence)
        if confidence < _ROOT_CAUSE_HYPOTHESIS_PROMOTION_CONFIDENCE:
            continue
        if _is_duplicate_root_cause_candidate(
            description=description,
            evidence_for=evidence_for,
            existing_root_causes=root_causes,
        ):
            continue
        root_causes.append(
            {
                "id": f"rc-{next_index}",
                "title": description,
                "layer": _infer_root_cause_layer_from_hypothesis(
                    description=description,
                    evidence_for=evidence_for,
                    default_layer=default_layer,
                ),
                "entities": [],
                "confidence": confidence,
                "certainty": "confirmed" if confidence >= 0.85 else "probable",
                "status": "contributing",
                "evidence_summary": "；".join(evidence_for[:3]),
                "impact_summary": default_impact_summary or description,
                "distinguishing_verification": "可单独对该因素执行修复并观测指标回落，验证其独立贡献。",
                "recommended_fix": None,
            }
        )
        next_index += 1


def _best_matching_hypothesis_for_root_cause(
    *,
    root_title: str,
    hypotheses: list[dict[str, Any]],
) -> dict[str, Any] | None:
    root_tokens = _extract_causal_signal_tokens(root_title)
    if not root_tokens:
        return None
    best: dict[str, Any] | None = None
    best_score = 0.0
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            continue
        description = str(hypothesis.get("description") or "").strip()
        if not description:
            continue
        status = str(hypothesis.get("status") or "").strip().lower()
        if status not in {"confirmed", "testing"}:
            continue
        hyp_tokens = _extract_causal_signal_tokens(description)
        if not hyp_tokens:
            continue
        overlap = root_tokens & hyp_tokens
        if not overlap:
            continue
        overlap_ratio = len(overlap) / max(1, len(root_tokens))
        confidence = _clamp_confidence(hypothesis.get("confidence"), default=0.5)
        score = overlap_ratio + 0.1 * confidence
        if score > best_score:
            best_score = score
            best = hypothesis
    if best_score < 0.35:
        return None
    return best


def _decouple_root_cause_evidence_summaries(payload: dict[str, Any]) -> None:
    """Align each root-cause evidence summary to its own hypothesis evidence.

    Purpose:
    - prevent mixed multi-factor evidence text from appearing under a single root-cause item.
    Input/Output:
    - input: normalized diagnosis payload with `root_cause[]` and `hypotheses[]`;
    - output: no return; updates `root_cause[].evidence_summary` in-place when safe.
    Compatibility rationale:
    - only applies in multi-root-cause scenarios and preserves existing summaries when
      no confident hypothesis mapping can be found.
    Why:
    - improves explanation clarity after introducing root-cause promotion from hypotheses.
    """
    root_causes = payload.get("root_cause")
    hypotheses = payload.get("hypotheses")
    if not isinstance(root_causes, list) or not isinstance(hypotheses, list):
        return
    if len(root_causes) < 2:
        return

    mapped_hypotheses: list[dict[str, Any]] = [item for item in hypotheses if isinstance(item, dict)]
    for root_item in root_causes:
        if not isinstance(root_item, dict):
            continue
        root_title = str(root_item.get("title") or "").strip()
        if not root_title:
            continue
        matched = _best_matching_hypothesis_for_root_cause(
            root_title=root_title,
            hypotheses=mapped_hypotheses,
        )
        if matched is None:
            continue
        evidence_for_raw = matched.get("evidence_for")
        evidence_for = [str(item).strip() for item in evidence_for_raw] if isinstance(evidence_for_raw, list) else []
        evidence_for = [item for item in evidence_for if item]
        if not evidence_for:
            continue
        root_item["evidence_summary"] = "；".join(evidence_for[:3])


def _clamp_confidence(value: Any, *, default: float) -> float:
    try:
        numeric = float(value)
    except Exception:  # noqa: BLE001
        numeric = default
    return max(0.0, min(1.0, numeric))


_CANARY_ALLOWED_KEYS = frozenset({
    "enabled", "target_percentage", "monitor_duration",
    "success_criteria", "criteria_mode", "max_batches",
    "auto_rollback_on_regression", "progressive",
})


def _normalize_canary_config(
    *,
    raw_canary: Any,
    root_cause_entities: list[str],
    normalized_steps: list[dict[str, Any]],
    force_canary: bool = False,
) -> dict[str, Any] | None:
    """Normalize and optionally auto-fill canary config for a remediation plan.

    - If LLM returned a canary dict, validate and return it.
    - If >= 2 affected entities and no canary, auto-fill a default config.
    - Otherwise return None.
    """
    # Collect unique targets from step params.
    target_keys = ("node", "target", "service_id", "entity_id")
    targets: set[str] = set()
    process_targets: set[str] = set()
    for step in normalized_steps:
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        for key in target_keys:
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                normalized_value = value.strip()
                targets.add(normalized_value)
                if key == "entity_id" and normalized_value.lower().startswith("proc:"):
                    process_targets.add(normalized_value)
        if str(step.get("tool", "") or "").strip() == "kill_process":
            process_entity = _resolve_kill_process_entity_id(params)
            if process_entity:
                process_targets.add(process_entity)
                targets.add(process_entity)

    for entity in root_cause_entities:
        entity_str = str(entity).strip()
        if entity_str:
            targets.add(entity_str)

    if isinstance(raw_canary, dict):
        canary = {k: v for k, v in raw_canary.items() if k in _CANARY_ALLOWED_KEYS}
        canary.setdefault("enabled", True)
        canary.setdefault("progressive", True)
        if force_canary:
            process_count = len(process_targets) or len(targets) or 1
            if process_count > 6:
                _llm_logger.warning(
                    "force canary process count exceeds demo cap for session config: count=%s (capped to 6)",
                    process_count,
                )
                process_count = 6
            canary["enabled"] = True
            canary["target_percentage"] = round(1.0 / process_count, 4)
            canary["max_batches"] = process_count
            canary["progressive"] = False
            canary.setdefault("monitor_duration", 60)
        sanitized = _sanitize_inline_canary_payload(canary, step_count=max(1, len(normalized_steps)))
        return sanitized if sanitized is not None else canary

    if force_canary:
        process_count = len(process_targets) or len(targets)
        if process_count > 6:
            _llm_logger.warning(
                "force canary process count exceeds demo cap for generated plan: count=%s (capped to 6)",
                process_count,
            )
            process_count = 6
        if process_count >= 1:
            forced_canary = {
                "enabled": True,
                "target_percentage": round(1.0 / process_count, 4),
                "monitor_duration": 60,
                "success_criteria": [],
                "criteria_mode": "all",
                "max_batches": process_count,
                "auto_rollback_on_regression": process_count >= 2,
                "progressive": False,
            }
            sanitized = _sanitize_inline_canary_payload(forced_canary, step_count=max(1, process_count))
            return sanitized if sanitized is not None else forced_canary

    if len(targets) >= 2:
        default_canary = {
            "enabled": True,
            "target_percentage": 0.1,
            "monitor_duration": 120,
            "success_criteria": [],
            "criteria_mode": "all",
            "max_batches": 3,
            "auto_rollback_on_regression": True,
            "progressive": True,
        }
        sanitized = _sanitize_inline_canary_payload(default_canary, step_count=max(1, len(normalized_steps)))
        return sanitized if sanitized is not None else default_canary

    return None


def _normalize_remediation_plan_payload(
    *,
    raw_plan: dict[str, Any] | None,
    diagnosis: DiagnosisResult,
    session_id: str,
    registry: ToolRegistry | None = None,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
    alert_name: str | None = None,
) -> RemediationPlan | None:
    """Normalize remediation plan payload while enforcing first-root-cause-first behavior.

    Purpose:
    - validate/repair LLM remediation plan into executable `RemediationPlan`.
    Input/Output:
    - input: raw plan payload plus validated diagnosis context and tool metadata;
    - output: validated `RemediationPlan` or `None` when plan is unsafe/invalid.
    Compatibility rationale:
    - this phase intentionally keeps a single plan path and binds it to `root_cause[0]`.
    Why:
    - avoids introducing multi-plan approval flow while diagnosis migrates to multi-root-cause.
    """
    if raw_plan is None:
        return None
    if not isinstance(raw_plan, dict):
        return None

    candidate = dict(raw_plan)
    primary_root_cause = _diagnosis_primary_root_cause(diagnosis)
    primary_root_title = str(primary_root_cause.get("title") or "").strip()
    primary_root_entities = [str(item).strip() for item in (primary_root_cause.get("entities") or []) if str(item).strip()]
    steps = candidate.get("steps")
    if steps is None:
        steps = candidate.get("actions")
    normalized_steps: list[dict[str, Any]] = []
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                continue
            step_tool_name = str(step.get("tool", "") or "").strip()
            if step_tool_name and registry is not None:
                try:
                    step_tool_def = registry.get_tool(step_tool_name)
                    if step_tool_def.safety_level == SafetyLevel.READ_ONLY:
                        _llm_logger.debug(
                            "skipping read-only tool %r in remediation plan for session %s",
                            step_tool_name,
                            session_id,
                        )
                        continue
                except Exception:  # noqa: BLE001
                    pass
            normalized_step = dict(step)
            normalized_step.setdefault("step_id", index)
            normalized_step.setdefault(
                "description",
                f"针对 {primary_root_title or '主根因'} 的候选修复步骤 {index}",
            )
            normalized_step.setdefault("params", {})
            invalid_reason = _normalize_step_params_in_place(
                normalized_step,
                diagnosis=diagnosis,
                registry=registry,
                tool_runs=tool_runs,
                variables=variables,
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

    # Re-number step_id after filtering (e.g., read-only tool removal may leave gaps).
    for re_index, step in enumerate(normalized_steps, start=1):
        step["step_id"] = re_index

    candidate["steps"] = normalized_steps
    candidate.pop("actions", None)
    candidate.setdefault(
        "plan_id",
        f"proposal-{(session_id or 'session')[:8]}",
    )
    candidate.setdefault("root_cause", primary_root_title or "主根因")
    candidate.setdefault(
        "description",
        "基于现有诊断证据生成的 proposal-only 修复方案，当前尚未执行任何写入动作。",
    )
    candidate.setdefault("estimated_impact", diagnosis.impact_summary)
    candidate.setdefault("confidence", _normalize_plan_confidence(diagnosis.confidence))
    candidate.setdefault("priority", _normalize_plan_priority(diagnosis.triage_priority))
    candidate.setdefault("safety_level", "high")
    force_canary = _is_force_canary_alert_name(str(alert_name or ""))
    raw_canary_provided = isinstance(candidate.get("canary"), dict)

    # ── Canary normalization ───────────────────────────────────────────
    candidate["canary"] = _normalize_canary_config(
        raw_canary=candidate.get("canary"),
        root_cause_entities=primary_root_entities,
        normalized_steps=normalized_steps,
        force_canary=force_canary,
    )
    if isinstance(candidate.get("canary"), dict):
        sanitized_canary = _sanitize_inline_canary_payload(candidate.get("canary"), step_count=max(1, len(normalized_steps)))
        if sanitized_canary is not None:
            candidate["canary"] = sanitized_canary
    # If canary validation fails (e.g. missing criteria), drop it silently.
    if candidate["canary"] is not None:
        try:
            from sre_agent.models.remediation import CanaryConfig
            CanaryConfig.model_validate(candidate["canary"])
        except Exception:  # noqa: BLE001
            candidate["canary"] = None
    if force_canary:
        normalized_canary = candidate.get("canary")
        batch_total = 0
        if isinstance(normalized_canary, dict):
            try:
                batch_total = int(normalized_canary.get("max_batches", 0) or 0)
            except Exception:  # noqa: BLE001
                batch_total = 0
        _llm_logger.info(
            "force canary normalization applied: session_id=%s force_canary=%s force_canary_reason=ttft_match canary_source=%s batch_total=%s",
            session_id,
            True,
            "provided" if raw_canary_provided else "forced_default",
            batch_total,
        )

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


def _resolve_ttft_target_node(
    *,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> str:
    source_runs = list(tool_runs or [])
    for run in reversed(source_runs):
        if not isinstance(run, dict):
            continue
        if str(run.get("tool", "")).strip() != "gpu.get_processes":
            continue
        params = run.get("params", {})
        if not isinstance(params, dict):
            continue
        node = str(params.get("node", "") or "").strip()
        if node:
            return node
    source_variables = dict(variables or {})
    for key in ("node", "node_ip", "instance"):
        value = str(source_variables.get(key, "") or "").strip()
        if value and value.lower() not in _PLACEHOLDER_VALUES:
            return value
    return ""


def _resolve_kill_process_entity_id(params: dict[str, Any]) -> str | None:
    raw_entity = str(params.get("entity_id", "") or "").strip()
    if raw_entity:
        return raw_entity

    pid_raw = params.get("pid")
    try:
        parsed_pid = int(pid_raw)
        if parsed_pid > 0:
            return f"proc:{parsed_pid}"
    except Exception:  # noqa: BLE001
        pass

    pid_or_name = str(params.get("pid_or_name", "") or "").strip()
    if pid_or_name:
        return f"proc:{pid_or_name}"

    process_name = str(params.get("process_name", "") or "").strip()
    if process_name:
        return f"proc:{process_name}"
    return None


def _is_gpu_contention_suspect_process(process_text: str) -> bool:
    """Detect GPU-burn style contention processes for TTFT GPU root-cause planning.

    Purpose:
    - identify non-serving processes that frequently occupy GPU resources and should
      be considered as independent remediation targets.
    Input/Output:
    - input: raw process text from gpu.get_processes rows;
    - output: boolean indicating whether the process is a GPU contention suspect.
    Compatibility rationale:
    - complements existing TTFT load-generator policy without changing its allowlist semantics.
    Why:
    - GPU burn workloads (for example `fi_gpu_burn_*`) are independent of external load generators
      and need their own candidate remediation plan.
    """
    lowered = str(process_text or "").strip().lower()
    if not lowered:
        return False
    deny_tokens = (
        "vllm::worker",
        "vllm worker",
        "cuda_mps",
    )
    if any(token in lowered for token in deny_tokens):
        return False
    allow_tokens = (
        "fi_gpu_burn",
        "gpu_burn",
        "burn_gpu",
        "gpu_contention",
    )
    return any(token in lowered for token in allow_tokens)


def _resolve_process_verification_pattern(process_text: str) -> str | None:
    """Resolve a process.find verification pattern for kill-process steps."""
    allowed = extract_ttft_verification_pattern(process_text)
    if allowed:
        return allowed
    lowered = str(process_text or "").strip().lower()
    if "fi_gpu_burn" in lowered:
        return "fi_gpu_burn"
    if "gpu_burn" in lowered:
        return "gpu_burn"
    if "gpu_contention" in lowered:
        return "gpu_contention"
    return None


def _root_cause_process_tokens(root_cause: Any) -> set[str]:
    """Extract process-level matching tokens from one root-cause item."""
    if not isinstance(root_cause, dict):
        return set()
    raw_segments: list[str] = []
    raw_segments.append(str(root_cause.get("title") or ""))
    raw_segments.append(str(root_cause.get("evidence_summary") or ""))
    entities = root_cause.get("entities")
    if isinstance(entities, list):
        for entity in entities:
            raw_segments.append(str(entity or ""))
    token_re = re.compile(r"[a-z0-9_.-]{4,}")
    ignored = {
        "worker",
        "service",
        "request",
        "process",
        "gpu",
        "ttft",
        "token",
        "python",
        "vllm",
    }
    tokens: set[str] = set()
    for segment in raw_segments:
        lowered = str(segment or "").strip().lower()
        if not lowered:
            continue
        for token in token_re.findall(lowered):
            normalized = token.strip("._-")
            if len(normalized) < 4:
                continue
            if normalized in ignored:
                continue
            if re.fullmatch(r"\d+(?:\.\d+)+", normalized):
                continue
            tokens.add(normalized)
    return tokens


def _build_ttft_kill_process_plan_for_root_cause(
    *,
    diagnosis: DiagnosisResult,
    root_cause_index: int,
    evidence_signals: dict[str, Any],
    session_id: str,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one process-level remediation plan candidate for a specific root-cause item.

    Purpose:
    - generate independent remediation candidates in multi-root-cause TTFT scenarios.
    Input/Output:
    - input: diagnosis, root-cause index, evidence signals, and runtime context;
    - output: raw remediation plan dict or None.
    Compatibility rationale:
    - keeps current approval flow unchanged by producing per-root-cause candidate plans
      while top-level execution still uses primary plan.
    Why:
    - factors like `fi_gpu_burn` and `load_simulator` can be independently remediated and
      should not be merged into a single fix.
    """
    if root_cause_index < 0 or root_cause_index >= len(diagnosis.root_cause):
        return None
    suspects = _collect_ttft_suspect_processes(evidence_signals.get("ttft_suspect_processes"), max_items=12)
    if not suspects:
        return None
    root_item = diagnosis.root_cause[root_cause_index]
    root_payload = root_item.model_dump(mode="json")
    tokens = _root_cause_process_tokens(root_payload)
    if not tokens:
        return None

    default_node = _resolve_ttft_target_node(tool_runs=tool_runs, variables=variables)
    matched: list[dict[str, Any]] = []
    for suspect in suspects:
        if not isinstance(suspect, dict):
            continue
        process_name = str(suspect.get("process_name", "") or "").strip()
        lowered = process_name.lower()
        if not lowered:
            continue
        if any(token in lowered for token in tokens):
            matched.append(suspect)
            continue
        if _is_gpu_contention_suspect_process(lowered) and any(token in {"fi_gpu_burn", "gpu_burn", "gpu_contention"} for token in tokens):
            matched.append(suspect)

    if not matched:
        return None

    steps: list[dict[str, Any]] = []
    for idx, suspect in enumerate(matched[:6], start=1):
        step_node = str(suspect.get("node", "") or "").strip() or default_node
        if not step_node:
            continue
        process_name = str(suspect.get("process_name", "") or "").strip()
        pid = suspect.get("pid")
        params: dict[str, Any] = {"node": step_node, "signal": "TERM"}
        target_label = ""
        target_token = ""
        if isinstance(pid, int) and pid > 0:
            params["pid"] = pid
            target_label = f"PID {pid}"
            target_token = str(pid)
        elif process_name:
            params["pid_or_name"] = process_name
            target_label = process_name
            target_token = process_name
        else:
            continue
        params["entity_id"] = f"proc:{target_token}"
        verify_pattern = _resolve_process_verification_pattern(process_name or target_token)
        verification: dict[str, Any] = {"method": "wait", "wait_seconds": 30}
        if verify_pattern:
            verification = {
                "method": "tool_call",
                "tool": "process.find",
                "tool_params": {"pattern": verify_pattern, "node": step_node},
                "condition": {"field": "count", "operator": "==", "value": 0},
                "wait_seconds": 30,
            }
        steps.append(
            {
                "step_id": idx,
                "description": f"Terminate suspect process {target_label} on node {step_node}",
                "tool": "kill_process",
                "params": params,
                "verification": verification,
                "timeout": 60,
            }
        )
    if not steps:
        return None

    plan_id = f"proposal-{(session_id or 'session')[:8]}-rc{root_cause_index + 1}-kill"
    return {
        "plan_id": plan_id,
        "root_cause": root_item.title,
        "description": f"Proposal-only remediation for root cause #{root_cause_index + 1}: {root_item.title}",
        "steps": steps,
        "estimated_impact": root_item.impact_summary or diagnosis.impact_summary,
        "confidence": _normalize_plan_confidence(max(0.55, float(root_item.confidence))),
        "priority": _normalize_plan_priority(diagnosis.triage_priority),
        "safety_level": "high",
        "canary": {
            "enabled": True,
            "target_percentage": round(1.0 / len(steps), 4),
            "monitor_duration": 60,
            "success_criteria": [],
            "criteria_mode": "all",
            "max_batches": len(steps),
            "auto_rollback_on_regression": len(steps) >= 2,
            "progressive": False,
        },
    }


def _enrich_ttft_root_causes_from_evidence(
    payload: dict[str, Any],
    evidence_signals: dict[str, Any],
) -> None:
    """Ensure independent TTFT factors are represented as independent root-cause items.

    When both GPU contention suspects (for example `fi_gpu_burn_*`) and external
    load-generator suspects (for example `load_simulator`) are present, keep them as
    separate candidates if they are independently remediable.
    """
    root_causes = payload.get("root_cause")
    if not isinstance(root_causes, list) or not root_causes:
        return
    suspects = _collect_ttft_suspect_processes(evidence_signals.get("ttft_suspect_processes"), max_items=12)
    if not suspects:
        return

    gpu_suspects: list[dict[str, Any]] = []
    load_suspects: list[dict[str, Any]] = []
    for suspect in suspects:
        if not isinstance(suspect, dict):
            continue
        process_name = str(suspect.get("process_name", "") or "").strip()
        if not process_name:
            continue
        lowered = process_name.lower()
        if _is_gpu_contention_suspect_process(lowered):
            gpu_suspects.append(suspect)
            continue
        if is_ttft_suspect_process(process_name):
            load_suspects.append(suspect)

    if not gpu_suspects or not load_suspects:
        return

    def _contains_gpu_factor(item: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("evidence_summary") or ""),
                " ".join(str(v) for v in (item.get("entities") or []) if isinstance(v, str)),
            ]
        ).lower()
        return any(token in text for token in ("fi_gpu_burn", "gpu_burn", "gpu contention", "gpu_contention"))

    def _contains_load_factor(item: dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("evidence_summary") or ""),
                " ".join(str(v) for v in (item.get("entities") or []) if isinstance(v, str)),
            ]
        ).lower()
        return ("load_simulator" in text) or ("python -m" in text and "load" in text) or ("external load" in text)

    existing_gpu = any(_contains_gpu_factor(item) for item in root_causes if isinstance(item, dict))
    existing_load = any(_contains_load_factor(item) for item in root_causes if isinstance(item, dict))
    if existing_gpu and existing_load:
        return

    base_conf = _clamp_confidence(payload.get("confidence"), default=0.6)
    gpu_conf = _clamp_confidence(max(0.85, base_conf), default=0.85)
    load_conf = _clamp_confidence(max(0.8, base_conf - 0.05), default=0.8)

    first_gpu = gpu_suspects[0]
    first_load = load_suspects[0]
    gpu_node = str(first_gpu.get("node", "") or "").strip()
    load_node = str(first_load.get("node", "") or "").strip()
    gpu_process = str(first_gpu.get("process_name", "") or "").strip() or "fi_gpu_burn"
    load_process = str(first_load.get("process_name", "") or "").strip() or "load_simulator"

    gpu_item: dict[str, Any] = {
        "id": "rc-gpu-contention",
        "title": "GPU contention caused by gpu_burn process",
        "layer": "service",
        "entities": [v for v in [gpu_node, gpu_process] if v],
        "confidence": gpu_conf,
        "certainty": "confirmed" if gpu_conf >= 0.85 else "probable",
        "status": "contributing",
        "evidence_summary": (
            f"gpu.get_processes detected suspect process {gpu_process}" + (f" on {gpu_node}" if gpu_node else "")
        ),
        "impact_summary": "GPU contention consumes compute resources and elevates TTFT latency.",
        "distinguishing_verification": "Terminate gpu_burn process and observe TTFT drop independently.",
        "recommended_fix": None,
    }
    load_item: dict[str, Any] = {
        "id": "rc-external-load",
        "title": "External load_simulator process continuously injects requests",
        "layer": "service",
        "entities": [v for v in [load_node, "load_simulator"] if v],
        "confidence": load_conf,
        "certainty": "confirmed" if load_conf >= 0.85 else "probable",
        "status": "contributing",
        "evidence_summary": (
            f"process.find detected external load process {load_process}" + (f" on {load_node}" if load_node else "")
        ),
        "impact_summary": "External request pressure amplifies service queuing and TTFT degradation.",
        "distinguishing_verification": "Stop load_simulator and observe TTFT change independently.",
        "recommended_fix": None,
    }

    primary = root_causes[0] if isinstance(root_causes[0], dict) else {}
    primary_id = str(primary.get("id", "") or "").strip().lower()
    primary_conf = _clamp_confidence(primary.get("confidence"), default=base_conf)
    primary_status = str(primary.get("status", "") or "").strip().lower()
    primary_generic_fallback = (
        len(root_causes) == 1
        and (
            primary_id.startswith("rc-fallback")
            or (primary_conf <= 0.45 and primary_status in {"suspected", "monitoring"})
        )
    )

    if primary_generic_fallback:
        top_level_plan = payload.get("recommended_fix")
        plan_text = ""
        if isinstance(top_level_plan, dict):
            try:
                plan_text = json.dumps(top_level_plan, ensure_ascii=False).lower()
            except Exception:  # noqa: BLE001
                plan_text = str(top_level_plan).lower()
        prefer_load_primary = "load_simulator" in plan_text
        prefer_gpu_primary = any(token in plan_text for token in ("fi_gpu_burn", "gpu_burn", "gpu_contention"))
        if prefer_load_primary and not prefer_gpu_primary:
            payload["root_cause"] = [load_item, gpu_item]
        else:
            payload["root_cause"] = [gpu_item, load_item]
        if isinstance(top_level_plan, dict):
            payload["root_cause"][0]["recommended_fix"] = top_level_plan
        _sync_primary_recommended_fix_from_root_cause(payload)
        return

    def _next_id() -> str:
        return f"rc-{len(root_causes) + 1}"

    if not existing_gpu:
        candidate = dict(gpu_item)
        candidate["id"] = _next_id()
        root_causes.append(candidate)

    if not existing_load:
        candidate = dict(load_item)
        candidate["id"] = _next_id()
        root_causes.append(candidate)

    _sync_primary_recommended_fix_from_root_cause(payload)


def _diagnosis_view_with_primary_index(diagnosis: DiagnosisResult, index: int) -> DiagnosisResult:
    """Build a temporary diagnosis view whose primary root cause is the selected index."""
    if index <= 0 or index >= len(diagnosis.root_cause):
        return diagnosis
    reordered = [diagnosis.root_cause[index], *diagnosis.root_cause[:index], *diagnosis.root_cause[index + 1 :]]
    return diagnosis.model_copy(update={"root_cause": reordered, "recommended_fix": reordered[0].recommended_fix})


def _attach_per_root_cause_recommended_fixes(
    *,
    diagnosis: DiagnosisResult,
    primary_plan: RemediationPlan | None,
    evidence_signals: dict[str, Any],
    session_id: str,
    registry: ToolRegistry | None,
    tool_runs: list[dict[str, Any]] | None,
    variables: dict[str, Any] | None,
    alert_name: str | None,
) -> tuple[DiagnosisResult, RemediationPlan | None]:
    """Attach independent remediation plan candidates to each root cause when possible.

    Purpose:
    - produce multi-root-cause remediation candidates without breaking single-plan approval flow.
    Input/Output:
    - input: diagnosis result, current primary plan, evidence signals, and runtime metadata;
    - output: updated diagnosis plus selected primary plan for top-level approval path.
    Compatibility rationale:
    - top-level `recommended_fix` remains tied to `root_cause[0]`, while secondary plans are stored
      in `root_cause[i].recommended_fix` as candidate proposals.
    Why:
    - enables independent remediation for factors such as GPU burn and external load generators.
    """
    if not diagnosis.root_cause:
        return diagnosis, primary_plan
    updated_root_causes = list(diagnosis.root_cause)
    selected_primary_plan = primary_plan

    for index, root_item in enumerate(updated_root_causes):
        existing_plan = root_item.recommended_fix
        if existing_plan is not None and index > 0:
            continue
        # For primary root cause, always try to build a root-cause-specific plan first.
        # This avoids reusing a generic plan that may target a different contributing factor.
        if existing_plan is not None and index == 0 and selected_primary_plan is None:
            selected_primary_plan = existing_plan
        raw_candidate = _build_ttft_kill_process_plan_for_root_cause(
            diagnosis=diagnosis,
            root_cause_index=index,
            evidence_signals=evidence_signals,
            session_id=session_id,
            tool_runs=tool_runs,
            variables=variables,
        )
        if raw_candidate is None:
            if index == 0 and existing_plan is None and selected_primary_plan is not None:
                updated_root_causes[index] = root_item.model_copy(update={"recommended_fix": selected_primary_plan})
            continue
        scoped_diagnosis = _diagnosis_view_with_primary_index(diagnosis, index)
        normalized_candidate = _normalize_remediation_plan_payload(
            raw_plan=raw_candidate,
            diagnosis=scoped_diagnosis,
            session_id=session_id,
            registry=registry,
            tool_runs=tool_runs,
            variables=variables,
            alert_name=alert_name,
        )
        if normalized_candidate is None:
            if index == 0 and existing_plan is None and selected_primary_plan is not None:
                updated_root_causes[index] = root_item.model_copy(update={"recommended_fix": selected_primary_plan})
            continue
        updated_root_causes[index] = root_item.model_copy(update={"recommended_fix": normalized_candidate})
        if index == 0:
            selected_primary_plan = normalized_candidate

    updated_diagnosis = diagnosis.model_copy(update={"root_cause": updated_root_causes})
    if selected_primary_plan is not None:
        updated_diagnosis = updated_diagnosis.model_copy(update={"recommended_fix": selected_primary_plan})
    return updated_diagnosis, selected_primary_plan


def _collect_ttft_suspect_processes(raw_items: Any, *, max_items: int = 6) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        process_name = str(item.get("process_name", "") or "").strip()
        node = str(item.get("node", "") or "").strip()
        pid: int | None = None
        try:
            parsed_pid = int(item.get("pid"))
            if parsed_pid > 0:
                pid = parsed_pid
        except Exception:  # noqa: BLE001
            pid = None
        if not process_name and pid is None:
            continue
        node_prefix = node.lower()
        dedupe_key = (
            f"{node_prefix}|pid:{pid}"
            if pid is not None
            else f"{node_prefix}|name:{process_name.lower()}"
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        entry: dict[str, Any] = {
            "pid": pid,
            "process_name": process_name,
            "memory_mib": item.get("memory_mib"),
        }
        if node:
            entry["node"] = node
        normalized.append(entry)
        if len(normalized) >= max_items:
            break
    return normalized


def _build_ttft_kill_process_plan_candidate(
    *,
    diagnosis: DiagnosisResult,
    evidence_signals: dict[str, Any],
    session_id: str,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build TTFT-focused kill-process plan candidate driven by the primary root cause.

    Purpose:
    - generate a safe plan candidate from detected suspect processes in TTFT scenarios.
    Input/Output:
    - input: diagnosis context, evidence signals, and optional runtime probes;
    - output: remediation plan dict or None when candidate cannot be safely formed.
    Compatibility rationale:
    - still returns a single plan because this migration intentionally keeps first-root-cause execution.
    Why:
    - avoids introducing multi-root-cause multi-plan orchestration in the same release.
    """
    primary_root = _diagnosis_primary_root_cause(diagnosis)
    primary_root_title = str(primary_root.get("title") or "").strip() or "主根因"
    suspect_present = bool(evidence_signals.get("ttft_suspect_process_present"))
    suspect_processes = _collect_ttft_suspect_processes(evidence_signals.get("ttft_suspect_processes"))
    if not suspect_present or not suspect_processes:
        return None

    default_node = _resolve_ttft_target_node(tool_runs=tool_runs, variables=variables)

    steps: list[dict[str, Any]] = []
    filtered_targets = 0
    for index, suspect in enumerate(suspect_processes, start=1):
        step_node = str(suspect.get("node", "") or "").strip() or default_node
        if not step_node:
            continue
        process_name = str(suspect.get("process_name", "") or "").strip()
        pid = suspect.get("pid")
        step_params: dict[str, Any] = {
            "node": step_node,
            "signal": "TERM",
        }
        target_token = ""
        target_label = ""
        if isinstance(pid, int) and pid > 0:
            step_params["pid"] = pid
            target_token = str(pid)
            target_label = f"PID {pid}"
        elif process_name:
            step_params["pid_or_name"] = process_name
            target_token = process_name
            target_label = process_name
        else:
            continue
        step_params["entity_id"] = f"proc:{target_token}"
        verification_pattern = extract_ttft_verification_pattern(process_name or target_token)
        if not verification_pattern:
            filtered_targets += 1
            _llm_logger.info(
                "ttft suspect target filtered in plan build: suspect_filter_reason=%s suspect_source_tool=%s pid=%s process=%s node=%s",
                "verification_pattern_not_whitelisted",
                "ttft_suspect_processes",
                pid,
                process_name,
                step_node,
            )
            continue
        steps.append(
            {
                "step_id": index,
                "description": f"在节点 {step_node} 终止可疑负载进程 {target_label}",
                "tool": "kill_process",
                "params": step_params,
                "verification": {
                    "method": "tool_call",
                    "tool": "process.find",
                    "tool_params": {"pattern": verification_pattern, "node": step_node},
                    "wait_seconds": 30,
                    "condition": {"field": "count", "operator": "==", "value": 0},
                },
                "timeout": 60,
            }
        )
    if not steps:
        if filtered_targets > 0:
            _llm_logger.info(
                "ttft auto kill plan skipped: all suspect targets filtered; filtered_count=%s session_id=%s",
                filtered_targets,
                session_id,
            )
        return None

    step_count = len(steps)
    canary: dict[str, Any] = {
        "enabled": True,
        "target_percentage": round(1.0 / step_count, 4),
        "monitor_duration": 60,
        "success_criteria": [],
        "criteria_mode": "all",
        "max_batches": step_count,
        "auto_rollback_on_regression": step_count >= 2,
        "progressive": False,
    }

    payload: dict[str, Any] = {
        "plan_id": f"proposal-{(session_id or 'session')[:8]}-ttft-kill",
        "root_cause": primary_root_title,
        "description": "识别到可疑压测/模拟负载进程，按进程粒度灰度终止并复核 TTFT 与告警状态。",
        "steps": steps,
        "estimated_impact": "释放异常争用，预期 TTFT 回落并推动告警恢复。",
        "confidence": _normalize_plan_confidence(max(0.55, float(diagnosis.confidence))),
        "priority": _normalize_plan_priority(diagnosis.triage_priority),
        "safety_level": "high",
    }
    payload["canary"] = canary
    return payload


def _normalize_step_params_in_place(
    step: dict[str, Any],
    *,
    diagnosis: DiagnosisResult,
    registry: ToolRegistry | None,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> str | None:
    tool_name = str(step.get("tool") or "").strip()
    params = step.get("params")
    if not isinstance(params, dict):
        return "missing_required_params: params must be an object"
    node_param_error = _normalize_node_param_in_place(params)
    if node_param_error:
        return node_param_error

    if tool_name == "k8s.delete_pod":
        pod_selector = params.pop("pod_selector", None)
        if pod_selector is not None and "label_selector" not in params:
            params["label_selector"] = pod_selector

    if _step_looks_like_tc_qdisc_cleanup(step) and tool_name != "network.clear_tc_qdisc":
        return "tool_not_supported_for_intended_action: tc_qdisc_cleanup_requires_network.clear_tc_qdisc"

    if tool_name == "network.clear_tc_qdisc":
        iface = params.get("iface")
        # 鍗犱綅鍊艰涓虹己澶?
        if _is_placeholder_param(iface):
            params.pop("iface", None)

        if not params.get("iface"):
            # Inference order: step text -> tc qdisc tool run key_fields -> runtime variables
            candidates = _collect_tc_iface_candidates(
                step,
                diagnosis=diagnosis,
                tool_runs=tool_runs,
                variables=variables,
            )
            if len(candidates) == 1:
                params["iface"] = candidates[0]
            elif len(candidates) > 1:
                return "iface_inference_ambiguous"
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

    if tool_name == "kill_process":
        normalized_signal = _normalize_kill_signal(params)
        if normalized_signal:
            params["signal"] = normalized_signal
        process_entity_id = _resolve_kill_process_entity_id(params)
        if process_entity_id:
            params["entity_id"] = process_entity_id

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
            node_param_error = _normalize_node_param_in_place(params)
            if node_param_error:
                return node_param_error

    missing = [field for field in required if field not in params or _is_blank_param(params.get(field))]
    if missing:
        return f"missing_required_params: {missing}"
    return None


_PLACEHOLDER_VALUES = frozenset({"unknown", "n/a", "none", "-", "--", "null", ""})


def _is_placeholder_param(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _PLACEHOLDER_VALUES
    return False


def _is_blank_param(value: Any) -> bool:
    return _is_placeholder_param(value)


def _normalize_kill_signal(params: dict[str, Any]) -> str:
    raw_signal = str(params.get("signal") or "").strip()
    if not raw_signal:
        return ""
    normalized = raw_signal.upper()
    if normalized.startswith("SIG") and len(normalized) > 3:
        normalized = normalized[3:]
    return normalized


def _normalize_node_param_in_place(params: dict[str, Any]) -> str | None:
    raw_node = params.get("node")
    if not isinstance(raw_node, str) or not raw_node.strip():
        return None

    inventory_names, host_to_name = _load_inventory_node_mapping()
    if not inventory_names and not host_to_name:
        return None

    normalized_node, reason = normalize_node_identifier(
        raw_node,
        inventory_names=inventory_names,
        host_to_name=host_to_name,
    )
    if normalized_node:
        params["node"] = normalized_node
        return None
    return f"missing_required_params: ['node'] ({reason or 'node_not_in_inventory'})"


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


def _collect_tc_iface_candidates(
    step: dict[str, Any],
    diagnosis: Any,
    *,
    tool_runs: list[dict[str, Any]] | None = None,
    variables: dict[str, Any] | None = None,
) -> list[str]:
    seen: set[str] = set()
    candidates: list[str] = []

    def _add(iface: str | None) -> None:
        if iface and iface.lower() not in _PLACEHOLDER_VALUES and iface not in seen:
            seen.add(iface)
            candidates.append(iface)

    # 鏉ユ簮 1锛歴tep 鏂囨湰涓殑 dev <iface>
    _add(_infer_tc_iface(step))
    if candidates:
        return candidates

    step_params = step.get("params", {})
    expected_node = ""
    expected_parent = ""
    expected_handle = ""
    if isinstance(step_params, dict):
        expected_node = str(step_params.get("node", "") or "").strip()
        expected_parent = str(step_params.get("parent", "") or "").strip()
        expected_handle = str(step_params.get("handle", "") or "").strip()
    if not expected_parent:
        expected_parent = str(_extract_token_from_step(step, token_name="parent") or "").strip()
    if not expected_handle:
        expected_handle = str(_extract_token_from_step(step, token_name="handle") or "").strip()

    # 来源 2：tool_runs 中 network.get_tc_qdisc 的 key_fields.iface
    source_tool_runs = list(tool_runs or [])
    diag_dict = diagnosis if isinstance(diagnosis, dict) else None
    if not source_tool_runs and diag_dict is not None:
        raw_runs = diag_dict.get("tool_runs", [])
        if isinstance(raw_runs, list):
            source_tool_runs = [item for item in raw_runs if isinstance(item, dict)]

    if source_tool_runs:
        strict_runs: list[dict[str, Any]] = []
        relaxed_runs: list[dict[str, Any]] = []
        for run in source_tool_runs:
            if str(run.get("tool", "")).strip() != "network.get_tc_qdisc":
                continue
            run_params = run.get("params")
            run_data = run.get("data")
            run_fields = run.get("key_fields")
            run_node = ""
            if isinstance(run_params, dict):
                run_node = str(run_params.get("node", "") or "").strip()
            if not run_node and isinstance(run_data, dict):
                run_node = str(run_data.get("node", "") or "").strip()

            run_parent = str(run_fields.get("parent", "") or "").strip() if isinstance(run_fields, dict) else ""
            run_handle = str(run_fields.get("handle", "") or "").strip() if isinstance(run_fields, dict) else ""

            node_match = bool(expected_node and run_node and run_node == expected_node)
            parent_match = bool(expected_parent and run_parent and run_parent == expected_parent)
            handle_match = bool(expected_handle and run_handle and run_handle == expected_handle)

            if node_match and (parent_match or handle_match):
                strict_runs.append(run)
            else:
                relaxed_runs.append(run)

        for run in strict_runs:
            key_fields = run.get("key_fields")
            if isinstance(key_fields, dict):
                _add(key_fields.get("iface"))
            data = run.get("data")
            if isinstance(data, dict):
                _add(data.get("iface"))
        if candidates:
            return candidates

        for run in relaxed_runs:
            key_fields = run.get("key_fields")
            if isinstance(key_fields, dict):
                _add(key_fields.get("iface"))
            data = run.get("data")
            if isinstance(data, dict):
                _add(data.get("iface"))
        if candidates:
            return candidates

    # 来源 3：运行时变量
    source_variables = dict(variables or {})
    if not source_variables and diag_dict is not None:
        raw_variables = diag_dict.get("variables", {})
        if isinstance(raw_variables, dict):
            source_variables = raw_variables
    if source_variables:
        _add(source_variables.get("iface"))

    return candidates


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
    """Infer target node from step text plus primary root-cause entities.

    Purpose:
    - resolve a unique node candidate for tooling commands that require node scope.
    Input/Output:
    - input: plan step dict and validated diagnosis result;
    - output: tuple(node_name_or_none, ambiguous_flag).
    Compatibility rationale:
    - reads entities from `root_cause[0]` because the old top-level entity fields are removed.
    Why:
    - remediation remains first-root-cause-driven in the current migration stage.
    """
    inventory_names, host_to_name = _load_inventory_node_mapping()
    node_candidates: set[str] = set()
    ip_candidates: set[str] = set()

    primary = _diagnosis_primary_root_cause(diagnosis)
    for raw in (primary.get("entities") or []):
        _collect_node_candidates(str(raw or ""), inventory_names, node_candidates, ip_candidates)

    text = _step_text(step)
    for ip in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text):
        ip_candidates.add(ip)
    for name in inventory_names:
        if name and name in text:
            node_candidates.add(name)

    resolved_nodes: set[str] = set()
    for candidate in {*(node_candidates or set()), *(ip_candidates or set())}:
        normalized_node, _ = normalize_node_identifier(
            candidate,
            inventory_names=inventory_names,
            host_to_name=host_to_name,
        )
        if normalized_node:
            resolved_nodes.add(normalized_node)

    if len(resolved_nodes) == 1:
        return next(iter(resolved_nodes)), False

    if len(resolved_nodes) > 1:
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
    return load_inventory_node_mapping()


def _inventory_candidates() -> list[Path]:
    # Backward-compatible shim for older callers.
    return []
