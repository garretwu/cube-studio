"""Claude-style skill tools for discovery, loading, reference reading, and execution."""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from sre_agent.skills import SkillExecutor, SkillPolicy, SkillRegistry, rank_skills
from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


def _get_skill_registry(context: ToolExecutionContext) -> SkillRegistry:
    registry = context.metadata.get("skill_registry")
    if isinstance(registry, SkillRegistry):
        return registry
    return SkillRegistry()


def _get_skill_policy(context: ToolExecutionContext) -> SkillPolicy:
    policy = context.metadata.get("skill_policy")
    if isinstance(policy, SkillPolicy):
        return policy
    return SkillPolicy()


def _get_skill_executor(context: ToolExecutionContext) -> SkillExecutor:
    executor = context.metadata.get("skill_executor")
    if isinstance(executor, SkillExecutor):
        return executor
    return SkillExecutor()


def _serialize_skill(skill: Any) -> dict[str, Any]:
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "scope": skill.scope,
        "description": skill.description,
        "summary": skill.summary,
        "source": skill.source,
        "path": skill.path,
        "scripts": list(skill.scripts),
        "references": list(skill.references),
        "permissions": list(skill.permissions),
        "tags": list(skill.tags),
        "match_score": skill.match_score,
    }


def _coerce_string_list(value: Any, *, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    raise ToolValidationError(f"parameter {key!r} must be a list of strings")


async def list_skills(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    refresh = bool(params.get("refresh", False))
    query = str(params.get("query", "") or "").strip()
    top_k = max(1, int(params.get("top_k", 20)))
    skills = registry.discover(refresh=refresh)
    if query:
        skills = rank_skills(query, skills, top_k=top_k)
    else:
        skills = skills[:top_k]
    return {
        "skills": [_serialize_skill(skill) for skill in skills],
        "warnings": registry.warnings,
    }


async def load_skill(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    skill_id = _require_str(params, "skill_id")
    skill, content = registry.load_skill(skill_id)
    return {
        **_serialize_skill(skill),
        "content": content,
        "version": skill.version,
    }


async def read_skill_ref(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    skill_id = _require_str(params, "skill_id")
    reference = _require_str(params, "reference")
    path = registry.resolve_reference_path(skill_id, reference)
    return {
        "skill_id": skill_id,
        "reference": reference,
        "path": str(path),
        "content": path.read_text(encoding="utf-8"),
    }


async def run_skill(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    executor = _get_skill_executor(context)
    policy = _get_skill_policy(context)

    skill_id = _require_str(params, "skill_id")
    skill = registry.get(skill_id)
    script_raw = params.get("script")
    script = str(script_raw).strip() if script_raw is not None else None
    if script == "":
        script = None
    args = _coerce_string_list(params.get("args", []), key="args")

    result = await executor.execute(
        skill=skill,
        script=script,
        args=args,
        policy=policy,
        skill_registry=registry,
        timeout_sec=params.get("timeout_sec"),
        output_limit=params.get("output_limit"),
    )
    return asdict(result)
