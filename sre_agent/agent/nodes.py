from __future__ import annotations

import ast
import asyncio
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field

from sre_agent.agent.checkpoint import persist_state_snapshot
from sre_agent.agent.prompts import build_system_prompt
from sre_agent.agent.state import SREAgentState
from sre_agent.models.diagnosis import DiagnosisResult, Observation, ThinkingStep, ThinkingTrace
from sre_agent.models.remediation import RemediationPlan
from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry
from sre_agent.tools import ToolExecutionContext, ToolRegistry, build_default_registry


class FinalDiagnosisEnvelope(BaseModel):
    thought: str = Field(min_length=1)
    diagnosis: dict[str, Any]
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
        "trace_items": [],
        "pending_tool_calls": [],
        "tool_runs": [],
        "step_count": 0,
        "max_steps": max_steps,
        "step_timeout_sec": step_timeout_sec,
        "total_timeout_sec": total_timeout_sec,
        "selected_skill_id": None,
        "skill_catalog": [],
        "diagnosis_result": None,
        "remediation_plan": None,
        "status": None,
        "summary": None,
        "error": None,
        "checkpoint_dir": checkpoint_dir,
        "allowed_tool_names": allowed_tool_names,
    }


async def reason_node(
    state: SREAgentState,
    *,
    llm: Any,
    registry: ToolRegistry,
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

    system_prompt = build_system_prompt(
        registry,
        allowed_tool_names=state.get("allowed_tool_names"),
    )
    messages = _coerce_messages(state.get("messages", []))
    if not messages:
        messages = [HumanMessage(content=state["query"])]
    else:
        messages = list(messages)

    model_messages = [SystemMessage(content=system_prompt), *messages]
    final_turn = bool(state.get("tool_runs")) and state.get("step_count", 0) >= max(state.get("max_steps", 6) - 1, 1)
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
        response = await asyncio.wait_for(llm.ainvoke(final_messages), timeout=state["step_timeout_sec"])
    else:
        call_model = llm.bind_tools(
            registry.get_langchain_tools(
                tool_names=state.get("allowed_tool_names"),
            ),
            tool_choice="required" if not state.get("tool_runs") else "auto",
        )
        response = await asyncio.wait_for(call_model.ainvoke(model_messages), timeout=state["step_timeout_sec"])
    if not isinstance(response, AIMessage):
        raise RuntimeError(f"expected AIMessage from LLM, got {type(response).__name__}")

    updated_messages = [*messages, response]
    updated_trace = list(state.get("trace_items", []))
    pending_tool_calls = list(response.tool_calls or [])
    step_index = state.get("step_count", 0) + 1

    if pending_tool_calls:
        updated_trace.append(
            {
                "type": "thought",
                "step": step_index,
                "content": _extract_text(response.content) or "Requesting read-only evidence via tools.",
                "action": "tool_call",
                "tool_name": pending_tool_calls[0]["name"],
                "tool_params": pending_tool_calls[0].get("args", {}),
                "confidence": None,
            }
        )
        updated = {
            **state,
            "messages": updated_messages,
            "trace_items": updated_trace,
            "pending_tool_calls": pending_tool_calls,
            "step_count": step_index,
            "status": "running",
            "summary": None,
            "error": None,
        }
        persist_state_snapshot(updated.get("checkpoint_dir"), updated["session_id"], "reason", updated)
        return updated

    parsed = _parse_final_output(_extract_text(response.content))
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
    if state.get("pending_tool_calls"):
        return "act"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "finalize"


def route_after_decide(state: SREAgentState) -> str:
    if state.get("status") in {"failed", "timeout"}:
        return "finalize"
    if state.get("diagnosis_result") is not None:
        return "finalize"
    return "reason"


def load_and_select_skill_node(
    state: SREAgentState,
    *,
    registry: SkillRegistry,
    policy: SkillPolicy,
) -> SREAgentState:
    query = str(state.get("query", "") or "").strip()
    skills = registry.discover()
    catalog = [skill.id for skill in skills]
    if not skills:
        return {
            **state,
            "skill_catalog": catalog,
            "selected_skill_id": None,
            "status": "failed",
            "summary": "no skills discovered",
            "error": "no skills discovered",
        }

    ranked = policy.rank(query, skills, top_k=1)
    if not ranked or ranked[0].match_score <= 0:
        return {
            **state,
            "skill_catalog": catalog,
            "selected_skill_id": None,
            "status": "failed",
            "summary": "no skill matched the query",
            "error": "no skill matched the query",
        }

    selected = ranked[0]
    return {
        **state,
        "skill_catalog": catalog,
        "selected_skill_id": selected.id,
        "status": None,
        "summary": None,
        "error": None,
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
    return {
        **state,
        "tool_runs": [_serialize_tool_run(item) for item in result.tool_runs],
        "status": result.status,
        "summary": result.summary,
        "error": None if result.status == "success" else result.summary,
    }


def should_execute_selected_skill(state: SREAgentState) -> str:
    if state.get("selected_skill_id") and state.get("status") != "failed":
        return "execute_selected_skill"
    return "finalize"


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


def _parse_final_output(content: str) -> FinalDiagnosisEnvelope:
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
    return FinalDiagnosisEnvelope.model_validate(payload)


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
    return normalized


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
