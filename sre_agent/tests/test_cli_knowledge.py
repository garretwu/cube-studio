from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

from sre_agent.cli import main
from sre_agent.knowledge.store import KnowledgeStore


def test_knowledge_ingest_cli_persists_documents(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "roce.md").write_text(
        "# RoCEv2 ECN\n\nEnable ECN and PFC consistently on leaf-spine switches.",
        encoding="utf-8",
    )
    (docs_dir / "gpu.md").write_text(
        "# GPU contention\n\nKill gpu-burn when inference latency spikes persist.",
        encoding="utf-8",
    )

    persist_dir = tmp_path / "knowledge_db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "knowledge_base:",
                f"  persist_dir: {persist_dir}",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["knowledge", "ingest", "--config", str(config_path), "--path", str(docs_dir), "--category", "hardware"],
    )

    assert result.exit_code == 0
    assert "Knowledge Ingest Complete" in result.output
    assert "Files Ingested: 2" in result.output
    assert "Chunks Written: 2" in result.output
    assert "Skipped Files: 0" in result.output

    async def _verify() -> None:
        store = KnowledgeStore(persist_dir=str(persist_dir))
        results = await store.search("ECN switch RoCEv2", category="hardware", top_k=3)
        assert results
        assert any("RoCEv2" in item.content for item in results)

    asyncio.run(_verify())


def test_knowledge_search_cli_supports_mixed_language_query(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "roce.md").write_text(
        "# RoCEv2 ECN 配置\n\nRoCEv2 ECN 配置需要同时校验 PFC、队列阈值和拥塞遥测。",
        encoding="utf-8",
    )

    persist_dir = tmp_path / "knowledge_db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "knowledge_base:",
                f"  persist_dir: {persist_dir}",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    ingest = runner.invoke(
        main,
        ["knowledge", "ingest", "--config", str(config_path), "--path", str(docs_dir), "--category", "hardware"],
    )
    assert ingest.exit_code == 0

    search = runner.invoke(
        main,
        ["knowledge", "search", "--config", str(config_path), "--query", "RoCEv2 ECN 配置"],
    )

    assert search.exit_code == 0
    assert "Knowledge Search Results" in search.output
    assert "Query: RoCEv2 ECN 配置" in search.output
    assert "Count: 1" in search.output
    assert "RoCEv2 ECN 配置" in search.output


def test_knowledge_ingest_cli_replaces_existing_source_chunks(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    doc_path = docs_dir / "roce.md"
    doc_path.write_text("# RoCEv2\n\nfirst version", encoding="utf-8")

    persist_dir = tmp_path / "knowledge_db"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "knowledge_base:",
                f"  persist_dir: {persist_dir}",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    first = runner.invoke(
        main,
        ["knowledge", "ingest", "--config", str(config_path), "--path", str(docs_dir), "--category", "hardware"],
    )
    assert first.exit_code == 0

    doc_path.write_text("# RoCEv2\n\nsecond version", encoding="utf-8")
    second = runner.invoke(
        main,
        ["knowledge", "ingest", "--config", str(config_path), "--path", str(docs_dir), "--category", "hardware"],
    )
    assert second.exit_code == 0

    search = runner.invoke(
        main,
        ["knowledge", "search", "--config", str(config_path), "--query", "second version", "--top-k", "5"],
    )
    assert search.exit_code == 0
    assert "second version" in search.output
    assert "first version" not in search.output
