from __future__ import annotations

import json
from typing import Any, Iterable

from sre_agent.tools import SafetyLevel, ToolDefinition, ToolRegistry

DEFAULT_SELECTION_MODE = "react-readonly-diagnosis"

BASE_SYSTEM_PROMPT = """You are an SRE diagnosis agent.

Rules:
- Use only the bound read-only tools for evidence gathering.
- Never execute fixes or pretend a write action was executed.
- If a reusable skill is a strong match for the current alert/context, you may select it instead of making the next direct tool call.
- If evidence is insufficient, call a relevant read-only tool.
- Do not keep querying equivalent metrics after repeated empty or zero-valued results; treat that as evidence.
- Prefer at most 2-3 rounds of evidence gathering before concluding.
- Always include at least 3 hypotheses in the final diagnosis:
  1. the leading root-cause hypothesis,
  2. one alternative that was eliminated or weakened,
  3. one alternative that remains testing or lower-confidence.
- **GPU evidence is MANDATORY for GPU-related alerts**: When diagnosing alerts involving GPU nodes or GPU metrics, you MUST call ALL available GPU read-only tools (gpu.get_metrics AND gpu.get_processes) to collect comprehensive evidence. Use the node IP address or node name as the 'node' parameter.
- After enough evidence is collected, return JSON only.

Skill selection JSON shape:
{
  "thought": "brief reasoning summary",
  "skill_call": {
    "skill_id": "skill id from the available skills reference",
    "reason": "why this skill is the best next move"
  }
}

Return a skill_call only when the skill is a strong, specific match and is likely to accelerate diagnosis.
If the situation is open-ended or the fit is weak, prefer direct tool calls instead.

Final JSON shape:
{
  "thought": "brief reasoning summary",
  "diagnosis": {
    "root_cause": "string",
    "root_cause_layer": "hardware|network|os|platform|service",
    "root_cause_entities": ["string"],
    "confidence": 0.0,
    "hypotheses": [
      {
        "description": "primary hypothesis",
        "status": "confirmed|testing|eliminated",
        "evidence_for": ["string"],
        "evidence_against": ["string"],
        "confidence": 0.0
      },
      {
        "description": "alternative hypothesis 1",
        "status": "confirmed|testing|eliminated",
        "evidence_for": ["string"],
        "evidence_against": ["string"],
        "confidence": 0.0
      },
      {
        "description": "alternative hypothesis 2",
        "status": "confirmed|testing|eliminated",
        "evidence_for": ["string"],
        "evidence_against": ["string"],
        "confidence": 0.0
      }
    ],
    "impact_summary": "string",
    "affected_services": ["string"],
    "triage_priority": "P0|P1|P2|P3",
    "diagnosis_certainty": "confirmed|probable|ambiguous"
  },
  "remediation_plan": {
    "plan_id": "proposal-<short-id>",
    "root_cause": "string",
    "description": "proposal-only remediation plan; not executed",
    "steps": [
      {
        "step_id": 1,
        "description": "single conservative action proposal",
        "tool": "write tool name from the schema reference",
        "params": {},
        "verification": {
          "method": "wait",
          "wait_seconds": 30
        },
        "timeout": 60
      }
    ],
    "estimated_impact": "string",
    "confidence": 0.0,
    "priority": "P0|P1|P2",
    "safety_level": "low|medium|high|critical"
  }
}

If you include a remediation plan, it must exactly match the schema in the write-tool reference.
If the evidence is strong enough to support a diagnosis, prefer returning a proposal-only remediation plan.
Use a single conservative step if needed; verification may use method=wait.
Only set "remediation_plan" to null when the evidence is genuinely insufficient to suggest a safe proposal.
The remediation plan is proposal-only and must not assume any write action has run.
For `k8s.delete_pod`, valid params use `namespace` plus either `label_selector` or `pod_name`.
Never invent `pod_selector` for `k8s.delete_pod`.
"""


def build_system_prompt(
    registry: ToolRegistry,
    *,
    allowed_tool_names: Iterable[str] | None = None,
    available_skills: Iterable[dict[str, Any]] | None = None,
    preferred_skill: dict[str, Any] | None = None,
) -> str:
    preferred_skill_block = render_preferred_skill_block(preferred_skill)
    read_only_block = render_tool_reference_block(
        registry,
        tool_names=allowed_tool_names,
        safety_levels=[SafetyLevel.READ_ONLY],
        title="Read-only tool reference",
    )
    skill_block = render_skill_reference_block(
        available_skills or [],
        title="Reusable skill reference",
    )
    write_block = render_tool_reference_block(
        registry,
        safety_levels=[SafetyLevel.LOW, SafetyLevel.MEDIUM, SafetyLevel.HIGH, SafetyLevel.CRITICAL],
        title="Write-tool schema reference (not callable in this phase)",
    )
    blocks = [BASE_SYSTEM_PROMPT]
    if preferred_skill_block:
        blocks.append(preferred_skill_block)
    blocks.extend([read_only_block, skill_block, write_block])
    return "\n\n".join(blocks)


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


def render_skill_reference_block(
    skills: Iterable[dict[str, Any]],
    *,
    title: str,
) -> str:
    lines = [title + ":"]
    rendered = False
    for skill in skills:
        skill_id = str(skill.get("id", "")).strip()
        if not skill_id:
            continue
        rendered = True
        tags = skill.get("tags", [])
        lines.append(f"- {skill_id}")
        lines.append(f"  name: {skill.get('name', '')}")
        lines.append(f"  summary: {skill.get('summary', '')}")
        lines.append(f"  tags: {json.dumps(tags, ensure_ascii=False)}")
        match_score = skill.get("match_score")
        if match_score is not None:
            lines.append(f"  match_score: {match_score}")
    if not rendered:
        lines.append("- none")
    return "\n".join(lines)


def render_preferred_skill_block(preferred_skill: dict[str, Any] | None) -> str:
    if not preferred_skill:
        return ""
    skill_id = str(preferred_skill.get("id", "")).strip()
    if not skill_id:
        return ""
    score = preferred_skill.get("match_score")
    summary = str(preferred_skill.get("summary", "")).strip()
    lines = [
        "Current turn guidance:",
        (
            f"- The top reusable skill match for this alert is `{skill_id}`"
            + (f" with match_score={score}." if score is not None else ".")
        ),
    ]
    if summary:
        lines.append(f"- Skill summary: {summary}")
    lines.append(
        "- Because this is the first investigation round and the skill match is strong, "
        "prefer returning a `skill_call` for this skill before making direct tool calls."
    )
    lines.append(
        "- Only skip the skill if you can clearly infer that it is a poor fit for the current alert/context."
    )
    return "\n".join(lines)


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


def build_skill_selection_prompt(
    *,
    query: str,
    available_skills: Iterable[dict[str, Any]],
) -> str:
    lines = [
        "Decide whether this alert/context should first use a reusable diagnosis skill.",
        "Return JSON only.",
        "If one skill is a strong fit, return:",
        '{"thought":"brief reasoning","skill_call":{"skill_id":"...","reason":"..."}}',
        "If no skill is a strong fit, return:",
        '{"thought":"brief reasoning","skill_call":null}',
        "Do not call tools in this step.",
        f"Original query: {query}",
    ]
    rendered = [skill for skill in available_skills if str(skill.get("id", "")).strip()]
    if rendered:
        lines.append("Candidate skills:")
        for skill in rendered:
            lines.append(
                "- "
                + json.dumps(
                    {
                        "id": skill.get("id"),
                        "name": skill.get("name"),
                        "summary": skill.get("summary"),
                        "tags": skill.get("tags"),
                        "match_score": skill.get("match_score"),
                    },
                    ensure_ascii=False,
                )
            )
    return "\n".join(lines)
