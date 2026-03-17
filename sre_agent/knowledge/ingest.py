"""Knowledge ingestion orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sre_agent.knowledge.runbook import Runbook
from sre_agent.knowledge.store import KnowledgeStore


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
