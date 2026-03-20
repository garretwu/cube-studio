from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SkillRegistryError(RuntimeError):
    """Raised when skill metadata or layout is invalid."""


@dataclass(frozen=True)
class SkillStep:
    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True)
class SkillDescriptor:
    id: str
    name: str
    scope: str
    summary: str
    source: str
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    match_score: float = 0.0
    steps: list[SkillStep] = field(default_factory=list)


class SkillRegistry:
    """Discovers and validates markdown-based skills."""

    REQUIRED_FRONTMATTER_KEYS = ("name", "description")

    def __init__(self, *, root: Path | None = None) -> None:
        self.root = root or (Path(__file__).resolve().parent / "builtin")
        self._skills: dict[str, SkillDescriptor] = {}

    def discover(self) -> list[SkillDescriptor]:
        self._skills.clear()
        if not self.root.exists():
            return []

        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            skill = self._parse_skill_file(skill_file)
            if skill.id in self._skills:
                raise SkillRegistryError(f"duplicate skill id: {skill.id}")
            self._skills[skill.id] = skill
        return self.list_skills()

    def list_skills(self) -> list[SkillDescriptor]:
        return [self._skills[key] for key in sorted(self._skills)]

    def get(self, skill_id: str) -> SkillDescriptor:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise SkillRegistryError(f"skill not found: {skill_id}")
        return skill

    def _parse_skill_file(self, path: Path) -> SkillDescriptor:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(raw, source=str(path))
        self._validate_frontmatter(frontmatter, source=str(path))

        runtime_metadata = self._extract_named_yaml_block(body, heading="## Runtime Metadata", source=str(path))
        steps_payload = self._extract_named_yaml_block(body, heading="## Steps", source=str(path))
        if runtime_metadata is None:
            runtime_metadata = {}
        if not isinstance(runtime_metadata, dict):
            raise SkillRegistryError(f"runtime metadata block must be a mapping: {path}")

        # Claude-first front-matter keys are name/description.
        # Engineering metadata can come from either front-matter or runtime metadata.
        skill_id = self._get_optional_text(frontmatter, "id") or self._get_optional_text(runtime_metadata, "id")
        if not skill_id:
            skill_id = f"builtin-{path.parent.name}"
        scope = self._get_optional_text(frontmatter, "scope") or self._get_optional_text(runtime_metadata, "scope")
        if not scope:
            scope = "builtin"

        permissions = self._resolve_text_list(frontmatter, runtime_metadata, key="permissions")
        tags = self._resolve_text_list(frontmatter, runtime_metadata, key="tags")
        summary = str(frontmatter["description"]).strip()

        steps = self._parse_steps(steps_payload, source=str(path))
        source_ref = f"{scope}://{path.parent.name}"
        return SkillDescriptor(
            id=skill_id,
            name=str(frontmatter["name"]).strip(),
            scope=scope,
            summary=summary,
            source=source_ref,
            permissions=permissions,
            tags=tags,
            steps=steps,
        )

    @staticmethod
    def _split_frontmatter(raw: str, *, source: str) -> tuple[dict[str, Any], str]:
        text = raw.strip()
        if not text.startswith("---"):
            raise SkillRegistryError(f"missing yaml front matter: {source}")
        chunks = text.split("---", 2)
        if len(chunks) < 3:
            raise SkillRegistryError(f"invalid yaml front matter: {source}")

        meta_text = chunks[1].strip()
        body = chunks[2]
        metadata = yaml.safe_load(meta_text) or {}
        if not isinstance(metadata, dict):
            raise SkillRegistryError(f"front matter must be a mapping: {source}")
        return metadata, body

    def _validate_frontmatter(self, metadata: dict[str, Any], *, source: str) -> None:
        for key in self.REQUIRED_FRONTMATTER_KEYS:
            value = metadata.get(key)
            if value is None:
                raise SkillRegistryError(f"missing required metadata '{key}': {source}")
            if isinstance(value, str) and not value.strip():
                raise SkillRegistryError(f"metadata '{key}' must not be blank: {source}")

    def _resolve_text_list(self, frontmatter: dict[str, Any], runtime_meta: dict[str, Any], *, key: str) -> list[str]:
        if key in frontmatter:
            return self._as_str_list(frontmatter[key], key=key, source="front-matter")
        if key in runtime_meta:
            return self._as_str_list(runtime_meta[key], key=key, source="runtime metadata")
        return []

    @staticmethod
    def _get_optional_text(metadata: dict[str, Any], key: str) -> str:
        value = metadata.get(key)
        if value is None:
            return ""
        text = str(value).strip()
        return text

    @staticmethod
    def _as_str_list(value: Any, *, key: str, source: str) -> list[str]:
        if not isinstance(value, list):
            raise SkillRegistryError(f"metadata '{key}' must be a list: {source}")
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise SkillRegistryError(f"metadata '{key}' contains blank value: {source}")
            out.append(text)
        return out

    @staticmethod
    def _parse_steps(steps_payload: Any, *, source: str) -> list[SkillStep]:
        if not isinstance(steps_payload, list):
            raise SkillRegistryError(f"steps yaml must be a list: {source}")
        if not steps_payload:
            raise SkillRegistryError(f"skill must define at least one step: {source}")
        steps: list[SkillStep] = []
        for idx, item in enumerate(steps_payload):
            if not isinstance(item, dict):
                raise SkillRegistryError(f"step #{idx + 1} must be a mapping: {source}")
            tool = str(item.get("tool", "")).strip()
            if not tool:
                raise SkillRegistryError(f"step #{idx + 1} missing tool: {source}")
            params = item.get("params", {})
            if not isinstance(params, dict):
                raise SkillRegistryError(f"step #{idx + 1} params must be mapping: {source}")
            description = str(item.get("description", "")).strip()
            steps.append(SkillStep(tool=tool, params=params, description=description))
        return steps

    @staticmethod
    def _extract_named_yaml_block(body: str, *, heading: str, source: str) -> Any | None:
        heading_pos = body.find(heading)
        if heading_pos < 0:
            if heading == "## Runtime Metadata":
                return None
            raise SkillRegistryError(f"missing section '{heading}': {source}")

        segment = body[heading_pos + len(heading) :]
        fence = "```yaml"
        start = segment.find(fence)
        if start < 0:
            raise SkillRegistryError(f"missing yaml block under '{heading}': {source}")
        start += len(fence)
        end = segment.find("```", start)
        if end < 0:
            raise SkillRegistryError(f"unterminated yaml block under '{heading}': {source}")
        text = segment[start:end].strip()
        return yaml.safe_load(text)
