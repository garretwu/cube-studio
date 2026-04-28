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


def _serialize_skill_listing(skill: Any) -> dict[str, Any]:
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "description": skill.summary,
        "match_score": skill.match_score,
    }


def _serialize_loaded_skill(skill: Any) -> dict[str, Any]:
    return {
        "skill_id": skill.id,
        "name": skill.name,
        "scope": skill.scope,
        "description": skill.description,
        "summary": skill.summary,
        "source": skill.source,
        "path": skill.path,
        "scripts": list(skill.scripts),
        "script_descriptions": dict(getattr(skill, "script_descriptions", {}) or {}),
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


def _flatten_text_parts(value: Any) -> list[str]:
    parts: list[str] = []
    if isinstance(value, str):
        text = value.strip()
        if text:
            parts.append(text)
        return parts
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if key_text:
                parts.append(key_text)
            parts.extend(_flatten_text_parts(item))
        return parts
    if isinstance(value, list):
        for item in value:
            parts.extend(_flatten_text_parts(item))
        return parts
    if value is None:
        return parts
    text = str(value).strip()
    if text:
        parts.append(text)
    return parts


def _dedupe_terms(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in parts:
        text = " ".join(str(item).strip().split())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


def _build_skill_query(params: dict[str, Any], context: ToolExecutionContext) -> str:
    explicit_query = str(params.get("query", "") or "").strip()
    if explicit_query:
        return explicit_query

    parts: list[str] = []
    alert_name = str(params.get("alert_name", "") or "").strip()
    if alert_name:
        parts.append(alert_name)

    labels = params.get("labels")
    annotations = params.get("annotations")
    parts.extend(_flatten_text_parts(labels))
    parts.extend(_flatten_text_parts(annotations))

    structured_query = " ".join(_dedupe_terms(parts)).strip()
    if structured_query:
        return structured_query

    metadata_query = str(context.metadata.get("query", "") or "").strip()
    if metadata_query:
        return metadata_query
    return ""


async def list_skills(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    refresh = bool(params.get("refresh", False))
    query = _build_skill_query(params, context)
    if not query:
        raise ToolValidationError(
            "parameter 'query' is required; or provide alert_name/labels/annotations so a query can be derived"
        )
    skills = registry.discover(refresh=refresh)
    # Keep skill discovery decisive: return only the single highest-scoring skill.
    skills = rank_skills(query, skills, top_k=1)
    return {
        "skills": [_serialize_skill_listing(skill) for skill in skills],
        "warnings": registry.warnings,
    }


async def load_skill(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    registry = _get_skill_registry(context)
    skill_id = _require_str(params, "skill_id")
    skill, content = registry.load_skill(skill_id)
    return {
        **_serialize_loaded_skill(skill),
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
