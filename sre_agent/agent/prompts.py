from __future__ import annotations

import json
from typing import Iterable

from sre_agent.tools import SafetyLevel, ToolDefinition, ToolRegistry

DEFAULT_SELECTION_MODE = "react-readonly-diagnosis"

BASE_SYSTEM_PROMPT = """You are an SRE diagnosis agent.

Rules:
- Use only the bound read-only tools for evidence gathering.
- Never execute fixes or pretend a write action was executed.
- If evidence is insufficient, call a relevant read-only tool.
- Do not keep querying equivalent metrics after repeated empty or zero-valued results; treat that as evidence.
- Prefer at most 2-3 rounds of evidence gathering before concluding.
- After enough evidence is collected, return JSON only.

Final JSON shape:
{
  "thought": "brief reasoning summary",
  "diagnosis": {
    "root_cause": "string",
    "root_cause_layer": "hardware|network|os|platform|service",
    "root_cause_entities": ["string"],
    "confidence": 0.0,
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
"""


def build_system_prompt(
    registry: ToolRegistry,
    *,
    allowed_tool_names: Iterable[str] | None = None,
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
    return "\n\n".join([BASE_SYSTEM_PROMPT, read_only_block, write_block])


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
