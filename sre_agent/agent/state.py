from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class ToolRunRecord(TypedDict):
    step: int
    source: str
    tool: str
    params: dict[str, Any]
    success: bool
    data: Any
    error: str
    skill_id: NotRequired[str | None]
    prompt_summary: NotRequired[str]
    artifact_ref: NotRequired[str | None]
    data_kind: NotRequired[str | None]
    item_count: NotRequired[int | None]
    key_fields: NotRequired[dict[str, Any] | None]


class SkillRunRecord(TypedDict):
    step: int
    skill_id: str
    status: str
    summary: str
    selection_reason: NotRequired[str | None]
    prompt_summary: NotRequired[str]
    artifact_ref: NotRequired[str | None]
    tool_runs: list[ToolRunRecord]


class LoopGuardState(TypedDict, total=False):
    recent_fingerprint: str | None
    repeat_count: int
    threshold: int
    triggered: bool
    trigger_step: int | None


class SREAgentState(TypedDict):
    query: str
    variables: dict[str, Any]
    session_id: str
    messages: list[Any]
    llm_interactions: list[dict[str, Any]]
    trace_items: list[dict[str, Any]]
    pending_tool_calls: list[dict[str, Any]]
    tool_runs: list[ToolRunRecord]
    skill_runs: list[SkillRunRecord]
    step_count: int
    max_steps: int
    step_timeout_sec: float
    total_timeout_sec: float
    diagnosis_result: dict[str, Any] | None
    remediation_plan: dict[str, Any] | None
    status: str | None
    summary: str | None
    error: str | None
    checkpoint_dir: str | None
    allowed_tool_names: NotRequired[list[str] | None]
    alert_snapshot: NotRequired[dict[str, Any] | None]
    topology_context: NotRequired[dict[str, Any] | None]
    extra_alerts: NotRequired[list[dict[str, Any]] | None]
    evidence_signals: NotRequired[dict[str, Any]]
    loop_guard: NotRequired[LoopGuardState]
    force_final_turn: NotRequired[bool]
    _ttft_external_probe_injected: NotRequired[bool]
