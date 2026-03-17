"""Knowledge store backed by ChromaDB or a local compatibility implementation."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sre_agent.knowledge.chunker import chunk_text
from sre_agent.knowledge.runbook import Runbook

try:
    import chromadb  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import chromadb


@dataclass(frozen=True)
class KnowledgeChunk:
    content: str
    source: str
    category: str
    score: float
    metadata: dict[str, Any]


class KnowledgeStore:
    """Semantic knowledge store with optional persistent or ephemeral backing."""

    def __init__(self, persist_dir: str | None = "./knowledge_db") -> None:
        self.persist_dir = persist_dir
        if persist_dir:
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=persist_dir)
        else:
            self.client = chromadb.EphemeralClient()
        self.collection = self.client.get_or_create_collection(
            name="sre_knowledge",
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _document_id(content: str, metadata: dict[str, Any]) -> str:
        identity = "|".join(
            [
                content,
                str(metadata.get("source", "")),
                str(metadata.get("category", "")),
                str(metadata.get("version", "")),
                str(metadata.get("chunk_index", "")),
            ]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    async def add(self, content: str, metadata: dict[str, Any]) -> str:
        doc_id = self._document_id(content, metadata)
        await asyncio.to_thread(
            self.collection.upsert,
            documents=[content],
            metadatas=[dict(metadata)],
            ids=[doc_id],
        )
        return doc_id

    async def ingest_document(
        self,
        *,
        content: str,
        source: str,
        category: str,
        version: str = "v1",
        chunk_size: int = 500,
        overlap: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        base_metadata = {"source": source, "category": category, "version": version}
        if metadata:
            base_metadata.update(metadata)
        doc_ids: list[str] = []
        for index, chunk in enumerate(chunk_text(content, chunk_size=chunk_size, overlap=overlap)):
            chunk_metadata = dict(base_metadata)
            chunk_metadata["chunk_index"] = index
            doc_ids.append(await self.add(chunk, chunk_metadata))
        return doc_ids

    async def add_runbook(self, runbook: Runbook, version: str = "v1") -> str:
        return await self.add(
            runbook.to_document(),
            {
                "source": runbook.source or runbook.title,
                "category": "runbook",
                "version": version,
                "symptom": runbook.symptom,
                "title": runbook.title,
                "tags": ",".join(runbook.tags),
            },
        )

    async def search(self, query: str, category: str | None = None, top_k: int = 5) -> list[KnowledgeChunk]:
        where = {"category": category} if category else None
        results = await asyncio.to_thread(
            self.collection.query,
            query_texts=[query],
            n_results=top_k,
            where=where,
        )
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        chunks: list[KnowledgeChunk] = []
        for document, metadata, distance in zip(documents, metadatas, distances, strict=False):
            meta = dict(metadata or {})
            chunks.append(
                KnowledgeChunk(
                    content=document,
                    source=str(meta.get("source", "")),
                    category=str(meta.get("category", "")),
                    score=max(0.0, 1.0 - float(distance)),
                    metadata=meta,
                )
            )
        return chunks

    async def search_runbooks(self, symptom: str, top_k: int = 5) -> list[Runbook]:
        results = await self.search(symptom, category="runbook", top_k=top_k)
        runbooks: list[Runbook] = []
        for item in results:
            runbooks.append(
                Runbook(
                    title=str(item.metadata.get("title", item.source)),
                    symptom=str(item.metadata.get("symptom", symptom)),
                    root_cause=str(item.metadata.get("root_cause", "")),
                    action=item.content,
                    tags=[tag for tag in str(item.metadata.get("tags", "")).split(",") if tag],
                    source=item.source,
                )
            )
        return runbooks

    async def delete_by_source(self, source: str) -> None:
        await asyncio.to_thread(self.collection.delete, where={"source": source})
