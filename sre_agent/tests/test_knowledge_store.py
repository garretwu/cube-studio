from __future__ import annotations

from pathlib import Path

import pytest

from sre_agent.knowledge.chunker import chunk_text
from sre_agent.knowledge.ingest import KnowledgeIngestor
from sre_agent.knowledge.runbook import Runbook
from sre_agent.knowledge.store import KnowledgeStore


class TestKnowledgeStoreUnit:
    def test_unit_uses_documented_chunk_window_when_defaults_applied(self) -> None:
        chunks = chunk_text("A" * 700)

        assert len(chunks) == 2
        assert len(chunks[0]) == 512
        assert len(chunks[1]) == 252

    @pytest.mark.asyncio
    async def test_unit_splits_markdown_and_parses_runbook_when_supported_inputs(self, tmp_path: Path) -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)

        markdown_path = tmp_path / "guide.md"
        markdown_path.write_text(
            "# RoCEv2 ECN\n\nEnable ECN.\n\n## Validation\n\nCheck queue thresholds.",
            encoding="utf-8",
        )
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

        markdown_chunks = await ingestor.ingest_markdown(markdown_path, category="hardware")
        runbook_id = await ingestor.ingest_runbook(runbook_path)
        sections = await store.search("queue thresholds", category="hardware", top_k=5)
        runbooks = await store.search_runbooks("终止争用进程", top_k=5)

        assert markdown_chunks == 2
        assert runbook_id
        assert {item.metadata.get("section") for item in sections} == {"RoCEv2 ECN", "Validation"}
        assert runbooks[0].title == "vLLM P95 延迟过高"


class TestKnowledgeStoreIntegration:
    @pytest.mark.asyncio
    async def test_integration_ingests_and_searches_documents_when_store_chain_complete(self) -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)

        ids = await ingestor.ingest_text(
            content="GPU contention causes inference latency spikes. Kill gpu-burn to recover.",
            source="kb-1",
            category="troubleshooting",
        )
        results = await store.search("gpu contention latency", category="troubleshooting", top_k=3)

        assert ids
        assert results
        assert results[0].source == "kb-1"

    @pytest.mark.asyncio
    async def test_integration_adds_and_retrieves_runbooks_when_contract_matches(self) -> None:
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


class TestKnowledgeStoreE2E:
    @pytest.mark.asyncio
    async def test_e2e_ingests_directory_and_searches_when_mixed_documents_present(self, tmp_path: Path) -> None:
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir()
        (docs_dir / "guide.md").write_text("# Fabric\n\nRoCEv2 ECN queue thresholds.", encoding="utf-8")
        (docs_dir / "notes.txt").write_text("GPU contention remediation checklist.", encoding="utf-8")
        (docs_dir / "empty.txt").write_text("   \n", encoding="utf-8")

        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)

        summary = await ingestor.ingest_path(path=docs_dir, category="hardware")
        results = await store.search("queue thresholds", category="hardware", top_k=5)

        assert summary.file_count == 2
        assert summary.skipped_files == 1
        assert summary.chunk_count >= 2
        assert results

    @pytest.mark.asyncio
    async def test_e2e_handles_pdf_dependency_gap_when_optional_parser_missing(self, tmp_path: Path) -> None:
        store = KnowledgeStore(persist_dir=None)
        ingestor = KnowledgeIngestor(store)
        pdf_path = tmp_path / "manual.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")

        try:
            chunk_count = await ingestor.ingest_pdf(pdf_path, category="hardware")
        except RuntimeError as exc:
            assert "PDF ingestion requires pypdf or PyPDF2" in str(exc)
        else:  # pragma: no cover - exercised only if optional deps exist
            results = await store.search("manual", category="hardware", top_k=3)
            assert chunk_count >= 1
            assert results
