"""Knowledge ingestion orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sre_agent.knowledge.runbook import Runbook
from sre_agent.knowledge.store import KnowledgeStore


@dataclass(frozen=True)
class KnowledgeIngestSummary:
    source_path: str
    category: str
    file_count: int
    chunk_count: int
    skipped_files: int


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

    async def ingest_runbook(self, runbook: Runbook, version: str = "v1") -> str:
        return await self.store.add_runbook(runbook, version=version)

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
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                skipped_files += 1
                continue
            if not content.strip():
                skipped_files += 1
                continue
            await self.store.delete_by_source(str(file_path))
            file_metadata = dict(metadata or {})
            file_metadata["relative_path"] = str(file_path.relative_to(source_path if source_path.is_dir() else source_path.parent))
            ids = await self.ingest_text(
                content=content,
                source=str(file_path),
                category=category,
                version=version,
                metadata=file_metadata,
            )
            file_count += 1
            chunk_count += len(ids)

        return KnowledgeIngestSummary(
            source_path=str(source_path),
            category=category,
            file_count=file_count,
            chunk_count=chunk_count,
            skipped_files=skipped_files,
        )
