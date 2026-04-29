from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


class SkillRegistryError(RuntimeError):
    """Raised when skill metadata or layout is invalid."""


@dataclass(frozen=True)
class SkillRoot:
    scope: str
    path: Path


@dataclass
class SkillDescriptor:
    id: str
    name: str
    scope: str
    summary: str
    description: str
    source: str
    path: str
    skill_file: Path
    directory: Path
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    match_score: float = 0.0
    scripts: list[str] = field(default_factory=list)
    script_descriptions: dict[str, str] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    version: str | None = None
    script_paths: dict[str, Path] = field(default_factory=dict, repr=False)
    reference_paths: dict[str, Path] = field(default_factory=dict, repr=False)

    def read_markdown(self) -> str:
        return self.skill_file.read_text(encoding="utf-8")


class SkillRegistry:
    """Discovers and indexes Claude-style skills."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        roots: list[SkillRoot] | None = None,
    ) -> None:
        self.roots = roots or self._default_roots(root=root)
        self._skills: dict[str, SkillDescriptor] = {}
        self._warnings: list[str] = []

    @staticmethod
    def _default_roots(*, root: Path | None = None) -> list[SkillRoot]:
        if root is not None:
            return [SkillRoot(scope="custom", path=Path(root).resolve())]
        base = Path(__file__).resolve().parent
        return [
            SkillRoot(scope="builtin", path=(base / "builtin").resolve()),
            SkillRoot(scope="custom", path=(base / "custom").resolve()),
            SkillRoot(scope="shared", path=(Path.home() / ".claude" / "skills").resolve()),
        ]

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def refresh(self) -> list[SkillDescriptor]:
        self._skills.clear()
        self._warnings.clear()

        for root in self.roots:
            if not root.path.exists():
                continue
            for skill_file in sorted(root.path.rglob("SKILL.md")):
                try:
                    skill = self._parse_skill_file(skill_file, root=root)
                except Exception as exc:  # noqa: BLE001
                    self._warnings.append(f"{skill_file}: {exc}")
                    continue
                if skill.id in self._skills:
                    self._warnings.append(f"{skill_file}: duplicate skill id: {skill.id}")
                    continue
                self._skills[skill.id] = skill
        return self.list_skills()

    def discover(self, *, refresh: bool = False) -> list[SkillDescriptor]:
        if refresh or not self._skills:
            return self.refresh()
        return self.list_skills()

    def list_skills(self, *, refresh: bool = False) -> list[SkillDescriptor]:
        if refresh or not self._skills:
            self.refresh()
        return [self._skills[key] for key in sorted(self._skills)]

    def get(self, skill_id: str, *, refresh_if_missing: bool = True) -> SkillDescriptor:
        skill = self._skills.get(skill_id)
        if skill is None and refresh_if_missing:
            self.refresh()
            skill = self._skills.get(skill_id)
        if skill is None:
            raise SkillRegistryError(f"skill not found: {skill_id}")
        return skill

    def load_skill(self, skill_id: str) -> tuple[SkillDescriptor, str]:
        skill = self.get(skill_id)
        return skill, skill.read_markdown()

    def resolve_script_path(self, skill_id: str, script: str) -> Path:
        skill = self.get(skill_id)
        normalized = self._normalize_relative_path(script, label="script")
        path = skill.script_paths.get(normalized)
        if path is None:
            raise SkillRegistryError(f"script not found for {skill_id}: {normalized}")
        return path

    def resolve_reference_path(self, skill_id: str, reference: str) -> Path:
        skill = self.get(skill_id)
        normalized = self._normalize_relative_path(reference, label="reference")
        path = skill.reference_paths.get(normalized)
        if path is None:
            raise SkillRegistryError(f"reference not found for {skill_id}: {normalized}")
        return path

    @staticmethod
    def _normalize_relative_path(value: str, *, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise SkillRegistryError(f"{label} path is required")
        pure = PurePosixPath(text.replace("\\", "/"))
        if pure.is_absolute():
            raise SkillRegistryError(f"{label} path must be relative: {value}")
        if any(part == ".." for part in pure.parts):
            raise SkillRegistryError(f"{label} path must not escape the skill directory: {value}")
        normalized = pure.as_posix()
        if normalized in {"", "."}:
            raise SkillRegistryError(f"{label} path is invalid: {value}")
        return normalized

    def _parse_skill_file(self, path: Path, *, root: SkillRoot) -> SkillDescriptor:
        raw = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(raw, source=str(path))
        runtime_metadata = self._extract_named_yaml_block(body, heading="## Runtime Metadata", source=str(path))
        if runtime_metadata is not None and not isinstance(runtime_metadata, dict):
            raise SkillRegistryError("runtime metadata block must be a mapping")

        relative_path = path.parent.relative_to(root.path).as_posix()
        metadata = runtime_metadata or {}
        skill_id = self._resolve_skill_id(frontmatter, metadata, relative_path)
        name = self._resolve_display_name(frontmatter, body, path)
        description = self._resolve_description(frontmatter, body)
        summary = self._summarize_description(description)
        permissions = self._resolve_text_list(frontmatter, metadata, key="permissions")
        tags = self._resolve_text_list(frontmatter, metadata, key="tags")
        scripts, script_paths = self._collect_scripts(path.parent)
        script_descriptions = self._extract_script_descriptions(body, scripts)
        references, reference_paths = self._collect_references(path.parent)
        version = self._resolve_optional_text(frontmatter, metadata, key="version")

        return SkillDescriptor(
            id=skill_id,
            name=name,
            scope=root.scope,
            summary=summary,
            description=description,
            source=f"{root.scope}://{relative_path}",
            path=relative_path,
            skill_file=path,
            directory=path.parent,
            permissions=permissions,
            tags=tags,
            scripts=scripts,
            script_descriptions=script_descriptions,
            references=references,
            metadata={"frontmatter": frontmatter, "runtime_metadata": metadata},
            version=version or None,
            script_paths=script_paths,
            reference_paths=reference_paths,
        )

    @staticmethod
    def _split_frontmatter(raw: str, *, source: str) -> tuple[dict[str, Any], str]:
        text = raw.lstrip()
        if not text.startswith("---"):
            return {}, raw
        chunks = text.split("---", 2)
        if len(chunks) < 3:
            raise SkillRegistryError(f"invalid yaml front matter: {source}")
        meta_text = chunks[1].strip()
        body = chunks[2]
        metadata = yaml.safe_load(meta_text) or {}
        if not isinstance(metadata, dict):
            raise SkillRegistryError(f"front matter must be a mapping: {source}")
        return metadata, body

    @staticmethod
    def _resolve_skill_id(frontmatter: dict[str, Any], metadata: dict[str, Any], relative_path: str) -> str:
        explicit_id = str(frontmatter.get("id") or metadata.get("id") or "").strip()
        if explicit_id:
            return explicit_id
        name_override = str(frontmatter.get("name") or "").strip()
        if name_override and SkillRegistry._looks_like_machine_skill_name(name_override):
            return name_override
        return relative_path

    @staticmethod
    def _looks_like_machine_skill_name(value: str) -> bool:
        if not value or any(ch.isspace() for ch in value):
            return False
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._/-")
        return all(ch in allowed for ch in value)

    @staticmethod
    def _resolve_display_name(frontmatter: dict[str, Any], body: str, path: Path) -> str:
        name = str(frontmatter.get("title") or frontmatter.get("display_name") or frontmatter.get("name") or "").strip()
        if name:
            return name
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return path.parent.name

    @staticmethod
    def _resolve_description(frontmatter: dict[str, Any], body: str) -> str:
        description = str(frontmatter.get("description") or "").strip()
        if description:
            return description
        return SkillRegistry._extract_first_paragraph(body) or "No description provided."

    @staticmethod
    def _extract_first_paragraph(body: str) -> str:
        lines = body.splitlines()
        in_fence = False
        paragraph: list[str] = []
        for raw_line in lines:
            line = raw_line.strip()
            if line.startswith("```"):
                in_fence = not in_fence
                if paragraph:
                    break
                continue
            if in_fence:
                continue
            if not line:
                if paragraph:
                    break
                continue
            if line.startswith("#"):
                if paragraph:
                    break
                continue
            paragraph.append(line)
        return " ".join(paragraph).strip()

    @staticmethod
    def _summarize_description(description: str, *, limit: int = 220) -> str:
        text = str(description or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _resolve_text_list(frontmatter: dict[str, Any], metadata: dict[str, Any], *, key: str) -> list[str]:
        if key in frontmatter:
            return SkillRegistry._as_str_list(frontmatter[key], key=key, source="front-matter")
        if key in metadata:
            return SkillRegistry._as_str_list(metadata[key], key=key, source="runtime metadata")
        return []

    @staticmethod
    def _resolve_optional_text(frontmatter: dict[str, Any], metadata: dict[str, Any], *, key: str) -> str:
        value = frontmatter.get(key)
        if value is None:
            value = metadata.get(key)
        return str(value).strip() if value is not None else ""

    @staticmethod
    def _as_str_list(value: Any, *, key: str, source: str) -> list[str]:
        if not isinstance(value, list):
            raise SkillRegistryError(f"metadata '{key}' must be a list: {source}")
        items: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise SkillRegistryError(f"metadata '{key}' contains blank value: {source}")
            items.append(text)
        return items

    @staticmethod
    def _extract_named_yaml_block(body: str, *, heading: str, source: str) -> Any | None:
        heading_pos = body.find(heading)
        if heading_pos < 0:
            return None
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

    @staticmethod
    def _collect_scripts(skill_dir: Path) -> tuple[list[str], dict[str, Path]]:
        script_map: dict[str, Path] = {}
        script_dir = skill_dir / "scripts"
        if script_dir.exists():
            for item in sorted(script_dir.rglob("*")):
                if item.is_file():
                    script_map[item.relative_to(script_dir).as_posix()] = item
        else:
            for item in sorted(skill_dir.iterdir()):
                if not item.is_file() or item.name == "SKILL.md":
                    continue
                if item.suffix.lower() not in {".sh", ".py", ".bash"}:
                    continue
                script_map[item.name] = item
        return sorted(script_map), script_map

    @staticmethod
    def _extract_script_descriptions(body: str, scripts: list[str]) -> dict[str, str]:
        """Best-effort extraction of script intent from SKILL.md.

        Skills commonly document scripts as a bullet containing the script name
        followed by nested bullets. Keep this parser deliberately permissive so
        it also works for English/Chinese prose and inline code blocks.
        """
        if not scripts:
            return {}

        lines = body.splitlines()
        script_set = set(scripts)
        descriptions: dict[str, str] = {}
        active_script: str | None = None
        active_parts: list[str] = []

        def flush() -> None:
            nonlocal active_script, active_parts
            if active_script is None:
                return
            text = " ".join(part.strip() for part in active_parts if part.strip())
            if text and active_script not in descriptions:
                descriptions[active_script] = " ".join(text.split())
            active_script = None
            active_parts = []

        def script_on_line(line: str) -> str | None:
            for snippet in re.findall(r"`([^`]+)`", line):
                candidate = PurePosixPath(str(snippet).replace("\\", "/")).name
                if candidate in script_set:
                    return candidate
                if str(snippet).strip() in script_set:
                    return str(snippet).strip()
            for script in scripts:
                if script in line:
                    return script
                if PurePosixPath(script).name in line:
                    return script
            return None

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("```"):
                continue
            matched_script = script_on_line(line)
            if matched_script is not None:
                flush()
                active_script = matched_script
                remainder = line
                for marker in (f"`{matched_script}`", matched_script, PurePosixPath(matched_script).name):
                    remainder = remainder.replace(marker, " ")
                remainder = remainder.lstrip("-*0123456789. ):\t").strip()
                if remainder:
                    active_parts.append(remainder)
                continue
            if active_script is None:
                continue
            if line.startswith("#"):
                flush()
                continue
            if re.match(r"^[-*]\s+`?[\w./-]+\.(?:sh|bash|py)`?\b", line):
                flush()
                continue
            if line.startswith(("-", "*")) or raw_line.startswith((" ", "\t")):
                active_parts.append(line.lstrip("-* \t"))
                continue
            flush()

        flush()
        return descriptions

    @staticmethod
    def _collect_references(skill_dir: Path) -> tuple[list[str], dict[str, Path]]:
        ref_map: dict[str, Path] = {}
        ref_dir = skill_dir / "references"
        if ref_dir.exists():
            for item in sorted(ref_dir.rglob("*")):
                if item.is_file():
                    ref_map[item.relative_to(ref_dir).as_posix()] = item
        return sorted(ref_map), ref_map
