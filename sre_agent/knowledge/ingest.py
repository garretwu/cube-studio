"""Knowledge ingestion orchestrator."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sre_agent.knowledge.runbook import Runbook
from sre_agent.knowledge.store import KnowledgeStore

_MARKDOWN_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class KnowledgeIngestSummary:
    source_path: str
    category: str
    file_count: int
    chunk_count: int
    skipped_files: int


def _split_markdown_sections(content: str) -> list[tuple[str, str]]:
    matches = list(_MARKDOWN_HEADER_RE.finditer(content))
    if not matches:
        return [("", content)]

    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        header = match.group(2).strip()
        body = content[start:end].strip()
        if body:
            sections.append((header, body))
    return sections or [("", content)]


def _extract_pdf_text(path: Path) -> str:
    readers: list[type[Any]] = []
    try:  # pragma: no cover - optional dependency path
        from pypdf import PdfReader  # type: ignore

        readers.append(PdfReader)
    except ImportError:  # pragma: no cover - optional dependency path
        pass

    try:  # pragma: no cover - optional dependency path
        from PyPDF2 import PdfReader as LegacyPdfReader  # type: ignore

        readers.append(LegacyPdfReader)
    except ImportError:  # pragma: no cover - optional dependency path
        pass

    for reader_cls in readers:
        reader = reader_cls(str(path))
        parts = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\n".join(part for part in parts if part)
        if text.strip():
            return text
    raise RuntimeError("PDF ingestion requires pypdf or PyPDF2")


def _runbook_from_mapping(payload: dict[str, Any], source: str) -> Runbook:
    title = str(payload.get("title") or payload.get("name") or Path(source).stem)
    symptoms = payload.get("symptoms") or payload.get("symptom") or []
    if isinstance(symptoms, str):
        symptom = symptoms
    else:
        symptom = "; ".join(str(item) for item in symptoms)

    root_cause = str(payload.get("root_cause") or payload.get("cause") or "")
    if not root_cause:
        root_cause = "unknown"

    fix_steps = payload.get("fix_steps") or payload.get("actions") or []
    diagnosis_steps = payload.get("diagnosis_steps") or []
    action_parts: list[str] = []
    for step in diagnosis_steps:
        if isinstance(step, dict) and step.get("description"):
            action_parts.append(f"diagnosis: {step['description']}")
    for step in fix_steps:
        if isinstance(step, dict):
            condition = step.get("condition")
            action = step.get("action") or step.get("description")
            if condition and action:
                action_parts.append(f"{condition}: {action}")
            elif action:
                action_parts.append(str(action))
        else:
            action_parts.append(str(step))

    action = "\n".join(part for part in action_parts if part) or json.dumps(payload, ensure_ascii=False)
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    return Runbook(
        title=title,
        symptom=symptom,
        root_cause=root_cause,
        action=action,
        tags=[str(tag) for tag in tags],
        source=source,
    )


@dataclass
class KnowledgeIngestor:
    store: KnowledgeStore

    async def ingest_text(
        self,
        *,
        content: str,
        source: str,
        category: str,
        version: str = "v1",
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        return await self.store.ingest_document(
            content=content,
            source=source,
            category=category,
            version=version,
            metadata=metadata,
        )

    async def ingest_pdf(self, path: str | Path, category: str, version: str = "v1") -> int:
        source_path = Path(path)
        await self.store.delete_by_source(str(source_path))
        text = _extract_pdf_text(source_path)
        ids = await self.ingest_text(
            content=text,
            source=str(source_path),
            category=category,
            version=version,
            metadata={"file_type": "pdf"},
        )
        return len(ids)

    async def ingest_markdown(self, path: str | Path, category: str, version: str = "v1") -> int:
        source_path = Path(path)
        content = source_path.read_text(encoding="utf-8")
        await self.store.delete_by_source(str(source_path))

        chunk_count = 0
        for header, section_text in _split_markdown_sections(content):
            ids = await self.ingest_text(
                content=section_text,
                source=str(source_path),
                category=category,
                version=version,
                metadata={"file_type": "markdown", "section": header},
            )
            chunk_count += len(ids)
        return chunk_count

    async def ingest_runbook(self, runbook: Runbook | str | Path, version: str = "v1") -> str:
        if isinstance(runbook, Runbook):
            return await self.store.add_runbook(runbook, version=version)

        source_path = Path(runbook)
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"runbook must decode to a mapping: {source_path}")
        model = _runbook_from_mapping(payload, str(source_path))
        await self.store.delete_by_source(str(source_path))
        return await self.store.add_runbook(model, version=version)

    async def ingest_path(
        self,
        *,
        path: str | Path,
        category: str,
        version: str = "v1",
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeIngestSummary:
        source_path = Path(path)
        if not source_path.exists():
            raise FileNotFoundError(f"knowledge source path does not exist: {source_path}")

        files = [source_path] if source_path.is_file() else sorted(item for item in source_path.rglob("*") if item.is_file())
        file_count = 0
        chunk_count = 0
        skipped_files = 0

        for file_path in files:
            suffix = file_path.suffix.lower()
            try:
                if suffix == ".pdf":
                    chunk_count += await self.ingest_pdf(file_path, category=category, version=version)
                elif suffix in {".md", ".markdown"}:
                    chunk_count += await self.ingest_markdown(file_path, category=category, version=version)
                elif suffix in {".yaml", ".yml"}:
                    await self.ingest_runbook(file_path, version=version)
                    chunk_count += 1
                else:
                    content = file_path.read_text(encoding="utf-8")
                    if not content.strip():
                        skipped_files += 1
                        continue
                    await self.store.delete_by_source(str(file_path))
                    file_metadata = dict(metadata or {})
                    file_metadata["relative_path"] = str(
                        file_path.relative_to(source_path if source_path.is_dir() else source_path.parent)
                    )
                    ids = await self.ingest_text(
                        content=content,
                        source=str(file_path),
                        category=category,
                        version=version,
                        metadata=file_metadata,
                    )
                    chunk_count += len(ids)
                file_count += 1
            except UnicodeDecodeError:
                skipped_files += 1
            except RuntimeError:
                if suffix == ".pdf":
                    skipped_files += 1
                else:
                    raise

        return KnowledgeIngestSummary(
            source_path=str(source_path),
            category=category,
            file_count=file_count,
            chunk_count=chunk_count,
            skipped_files=skipped_files,
        )
