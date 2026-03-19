from __future__ import annotations

import asyncio
from pathlib import Path

from sre_agent.knowledge.ingest import KnowledgeIngestor
from sre_agent.knowledge.chunker import chunk_text
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


def test_chunk_text_defaults_use_documented_window() -> None:
    text = "A" * 700
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert len(chunks[0]) == 512
    assert len(chunks[1]) == 252


def test_knowledge_ingestor_markdown_and_runbook_path(tmp_path: Path) -> None:
    async def _run() -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)

        markdown_path = tmp_path / "guide.md"
        markdown_path.write_text(
            "# RoCEv2 ECN\n\nEnable ECN.\n\n## Validation\n\nCheck queue thresholds.",
            encoding="utf-8",
        )
        markdown_chunks = await ingestor.ingest_markdown(markdown_path, category="hardware")
        assert markdown_chunks == 2

        runbook_path = tmp_path / "latency.yaml"
        runbook_path.write_text(
            "\n".join(
                [
                    'name: "vLLM P95 延迟过高"',
                    "symptoms:",
                    '  - "vLLM P95 latency > 500ms"',
                    "fix_steps:",
                    '  - condition: "GPU 资源争用"',
                    '    action: "终止争用进程"',
                    'tags: ["vllm", "latency"]',
                ]
            ),
            encoding="utf-8",
        )
        runbook_id = await ingestor.ingest_runbook(runbook_path)
        assert runbook_id

        results = await store.search("queue thresholds", category="hardware", top_k=5)
        assert len(results) == 2
        assert {item.metadata.get("section") for item in results} == {"RoCEv2 ECN", "Validation"}

        runbooks = await store.search_runbooks("终止争用进程", top_k=5)
        assert runbooks
        assert runbooks[0].title == "vLLM P95 延迟过高"

    asyncio.run(_run())


def test_knowledge_ingestor_pdf_without_pdf_dependency_is_explicit(tmp_path: Path) -> None:
    async def _run() -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)
        pdf_path = tmp_path / "manual.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

        try:
            await ingestor.ingest_pdf(pdf_path, category="hardware")
        except RuntimeError as exc:
            assert "PDF ingestion requires pypdf or PyPDF2" in str(exc)
        else:  # pragma: no cover - exercised only if optional deps exist
            results = await store.search("manual", category="hardware", top_k=3)
            assert results

    asyncio.run(_run())
