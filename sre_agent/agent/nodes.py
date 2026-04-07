from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, messages_to_dict
from pydantic import BaseModel, Field

from sre_agent.agent.checkpoint import persist_state_snapshot
from sre_agent.agent.prompts import build_skill_selection_prompt, build_system_prompt
from sre_agent.agent.state import SREAgentState
from sre_agent.models.diagnosis import DiagnosisResult, Observation, ThinkingStep, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry
from sre_agent.tools import ToolExecutionContext, ToolRegistry, build_default_registry

# LLM 交互日志记录器
_llm_logger = logging.getLogger("sre_agent.llm")
_llm_logger.setLevel(logging.DEBUG)
_llm_logger.addHandler(logging.NullHandler())  # 默认空 handler，避免警告

LLM_LOG_DIR = Path("./data/llm_logs")


class FinalDiagnosisEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any]
    remediation_plan: dict[str, Any] | None = None


class SkillCallEnvelope(BaseModel):
    skill_id: str = Field(min_length=1)
    reason: str | None = None


class ReasoningEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    skill_call: SkillCallEnvelope | None = None
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
        "step_count": 0,
        "max_steps": max_steps,
        "step_timeout_sec": step_timeout_sec,
        "total_timeout_sec": total_timeout_sec,
        "selected_skill_id": None,
        "skill_selection_attempted": False,
        "skill_catalog": [],
        "skill_selection_reason": None,
        "diagnosis_result": None,
        "remediation_plan": None,
        "status": None,
        "summary": None,
        "error": None,
        "checkpoint_dir": checkpoint_dir,
        "allowed_tool_names": allowed_tool_names,
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
    response: AIMessage,
    mode: str,
    tool_choice: str,
    tool_calls: list[dict[str, Any]],
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
            },
            "response": {
                "content": _extract_text(response.content),
                "tool_calls": tool_calls,
                "response_metadata": getattr(response, "response_metadata", {}),
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
    skill_registry: SkillRegistry | None = None,
    skill_policy: SkillPolicy | None = None,
) -> SREAgentState:
    if state.get("step_count", 0) >= state.get("max_steps", 6):
        updated = {
            **state,
            "status": "timeout",
            "summary": "max diagnosis steps reached",
            "error": "max diagnosis steps reached",
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason-timeout", updated)
        return updated

    discovered_skills: list[Any] = []
    ranked_skills: list[Any] = []
    if skill_registry is not None:
        try:
            discovered_skills = skill_registry.discover()
            if skill_policy is not None:
                ranked_skills = skill_policy.rank(str(state.get("query", "") or "").strip(), discovered_skills, top_k=5)
        except Exception:  # noqa: BLE001
            discovered_skills = []
            ranked_skills = []
    skill_reference = [
        {
            "id": skill.id,
            "name": skill.name,
            "summary": skill.summary,
            "tags": list(skill.tags),
            "match_score": skill.match_score,
        }
        for skill in (ranked_skills or discovered_skills)
    ]
    top_skill_score = 0.0
    if ranked_skills:
        top_skill_score = float(getattr(ranked_skills[0], "match_score", 0.0) or 0.0)
    query_text = str(state.get("query", "") or "")
    alert_shaped_query = _looks_like_alert_driven_query(query_text)
    strong_skill_match = bool(not state.get("tool_runs") and alert_shaped_query and top_skill_score >= 0.02)
    preferred_skill = None
    if strong_skill_match and ranked_skills:
        preferred_skill = {
            "id": ranked_skills[0].id,
            "name": ranked_skills[0].name,
            "summary": ranked_skills[0].summary,
            "tags": list(ranked_skills[0].tags),
            "match_score": ranked_skills[0].match_score,
        }
    system_prompt = build_system_prompt(
        registry,
        allowed_tool_names=state.get("allowed_tool_names"),
        available_skills=skill_reference,
        preferred_skill=preferred_skill,
    )
    messages = _coerce_messages(state.get("messages", []))
    if not messages:
        messages = [HumanMessage(content=state["query"])]
    else:
        messages = list(messages)

    model_messages = [SystemMessage(content=system_prompt), *messages]
    invoked_messages = model_messages
    bound_tool_names: list[str] = []
    tool_choice = "auto"
    final_turn = bool(state.get("tool_runs")) and state.get("step_count", 0) >= max(state.get("max_steps", 6) - 1, 1)
    interaction_mode = "final_json" if final_turn else "tool_bound"
    if final_turn and hasattr(llm, "ainvoke"):
        final_messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"Original query: {state['query']}"),
            HumanMessage(content=f"Collected evidence:\n{_summarize_tool_runs(state.get('tool_runs', []))}"),
            HumanMessage(
                content=(
                    "Use the evidence already collected and return the final JSON now. "
                    "Do not call more tools. If remediation details are incomplete, set remediation_plan to null."
                )
            ),
        ]
        invoked_messages = final_messages
        tool_choice = "none"
        response = await asyncio.wait_for(llm.ainvoke(final_messages), timeout=state["step_timeout_sec"])
    else:
        bound_tools = registry.get_langchain_tools(
            tool_names=state.get("allowed_tool_names"),
        )
        bound_tool_names = [str(getattr(tool, "name", "")) for tool in bound_tools]
        tool_choice = "auto" if strong_skill_match else ("required" if not state.get("tool_runs") else "auto")
        try:
            call_model = llm.bind_tools(
                bound_tools,
                tool_choice=tool_choice,
            )
            response = await asyncio.wait_for(call_model.ainvoke(model_messages), timeout=state["step_timeout_sec"])
        except Exception as exc:  # noqa: BLE001
            if not hasattr(llm, "ainvoke") or not _is_tool_binding_incompatible_error(exc):
                raise
            # Provider compatibility fallback: continue diagnosis without tool-binding,
            # otherwise the whole session fails before any trace is generated.
            interaction_mode = "tool_binding_fallback"
            tool_choice = "none"
            fallback_messages = [
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
            ]
            invoked_messages = fallback_messages
            response = await asyncio.wait_for(llm.ainvoke(fallback_messages), timeout=state["step_timeout_sec"])
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
    )

    updated_interactions.append(
        {
            "step": step_index,
            "mode": interaction_mode,
            "tool_choice": tool_choice,
            "bound_tool_names": [name for name in bound_tool_names if name],
            "prompt_messages": messages_to_dict(invoked_messages),
            "response_message": messages_to_dict([response])[0],
            "raw_response_text": raw_response_text,
            "tool_calls": pending_tool_calls,
        }
    )

    inferred_skill_id = _infer_skill_selection_from_text(
        content=raw_response_text,
        available_skills=ranked_skills or discovered_skills,
    )
    if inferred_skill_id and not state.get("tool_runs"):
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": raw_response_text or f"Selecting reusable skill {inferred_skill_id}.",
                "action": "tool_call",
                "tool_name": inferred_skill_id,
                "tool_params": {"kind": "skill", "reason": "inferred from free-form model response"},
                "confidence": None,
            }
        )
        updated = {
            **state,
            "messages": updated_messages,
            "llm_interactions": updated_interactions,
            "trace_items": updated_trace,
            "pending_tool_calls": [],
            "step_count": step_index,
            "skill_catalog": [skill.id for skill in discovered_skills],
            "selected_skill_id": inferred_skill_id,
            "skill_selection_reason": raw_response_text or "inferred from free-form model response",
            "status": "running",
            "summary": None,
            "error": None,
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
        return updated

    if pending_tool_calls:
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": raw_response_text or "Requesting read-only evidence via tools.",
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
            "skill_catalog": [skill.id for skill in discovered_skills],
            "selected_skill_id": None,
            "skill_selection_reason": None,
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
    if parsed.skill_call is not None:
        selected_skill_id = parsed.skill_call.skill_id.strip()
        known_skill_ids = {skill.id for skill in discovered_skills}
        if selected_skill_id not in known_skill_ids:
            updated = {
                **state,
                "messages": updated_messages,
                "llm_interactions": updated_interactions,
                "pending_tool_calls": [],
                "step_count": step_index,
                "skill_catalog": [skill.id for skill in discovered_skills],
                "selected_skill_id": None,
                "skill_selection_reason": None,
                "status": "failed",
                "summary": f"selected skill is not available: {selected_skill_id}",
                "error": f"selected skill is not available: {selected_skill_id}",
            }
            persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason-failed", updated)
            return updated
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": parsed.thought,
                "action": "tool_call",
                "tool_name": selected_skill_id,
                "tool_params": {"kind": "skill", "reason": (parsed.skill_call.reason or "").strip()},
                "confidence": None,
            }
        )
        updated = {
            **state,
            "messages": updated_messages,
            "llm_interactions": updated_interactions,
            "trace_items": updated_trace,
            "pending_tool_calls": [],
            "step_count": step_index,
            "skill_catalog": [skill.id for skill in discovered_skills],
            "selected_skill_id": selected_skill_id,
            "skill_selection_reason": (parsed.skill_call.reason or "").strip() or parsed.thought,
            "status": "running",
            "summary": None,
            "error": None,
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
        return updated
    if parsed.diagnosis is None:
        parsed = _build_fallback_reasoning_output(
            query=str(state.get("query", "")).strip(),
            content=raw_response_text,
        )
        if parsed.diagnosis is None:
            parsed = ReasoningEnvelope(
                thought="Converted non-JSON model output to a structured low-confidence diagnosis.",
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
        "skill_catalog": [skill.id for skill in discovered_skills],
        "selected_skill_id": None,
        "skill_selection_reason": None,
        "diagnosis_result": diagnosis.model_dump(mode="json"),
        "remediation_plan": None if remediation_plan is None else remediation_plan.model_dump(mode="json"),
        "status": "diagnosed",
        "summary": diagnosis.impact_summary,
        "error": None,
    }
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
    return updated


def _is_tool_binding_incompatible_error(exc: Exception) -> bool:
    message = str(exc).lower()
    signals = (
        "null value for 'choices'",
        "response with null value for 'choices'",
        "tool_calls",
        "tool binding",
        "function call",
    )
    return any(signal in message for signal in signals)


def _build_fallback_final_output(*, query: str, content: str) -> FinalDiagnosisEnvelope:
    summary = (content or "").strip()
    if not summary:
        summary = "LLM returned an empty response while generating diagnosis."
    if len(summary) > 300:
        summary = summary[:297] + "..."
    root_cause = summary.splitlines()[0].strip() if summary else "Insufficient evidence from LLM response"
    if len(root_cause) > 140:
        root_cause = root_cause[:137] + "..."
    return FinalDiagnosisEnvelope.model_validate(
        {
            "thought": "Converted non-JSON model output to a structured low-confidence diagnosis.",
            "diagnosis": {
                "root_cause": root_cause or "Insufficient evidence from LLM response",
                "root_cause_layer": "platform",
                "root_cause_entities": [],
                "confidence": 0.35,
                "impact_summary": summary,
                "affected_services": [],
                "triage_priority": "P2",
                "diagnosis_certainty": "ambiguous",
                "hypotheses": [
                    {
                        "description": root_cause or "Primary hypothesis from unstructured model output",
                        "status": "testing",
                        "evidence_for": [summary] if summary else [],
                        "evidence_against": [],
                        "confidence": 0.35,
                    },
                    {
                        "description": "Metric collection/tool evidence was unavailable or incompatible",
                        "status": "testing",
                        "evidence_for": ["tool binding fallback path activated"],
                        "evidence_against": [],
                        "confidence": 0.3,
                    },
                    {
                        "description": "Alert may be transient or context incomplete",
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
        result = await asyncio.wait_for(
            registry.execute(tool_name, tool_args, context),
            timeout=state["step_timeout_sec"],
        )
        serialized = {
            "step": len(tool_runs) + 1,
            "tool": tool_name,
            "params": tool_args,
            "success": result.success,
            "data": result.data,
            "error": result.error,
        }
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
                content=json.dumps(
                    {
                        "success": result.success,
                        "data": _safe_jsonable(result.data),
                        "error": result.error,
                    },
                    ensure_ascii=False,
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
    persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "finalize", updated)
    return updated


def route_after_reason(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout"}:
        return "finalize"
    if state.get("selected_skill_id"):
        return "execute_selected_skill"
    if state.get("pending_tool_calls"):
        return "act"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "finalize"


def route_after_skill_selection(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout"}:
        return "finalize"
    if state.get("selected_skill_id"):
        return "execute_selected_skill"
    return "reason"


def route_after_decide(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout"}:
        return "finalize"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "reason"


async def load_and_select_skill_node(
    state: SREAgentState,
    *,
    llm: Any,
    registry: SkillRegistry,
    policy: SkillPolicy,
    tool_registry: ToolRegistry,
) -> SREAgentState:
    if state.get("skill_selection_attempted"):
        return state
    query = str(state.get("query", "") or "").strip()
    skills = registry.discover()
    catalog = [skill.id for skill in skills]
    ranked = policy.rank(query, skills, top_k=5) if skills else []
    top_skill_score = float(getattr(ranked[0], "match_score", 0.0) or 0.0) if ranked else 0.0
    strong_skill_match = bool(
        not state.get("tool_runs")
        and _looks_like_alert_driven_query(query)
        and top_skill_score >= 0.02
    )
    if not skills or not strong_skill_match:
        return {
            **state,
            "skill_catalog": catalog,
            "selected_skill_id": None,
            "skill_selection_attempted": True,
        }

    skill_reference = [
        {
            "id": skill.id,
            "name": skill.name,
            "summary": skill.summary,
            "tags": list(skill.tags),
            "match_score": skill.match_score,
        }
        for skill in ranked
    ]
    preferred_skill = skill_reference[0] if skill_reference else None
    system_prompt = build_system_prompt(
        tool_registry,
        allowed_tool_names=state.get("allowed_tool_names"),
        available_skills=skill_reference,
        preferred_skill=preferred_skill,
    )
    selection_prompt = build_skill_selection_prompt(
        query=query,
        available_skills=skill_reference,
    )
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=selection_prompt)]
    response = await asyncio.wait_for(llm.ainvoke(messages), timeout=state["step_timeout_sec"])
    if not isinstance(response, AIMessage):
        raise RuntimeError(f"expected AIMessage from LLM, got {type(response).__name__}")
    raw_response_text = _extract_text(response.content)
    step_index = state.get("step_count", 0) + 1

    _log_llm_interaction(
        session_id=str(state.get("session_id", "unknown")),
        step=step_index,
        prompt_messages=messages,
        response=response,
        mode="skill_selection",
        tool_choice="none",
        tool_calls=[],
    )

    updated_interactions = list(state.get("llm_interactions", []))
    updated_interactions.append(
        {
            "step": step_index,
            "mode": "skill_selection",
            "tool_choice": "none",
            "bound_tool_names": [],
            "prompt_messages": messages_to_dict(messages),
            "response_message": messages_to_dict([response])[0],
            "raw_response_text": raw_response_text,
            "tool_calls": [],
        }
    )

    try:
        parsed = _parse_reasoning_output(raw_response_text)
    except Exception:  # noqa: BLE001
        parsed = ReasoningEnvelope(thought=raw_response_text or "No skill selected.", skill_call=None)

    selected_skill_id: str | None = None
    selection_reason: str | None = None
    if parsed.skill_call is not None:
        candidate = parsed.skill_call.skill_id.strip()
        if candidate in {skill.id for skill in skills}:
            selected_skill_id = candidate
            selection_reason = (parsed.skill_call.reason or "").strip() or parsed.thought
    if selected_skill_id is None:
        inferred_skill_id = _infer_skill_selection_from_text(raw_response_text, ranked or skills)
        if inferred_skill_id:
            selected_skill_id = inferred_skill_id
            selection_reason = raw_response_text or "inferred from free-form model response"

    updated_trace = list(state.get("trace_items", []))
    if selected_skill_id:
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": parsed.thought or raw_response_text or f"Selecting reusable skill {selected_skill_id}.",
                "action": "tool_call",
                "tool_name": selected_skill_id,
                "tool_params": {"kind": "skill", "reason": selection_reason},
                "confidence": None,
            }
        )

    return {
        **state,
        "llm_interactions": updated_interactions,
        "trace_items": updated_trace,
        "step_count": step_index,
        "skill_catalog": catalog,
        "selected_skill_id": selected_skill_id,
        "skill_selection_attempted": True,
        "skill_selection_reason": selection_reason,
        "status": "running" if selected_skill_id else state.get("status"),
    }


async def execute_selected_skill_node(
    state: SREAgentState,
    *,
    registry: SkillRegistry,
    executor: SkillExecutor,
    context: ToolExecutionContext | None,
    tool_registry: ToolRegistry | None = None,
) -> SREAgentState:
    selected_skill_id = state.get("selected_skill_id")
    if not selected_skill_id:
        return {
            **state,
            "status": "failed",
            "summary": state.get("summary") or "selected skill is missing",
            "error": state.get("error") or "selected skill is missing",
        }

    if context is None:
        return {
            **state,
            "status": "failed",
            "summary": "tool execution context is required",
            "error": "tool execution context is required",
        }

    skill = registry.get(selected_skill_id)
    resolved_registry = tool_registry or build_default_registry()
    result = await executor.execute(
        skill=skill,
        registry=resolved_registry,
        context=context,
        variables=state.get("variables", {}),
    )
    serialized_runs = [_serialize_tool_run(item) for item in result.tool_runs]
    existing_runs = list(state.get("tool_runs", []))
    messages = list(_coerce_messages(state.get("messages", [])))
    if serialized_runs:
        messages.append(
            HumanMessage(
                content=(
                    f"Skill execution result for {selected_skill_id}:\n"
                    f"{_summarize_tool_runs(serialized_runs)}"
                )
            )
        )
    trace_items = list(state.get("trace_items", []))
    trace_items.extend(
        [
            {
                "type": "observation",
                "tool": item["tool"],
                "params": item["params"],
                "result": {
                    "success": item["success"],
                    "data": _safe_jsonable(item.get("data")),
                    "error": item.get("error"),
                },
            }
            for item in serialized_runs
        ]
    )
    llm_interactions = list(state.get("llm_interactions", []))
    llm_interactions.append(
        {
            "step": state.get("step_count", 0),
            "mode": "skill_execution",
            "skill_id": selected_skill_id,
            "skill_reason": state.get("skill_selection_reason"),
            "tool_runs": serialized_runs,
        }
    )
    return {
        **state,
        "messages": messages,
        "llm_interactions": llm_interactions,
        "trace_items": trace_items,
        "tool_runs": [*existing_runs, *serialized_runs],
        "selected_skill_id": None,
        "skill_selection_reason": None,
        "status": "running" if result.status == "success" else "failed",
        "summary": state.get("summary") if result.status == "success" else result.summary,
        "error": None if result.status == "success" else result.summary,
    }


def _serialize_tool_run(value: Any) -> dict[str, Any]:
    return asdict(value)


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
        success = bool(item.get("success", False))
        params = _safe_jsonable(item.get("params", {}))
        data = _safe_jsonable(item.get("data"))
        error = str(item.get("error", "") or "").strip()
        lines.append(f"- tool={tool} success={success} params={params}")
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


def _infer_skill_selection_from_text(content: str, available_skills: list[Any]) -> str | None:
    text = (content or "").strip()
    if not text:
        return None
    lowered = text.lower()
    selection_verbs = (
        "select",
        "selected",
        "choose",
        "chosen",
        "use",
        "using",
        "run",
        "execute",
        "apply",
        "pick",
    )
    if not any(verb in lowered for verb in selection_verbs):
        return None
    if "skill" not in lowered:
        return None

    for skill in available_skills:
        skill_id = str(getattr(skill, "id", "") or "").strip()
        skill_name = str(getattr(skill, "name", "") or "").strip()
        candidates = [value.lower() for value in (skill_id, skill_name) if value]
        if any(candidate in lowered for candidate in candidates):
            return skill_id or None
    return None


def _looks_like_alert_driven_query(query: str) -> bool:
    lowered = (query or "").lower()
    signals = (
        "alert",
        "alertname",
        "severity",
        "namespace",
        "service",
        "firing",
        "labels",
        "annotations",
        "payload",
    )
    return any(signal in lowered for signal in signals)


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
    root_cause = str(payload.get("root_cause") or "Primary root-cause hypothesis").strip()
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
            "description": "Network or RDMA degradation contributing to latency",
            "status": "eliminated" if root_cause_layer != "network" else "testing",
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and root_cause_layer != "network" else [],
            "confidence": _clamp_confidence(min(confidence, 0.45), default=0.35),
        },
        {
            "description": "Normal workload increase or service-side saturation",
            "status": "testing" if root_cause_layer != "service" else primary_status,
            "evidence_for": [],
            "evidence_against": [impact_summary] if impact_summary and root_cause_layer != "service" else [],
            "confidence": _clamp_confidence(min(confidence, 0.55), default=0.4),
        },
        {
            "description": "Thermal throttling or other hardware-side instability",
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
                f"Proposed remediation step {index} for {diagnosis.root_cause}",
            )
            normalized_step.setdefault("params", {})
            _normalize_step_params_in_place(normalized_step)
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
        "Proposal-only remediation plan generated from diagnosis evidence. No write action has been executed.",
    )
    candidate.setdefault("estimated_impact", diagnosis.impact_summary)
    candidate.setdefault("confidence", _normalize_plan_confidence(diagnosis.confidence))
    candidate.setdefault("priority", _normalize_plan_priority(diagnosis.triage_priority))
    candidate.setdefault("safety_level", "high")

    try:
        return RemediationPlan.model_validate(candidate)
    except Exception:  # noqa: BLE001
        return None


def _normalize_plan_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_plan_priority(value: str) -> str:
    normalized = str(value).strip().upper()
    if normalized in {"P0", "P1", "P2"}:
        return normalized
    return "P2"


def _normalize_step_params_in_place(step: dict[str, Any]) -> None:
    tool_name = str(step.get("tool") or "").strip()
    params = step.get("params")
    if not isinstance(params, dict):
        return

    if tool_name == "k8s.delete_pod":
        pod_selector = params.pop("pod_selector", None)
        if pod_selector is not None and "label_selector" not in params:
            params["label_selector"] = pod_selector
