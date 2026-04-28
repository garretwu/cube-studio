from __future__ import annotations

import json
from typing import Any, Iterable

from sre_agent.tools import SafetyLevel, ToolDefinition, ToolRegistry

DEFAULT_SELECTION_MODE = "react-readonly-diagnosis"

BASE_SYSTEM_PROMPT = """You are an SRE diagnosis agent.

Rules:
- Use only the bound read-only tools for evidence gathering.
- Never execute fixes or pretend a write action was executed.
- If reusable skills may help, use the fixed skill tools:
  1. `skills.list_skills`
  2. `skills.load_skill`
  3. `skills.read_skill_ref`
  4. `skills.run_skill`
- If there is a highly relevant skill that directly matches the current alert or failure pattern, prefer using the skill first instead of decomposing the investigation into many low-level tools.
- Prefer using skill tools when they can accelerate diagnosis, but keep the overall loop tool-driven.
- If a skill is matched and loaded successfully, you MUST execute diagnosis by following the SKILL.md workflow strictly:
  1. prioritize the ordered steps and command intent in SKILL.md,
  2. prefer executing SKILL.md commands via available tools (e.g. `ssh.run_command`) when equivalent native tools are unavailable,
  3. do not skip SKILL.md key evidence-collection steps before final diagnosis.
- Skill execution is a two-step process:
  1. call `skills.load_skill` and inspect the returned `scripts` list,
  2. then call `skills.run_skill` with both `skill_id` and one concrete `script` from that list.
- Never call `skills.run_skill` without a `script`.
- If a loaded skill has no scripts, do not call `skills.run_skill`; continue with normal read-only tools instead.
- Do not keep querying equivalent metrics after repeated empty or zero-valued results; treat that as evidence.
- Prefer at most 4-5 rounds of evidence gathering before concluding.
- Always include the leading root-cause hypothesis in the final diagnosis.
  If alternative explanations exist (eliminated or still testing), list them as additional hypotheses.
- All natural-language values in `diagnosis` and `remediation_plan` must be written in Chinese, while preserving English technical terms, identifiers, metric names, service names, tool names, PromQL, and resource names when needed.
- If you include a remediation plan, every step must use a real tool name from the write-tool schema reference. Never invent write tools or repurpose an unrelated tool just because the natural-language action sounds similar.
- Every remediation step `params` object must explicitly contain all required fields from that tool's `params_schema`. Do not leave required values only in `description` or `command`.
- If the intended action does not match any safe write tool in the schema reference, set `remediation_plan` to null instead of forcing an approximate tool call.
- After enough evidence is collected, return JSON only.

Final JSON shape:
{
  "thought": "中文推理摘要，可保留必要英文术语",
  "diagnosis": {
    "root_cause": "中文根因描述，可保留英文专业词汇",
    "root_cause_layer": "hardware|network|os|platform|service",
    "root_cause_entities": ["string"],
    "confidence": 0.0,
    "next_action": "中文下一步动作，说明用户应审批、补充验证或暂缓处置",
    "hypotheses": [
      {
        "description": "中文假设描述，可保留英文专业词汇",
        "status": "confirmed|testing|eliminated",
        "evidence_for": ["string"],
        "evidence_against": ["string"],
        "confidence": 0.0
      }
    ],
    "impact_summary": "中文影响摘要，可保留英文专业词汇",
    "affected_services": ["string"],
    "triage_priority": "P0|P1|P2|P3",
    "diagnosis_certainty": "confirmed|probable|ambiguous"
  },
  "remediation_plan": {
    "plan_id": "proposal-<short-id>",
    "root_cause": "中文根因描述，可保留英文专业词汇",
    "description": "中文修复方案描述，说明这是 proposal-only 且尚未执行",
    "steps": [
      {
        "step_id": 1,
        "description": "中文步骤描述，可保留 tool 名称和英文术语",
        "tool": "write tool name from the schema reference",
        "params": {},
        "verification": {
          "method": "wait",
          "wait_seconds": 30
        },
        "timeout": 60
      }
    ],
    "estimated_impact": "中文预估影响说明，可保留英文专业词汇",
    "confidence": 0.0,
    "priority": "P0|P1|P2",
    "safety_level": "low|medium|high|critical",
    "canary": {
      "enabled": true,
      "target_percentage": 0.1,
      "monitor_duration": 120,
      "success_criteria": [{"metric": "...", "operator": "<", "value": 0}],
      "max_batches": 3
    }
  }
}

If you include a remediation plan, it must exactly match the schema in the write-tool reference.
If the evidence is strong enough to support a diagnosis, prefer returning a proposal-only remediation plan.
Use a single conservative step if needed; verification may use method=wait.
Only set "remediation_plan" to null when the evidence is genuinely insufficient to suggest a safe proposal.
The remediation plan is proposal-only and must not assume any write action has run.
For `k8s.delete_pod`, valid params use `namespace` plus either `label_selector` or `pod_name`.
Never invent `pod_selector` for `k8s.delete_pod`.
For `kill_process`, `params` must include at least one target field: `pid`, `pid_or_name`, `process_name`, or `entity_id` formatted as `proc:<target>`.
For `kill_process`, if `signal` is provided, use `TERM|KILL|INT|HUP` (do not include `SIG` prefix).
Do not output `kill_process` with only `node` and natural-language target text in `description`.
Do not encode required remediation parameters only in free-form text such as "在节点 10.11.0.12 上执行"; they must appear in `params`.
If multiple entities are affected (e.g., multiple nodes or pods), include a canary config to roll out the fix progressively. Otherwise omit canary.
"""


def build_system_prompt(
    registry: ToolRegistry,
    *,
    allowed_tool_names: Iterable[str] | None = None,
    available_skills: Iterable[dict[str, Any]] | None = None,
    preferred_skill: dict[str, Any] | None = None,
    active_skill_id: str | None = None,
    active_skill_content: str | None = None,
) -> str:
    read_only_block = render_tool_reference_block(
        registry,
        tool_names=allowed_tool_names,
        safety_levels=[SafetyLevel.READ_ONLY],
        title="Read-only tool reference",
    )
    write_block = render_tool_reference_block(
        registry,
        safety_levels=[SafetyLevel.LOW, SafetyLevel.MEDIUM, SafetyLevel.HIGH, SafetyLevel.CRITICAL],
        title="Write-tool schema reference (not callable in this phase)",
    )
    active_skill_block = render_active_skill_guidance_block(
        skill_id=active_skill_id,
        skill_content=active_skill_content,
    )
    blocks = [BASE_SYSTEM_PROMPT]
    if active_skill_block:
        blocks.append(active_skill_block)
    blocks.extend([read_only_block, write_block])
    return "\n\n".join(blocks)


def render_active_skill_guidance_block(
    *,
    skill_id: str | None,
    skill_content: str | None,
) -> str:
    content = str(skill_content or "").strip()
    if not content:
        return ""
    normalized_skill_id = str(skill_id or "").strip() or "unknown-skill"
    return (
        "Active skill guidance:\n"
        f"- active_skill_id: {normalized_skill_id}\n"
        "SKILL.md content (verbatim):\n"
        f"{content}"
    )


def render_tool_reference_block(
    registry: ToolRegistry,
    *,
    title: str,
    safety_levels: Iterable[SafetyLevel | str] | None = None,
    tool_names: Iterable[str] | None = None,
) -> str:
    tools = registry.list_tools(safety_levels=safety_levels, tool_names=tool_names)
    lines = [title + ":"]
    if not tools:
        lines.append("- none")
        return "\n".join(lines)
    for tool in tools:
        lines.extend(_render_tool_definition(tool))
    return "\n".join(lines)


def _render_tool_definition(tool: ToolDefinition) -> list[str]:
    schema = json.dumps(tool.params_schema, ensure_ascii=True, sort_keys=True)
    return [
        f"- {tool.name}",
        f"  description: {tool.description}",
        f"  safety_level: {tool.safety_level.value}",
        f"  params_schema: {schema}",
    ]


def build_alert_diagnosis_prompt(
    *,
    alert_payload: dict[str, Any],
    available_tool_names: Iterable[str],
    diagnosis_goal: str,
    investigation_steps: Iterable[str] = (),
    context_hints: dict[str, Any] | None = None,
    remediation_guidance: str | None = None,
    history_count: int | None = None,
    extra_context: dict[str, Any] | None = None,
) -> str:
    lines = [
        "Diagnose the operational issue described by the alert below using a read-only ReAct workflow.",
        diagnosis_goal.strip(),
        f"Available read-only tools for this run: {json.dumps(list(available_tool_names), ensure_ascii=False)}.",
    ]

    hints = {str(key): value for key, value in (context_hints or {}).items() if value not in (None, "", [], {})}
    if hints:
        lines.append("Context hints:")
        for key, value in hints.items():
            lines.append(f"- {key}: {value}")

    if history_count is not None:
        lines.append(f"Historical alert samples found in lookback window: {history_count}")

    steps = [str(step).strip() for step in investigation_steps if str(step).strip()]
    if steps:
        lines.append("Suggested investigation order:")
        for idx, step in enumerate(steps, start=1):
            lines.append(f"{idx}. {step}")

    if remediation_guidance:
        lines.append(f"Remediation guidance: {remediation_guidance.strip()}")

    extra = {str(key): value for key, value in (extra_context or {}).items() if value not in (None, "", [], {})}
    if extra:
        lines.append(f"Additional run context: {json.dumps(extra, ensure_ascii=False)}")

    lines.append("Live alert payload:")
    lines.append(json.dumps(alert_payload, ensure_ascii=False, indent=2))
    return "\n".join(lines)
