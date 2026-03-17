"""Knowledge retrieval helpers."""
from __future__ import annotations

from dataclasses import dataclass

from sre_agent.knowledge.store import KnowledgeChunk, KnowledgeStore


@dataclass
class KnowledgeRetriever:
    store: KnowledgeStore

    async def retrieve(self, query: str, category: str | None = None, top_k: int = 5) -> list[KnowledgeChunk]:
        return await self.store.search(query=query, category=category, top_k=top_k)

    async def render_context(self, query: str, category: str | None = None, top_k: int = 5) -> str:
        chunks = await self.retrieve(query=query, category=category, top_k=top_k)
        return "\n\n".join(chunk.content for chunk in chunks)
