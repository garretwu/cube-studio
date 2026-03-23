from __future__ import annotations

import asyncio
import os
from typing import Any
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
    should_execute_selected_skill,
)
from sre_agent.agent.state import SREAgentState
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
    model = os.getenv("SRE_LLM_MODEL", "").strip() or "MiniMax-M2.5"
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "temperature": 0,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def create_sre_graph(
    *,
    llm: Any | None = None,
    guardrails: Any | None = None,
    skill_registry: SkillRegistry | None = None,
    skill_policy: SkillPolicy | None = None,
    skill_executor: SkillExecutor | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_context: ToolExecutionContext | None = None,
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
            return await reason_node(state, llm=wrapped_llm, registry=tools)
        except asyncio.TimeoutError:
            return {
                **state,
                "status": "timeout",
                "summary": "reason step timed out",
                "error": "reason step timed out",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                **state,
                "status": "failed",
                "summary": f"reason step failed: {exc}",
                "error": f"reason step failed: {exc}",
            }

    async def _act(state: SREAgentState) -> SREAgentState:
        try:
            return await act_node(state, registry=tools, context=tool_context)
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

    graph = StateGraph(SREAgentState)
    graph.add_node(
        "load_and_select_skill",
        lambda state: load_and_select_skill_node(
            state,
            registry=registry,
            policy=policy,
        ),
    )
    graph.add_node("reason", _reason)
    graph.add_node("act", _act)
    graph.add_node("observe", observe_node)
    graph.add_node("decide", decide_node)
    graph.add_node("execute_selected_skill", _execute_selected_skill)
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

    graph.add_edge("load_and_select_skill", "execute_selected_skill")
    graph.add_conditional_edges(
        "load_and_select_skill",
        should_execute_selected_skill,
        {
            "execute_selected_skill": "execute_selected_skill",
            "finalize": "finalize",
        },
    )
    graph.add_edge("execute_selected_skill", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=create_checkpointer())


async def run_diagnosis(
    *,
    query: str,
    context: ToolExecutionContext | None,
    variables: dict[str, Any] | None = None,
    llm: Any | None = None,
    guardrails: Any | None = None,
    tool_registry: ToolRegistry | None = None,
    session_id: str | None = None,
    step_timeout_sec: float = 60.0,
    total_timeout_sec: float = 600.0,
    max_steps: int = 6,
    checkpoint_dir: str | None = "./data/checkpoints/sre_agent",
    allowed_tool_names: list[str] | None = None,
) -> SREAgentState:
    active_session_id = session_id or uuid4().hex
    graph = create_sre_graph(
        llm=llm,
        guardrails=guardrails,
        tool_registry=tool_registry,
        tool_context=context,
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
