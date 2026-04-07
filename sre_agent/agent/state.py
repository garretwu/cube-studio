from __future__ import annotations

from typing import Any, NotRequired, TypedDict

class SREAgentState(TypedDict):
    query: str
    variables: dict[str, Any]
    session_id: str
    messages: list[Any]
    llm_interactions: list[dict[str, Any]]
    trace_items: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_runs: list[dict[str, Any]]
    step_count: int
    max_steps: int
    step_timeout_sec: float
    total_timeout_sec: float
    selected_skill_id: str | None
    skill_catalog: list[str]
    diagnosis_result: dict[str, Any] | None
    remediation_plan: dict[str, Any] | None
    status: str | None
    summary: str | None
    error: str | None
    checkpoint_dir: str | None
    allowed_tool_names: NotRequired[list[str] | None]
    alert_snapshot: NotRequired[dict[str, Any] | None]
    topology_context: NotRequired[dict[str, Any] | None]
