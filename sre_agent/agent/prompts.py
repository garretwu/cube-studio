from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Iterable

from sre_agent.tools import SafetyLevel, ToolDefinition, ToolRegistry

DEFAULT_SELECTION_MODE = "react-readonly-diagnosis"
_ALERT_CATALOG_PATH = Path(__file__).resolve().parents[1] / "conf" / "Entity.md"
_MAX_ALERT_CATALOG_ENTRIES = 16
_MAX_PROMQL_PREVIEW_CHARS = 220

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
- If evidence is insufficient, call a relevant read-only tool.
- Do not keep querying equivalent metrics after repeated empty or zero-valued results; treat that as evidence.
- Prefer at most 2-3 rounds of evidence gathering before concluding.
- Always consult the "Alert catalog reference" block in this system prompt first when selecting alert-related metrics.
- If the current alert name matches a catalog entry, prioritize that metric family and threshold semantics before broad generic probing.
- Always include the leading root-cause hypothesis in the final diagnosis. If alternative explanations exist (eliminated or still testing), list them as additional hypotheses.
- All natural-language values in `diagnosis` and `diagnosis.root_cause[i].recommended_fix` must be written in Chinese, while preserving English technical terms, identifiers, metric names, service names, tool names, PromQL, and resource names when needed.
- Remediation step `title`/`description` values must describe the action in Chinese; keep tool names and entity names unchanged, express node placement as `位于节点 <node>`, and do not use `on node <node>`.
- If you include `diagnosis.root_cause[i].recommended_fix`, every step must use a real tool name from the write-tool schema reference.
- Every remediation step `params` object must explicitly contain all required fields from that tool's `params_schema`.
- If the intended action does not match any safe write tool in the schema reference, set that root cause item's `recommended_fix` to null.
- After enough evidence is collected, return JSON only.

Final JSON shape:
{
  "thought": "Chinese reasoning summary with necessary English terms",
  "diagnosis": {
    "root_cause": [
      {
        "id": "rc-1",
        "title": "Chinese root-cause title with optional English terms",
        "layer": "hardware|network|os|platform|service",
        "entities": ["string"],
        "confidence": 0.0,
        "certainty": "confirmed|probable|ambiguous",
        "status": "confirmed|contributing|suspected|monitoring",
        "factor_type": "gpu_contention|external_load|cache_pressure|scheduler|mixed|unknown",
        "evidence_refs": ["tool:<step>:<tool_name>"],
        "evidence_interpretation": "Chinese explanation of why the cited evidence supports this cause",
        "evidence_summary": "Chinese evidence summary",
        "impact_summary": "Chinese impact summary",
        "distinguishing_verification": "Chinese distinguishing verification suggestion",
        "recommended_fix": {
          "plan_id": "proposal-<short-id>",
          "root_cause": "Chinese root cause description",
          "description": "Chinese remediation proposal description (proposal-only)",
          "steps": [
            {
              "step_id": 1,
              "description": "Chinese step description",
              "tool": "write tool name from the schema reference",
              "params": {},
              "verification": {"method": "wait", "wait_seconds": 30},
              "timeout": 60
            }
          ],
          "estimated_impact": "Chinese estimated impact",
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
    ],
    "confidence": 0.0,
    "next_action": "Chinese next action",
    "hypotheses": [
      {
        "description": "Chinese hypothesis description",
        "status": "confirmed|testing|eliminated",
        "evidence_for": ["string"],
        "evidence_against": ["string"],
        "confidence": 0.0
      }
    ],
    "impact_summary": "Chinese impact summary",
    "affected_services": ["string"],
    "triage_priority": "P0|P1|P2|P3",
    "diagnosis_certainty": "confirmed|probable|ambiguous"
  }
}

Additional root-cause rules:
- `diagnosis.root_cause` is the formal primary structure and must always be an array.
- If only one root cause is identified, return a single-element array.
- If multiple root causes are identified, keep the primary one at index 0.
- When multiple abnormal factors are observed, prefer separating them into distinct root-cause candidates if they can be independently validated, independently remediated, or independently observed after remediation.
- Only merge them into a single root-cause candidate when they form one inseparable causal chain or share the same remediation action.
- For TTFT cases, decide evidence attribution from source_tool + node_role + process_family + evidence role, not from tool name alone.
- TTFT hints: gpu.get_processes on the serving/GPU node finding fi_gpu_burn/gpu_burn is strong GPU contention evidence; process.find on an external node finding load_simulator/stress/benchmark/wrk is strong external load evidence; process.find on the serving/GPU node finding fi_gpu_burn or a PID from gpu.get_processes can support GPU contention or remediation verification.
- Each TTFT root_cause should cite the evidence_cards it relies on via evidence_refs. Do not label a root cause as external load if its cited evidence is only gpu.get_processes/fi_gpu_burn; do not label a root cause as GPU contention if its cited evidence is only process.find on an external load node.
- Keep remediation proposals embedded only in `root_cause[i].recommended_fix`.
- Do not output top-level `diagnosis.recommended_fix` or top-level `remediation_plan`.
- Do not output legacy `ranked_candidates`, `root_cause_layer`, or `root_cause_entities`.

If you include `root_cause[i].recommended_fix`, it must exactly match the schema in the write-tool reference.
If the evidence is strong enough to support a diagnosis, prefer returning proposal-only fixes under root-cause items.
Use a single conservative step if needed; verification may use method=wait.
Only set `root_cause[i].recommended_fix` to null when the evidence is genuinely insufficient to suggest a safe proposal for that cause.
Each recommended fix is proposal-only and must not assume any write action has run.
For `k8s.delete_pod`, valid params use `namespace` plus either `label_selector` or `pod_name`.
Never invent `pod_selector` for `k8s.delete_pod`.
For `kill_process`, `params` must include at least one target field: `pid`, `pid_or_name`, `process_name`, or `entity_id` formatted as `proc:<target>`.
For `kill_process`, if `signal` is provided, use `TERM|KILL|INT|HUP` (do not include `SIG` prefix).
Do not output `kill_process` with only `node` and natural-language target text in `description`.
Do not encode required remediation parameters only in free-form text; they must appear in `params`.
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
    """Build the final system prompt with tool references and active skill context.

    Purpose:
    - construct a single prompt that enforces the new multi-root-cause diagnosis contract.
    Input/Output:
    - input: tool registry and optional runtime filtering/context;
    - output: full prompt string consumed by the reasoning node.
    Compatibility rationale:
    - this prompt intentionally omits legacy single `root_cause` and `ranked_candidates` outputs.
    Why:
    - a strict prompt contract reduces post-processing drift and keeps backend/frontend data shape stable.
    """
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
    alert_catalog_block = render_alert_catalog_reference_block()
    blocks = [BASE_SYSTEM_PROMPT]
    if active_skill_block:
        blocks.append(active_skill_block)
    if alert_catalog_block:
        blocks.append(alert_catalog_block)
    blocks.extend([read_only_block, write_block])
    return "\n\n".join(blocks)


def render_active_skill_guidance_block(
    *,
    skill_id: str | None,
    skill_content: str | None,
) -> str:
    """Render active skill content as an auxiliary prompt block.

    Purpose:
    - inject workflow-specific SKILL.md guidance only when a skill is currently active.
    Input/Output:
    - input: active skill id and raw skill content;
    - output: formatted prompt block string, or empty string when inactive.
    Compatibility rationale:
    - unchanged from prior behavior because skill guidance format remains backward compatible.
    Why:
    - isolates skill instructions from the base prompt to keep the core contract readable and stable.
    """
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
    """Render a compact tool-schema block for LLM-side parameter grounding.

    Purpose:
    - serialize allowed tool definitions into deterministic prompt text.
    Input/Output:
    - input: registry plus optional safety-level and name filters;
    - output: multiline string containing tool schema snippets.
    Compatibility rationale:
    - filtering semantics stay compatible with the existing registry API.
    Why:
    - schema visibility reduces invalid tool/params generation in remediation proposals.
    """
    tools = registry.list_tools(safety_levels=safety_levels, tool_names=tool_names)
    lines = [title + ":"]
    if not tools:
        lines.append("- none")
        return "\n".join(lines)
    for tool in tools:
        lines.extend(_render_tool_definition(tool))
    return "\n".join(lines)


def _render_tool_definition(tool: ToolDefinition) -> list[str]:
    """Render one tool definition row used by `render_tool_reference_block`.

    Purpose:
    - convert a tool metadata object into deterministic textual bullets.
    Input/Output:
    - input: one ToolDefinition instance;
    - output: list of text lines for prompt embedding.
    Compatibility rationale:
    - output keys remain stable to avoid prompt parser drift in existing tests.
    Why:
    - deterministic rendering keeps prompt diffs reviewable and easier to debug.
    """
    schema = json.dumps(tool.params_schema, ensure_ascii=True, sort_keys=True)
    return [
        f"- {tool.name}",
        f"  description: {tool.description}",
        f"  safety_level: {tool.safety_level.value}",
        f"  params_schema: {schema}",
    ]


def _normalize_inline_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _compact_promql(expr: str, *, max_chars: int = _MAX_PROMQL_PREVIEW_CHARS) -> str:
    compact = re.sub(r"\s+", " ", str(expr or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _extract_backticks(text: str) -> list[str]:
    return [item.strip() for item in re.findall(r"`([^`]+)`", text) if item.strip()]


def _looks_like_metric_token(token: str) -> bool:
    return bool(re.search(r"[A-Za-z_][A-Za-z0-9_:]*", token))


def _parse_entity_alert_catalog(markdown: str) -> list[dict[str, str]]:
    lines = str(markdown or "").splitlines()
    in_rule_section = False
    in_promql = False
    promql_lines: list[str] = []
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    def flush_current() -> None:
        nonlocal current, promql_lines, in_promql
        if current:
            promql = _compact_promql("\n".join(promql_lines)) if promql_lines else ""
            if promql:
                current["promql"] = promql
            alert_name = _normalize_inline_text(current.get("alert_name", ""))
            if alert_name:
                current["alert_name"] = alert_name
                entries.append(current)
        current = None
        in_promql = False
        promql_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if re.match(r"^##\s+", stripped):
            lower = stripped.lower()
            is_rules_title = bool(re.search(r"^##\s*6(\.|\s|$)", lower)) or (
                "alert" in lower and "rule" in lower
            ) or ("告警" in stripped and "规则" in stripped)
            if is_rules_title:
                flush_current()
                in_rule_section = True
                continue
            if in_rule_section:
                flush_current()
                break

        if not in_rule_section:
            continue

        heading_match = re.match(r"^###\s+(.+?)\s*$", stripped)
        if heading_match:
            flush_current()
            current = {"alert_name": _normalize_inline_text(heading_match.group(1))}
            continue

        if current is None:
            continue

        if stripped.startswith("```"):
            fence_lang = stripped[3:].strip().lower()
            if in_promql:
                in_promql = False
            elif fence_lang in {"promql", ""}:
                in_promql = True
                promql_lines = []
            continue

        if in_promql:
            if stripped:
                promql_lines.append(stripped)
            continue

        backticks = _extract_backticks(stripped)
        if backticks:
            if "metric" not in current:
                metric_tokens = [token for token in backticks if _looks_like_metric_token(token)]
                if metric_tokens:
                    current["metric"] = ", ".join(metric_tokens[:2])
            if "condition" not in current:
                condition_tokens = [
                    token
                    for token in backticks
                    if any(op in token for op in (">", "<", "==", "!=", "=~")) or "histogram_quantile" in token.lower()
                ]
                if condition_tokens:
                    current["condition"] = _normalize_inline_text(condition_tokens[0])

    flush_current()

    deduped: dict[str, dict[str, str]] = {}
    for entry in entries:
        key = entry.get("alert_name", "").strip()
        if key and key not in deduped:
            deduped[key] = entry
    return list(deduped.values())


@lru_cache(maxsize=1)
def _load_alert_catalog_entries() -> list[dict[str, str]]:
    if not _ALERT_CATALOG_PATH.exists():
        return []
    try:
        text = _ALERT_CATALOG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = _ALERT_CATALOG_PATH.read_text(encoding="utf-8", errors="ignore")
    return _parse_entity_alert_catalog(text)


def render_alert_catalog_reference_block() -> str:
    entries = _load_alert_catalog_entries()
    if not entries:
        return ""

    lines = [
        "Alert catalog reference (from sre_agent/conf/Entity.md):",
        "- Treat this as the deployment-specific alertname -> metric/PromQL source of truth.",
        "- If the current alert matches one of these entries, use the mapped metric first.",
    ]
    for item in entries[:_MAX_ALERT_CATALOG_ENTRIES]:
        alert_name = item.get("alert_name", "unknown")
        metric = item.get("metric", "n/a")
        condition = item.get("condition", "")
        promql = item.get("promql", "")
        row = f"- {alert_name} | metric={metric}"
        if condition:
            row += f" | condition={condition}"
        if promql:
            row += f" | promql={promql}"
        lines.append(row)
    if len(entries) > _MAX_ALERT_CATALOG_ENTRIES:
        lines.append(f"- ... {len(entries) - _MAX_ALERT_CATALOG_ENTRIES} more entries omitted for brevity.")

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
    """Build the user-facing diagnosis prompt from alert and runtime context.

    Purpose:
    - provide run-specific evidence hints while preserving the system-level contract.
    Input/Output:
    - input: alert payload, tools, guidance hints, and optional extra context;
    - output: final multi-line prompt string for the diagnose workflow.
    Compatibility rationale:
    - field names remain unchanged to keep upstream orchestrator integrations stable.
    Why:
    - explicit context lines improve reasoning quality while keeping prompt construction deterministic.
    """
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
