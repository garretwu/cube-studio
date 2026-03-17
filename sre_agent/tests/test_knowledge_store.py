from __future__ import annotations

import asyncio

from sre_agent.knowledge.ingest import KnowledgeIngestor
from sre_agent.knowledge.runbook import Runbook
from sre_agent.knowledge.store import KnowledgeStore


def test_knowledge_store_ingest_and_search() -> None:
    async def _run() -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)
        ids = await ingestor.ingest_text(
            content="GPU contention causes inference latency spikes. Kill gpu-burn to recover.",
            source="kb-1",
            category="troubleshooting",
        )
        assert ids
        results = await store.search("gpu contention latency", category="troubleshooting", top_k=3)
        assert results
        assert results[0].source == "kb-1"

    asyncio.run(_run())


def test_knowledge_store_runbook_lookup() -> None:
    async def _run() -> None:
        store = KnowledgeStore(persist_dir=None)
        runbook = Runbook(
            title="Fix GPU contention",
            symptom="latency spike",
            root_cause="gpu contention",
            action="kill rogue gpu-burn process",
            tags=["gpu", "latency"],
            source="rb-1",
        )
        await store.add_runbook(runbook)
        matches = await store.search_runbooks("latency spike gpu", top_k=3)
        assert matches
        assert matches[0].title == "Fix GPU contention"

    asyncio.run(_run())
