from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from sre_agent.cli import main
from sre_agent.memory.store import MemoryStore
from sre_agent.models import Alert, AlertSeverity, AlertStatus, IncidentRecord


def _alert(fingerprint: str) -> Alert:
    return Alert(
        alert_name="vllm_latency_high",
        severity=AlertSeverity.CRITICAL,
        labels={"instance": "vllm-0", "namespace": "infer"},
        annotations={"summary": "p95 high"},
        starts_at=datetime(2026, 3, 18, 9, 0, tzinfo=UTC),
        fingerprint=fingerprint,
        status=AlertStatus.FIRING,
    )


def test_memory_patterns_cli_reads_known_patterns(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "memory:",
                f"  db_dir: {memory_dir}",
                "  pattern_min_occurrences: 3",
                "  pattern_min_confidence: 0.7",
            ]
        ),
        encoding="utf-8",
    )

    async def _seed() -> None:
        store = MemoryStore(aidc_id="test-aidc", db_dir=str(memory_dir))
        await store.connect()
        try:
            for index in range(4):
                await store.save_incident(
                    IncidentRecord(
                        incident_id=f"inc-{index}",
                        aidc_id="test-aidc",
                        timestamp=datetime(2026, 3, 18, 10, index, tzinfo=UTC),
                        alert=_alert(f"fp-{index}"),
                        symptoms=["instance", "namespace", "vllm_latency_high"],
                        diagnosis_trace=[],
                        root_cause="gpu contention",
                        root_cause_layer="hardware",
                        root_cause_entities=["gpu-1"],
                        hypotheses_tested=[],
                        remediation_applied=None,
                        outcome="resolved",
                        resolution_time_seconds=120,
                        tags=["demo"],
                    )
                )
        finally:
            await store.close()

    asyncio.run(_seed())

    runner = CliRunner()
    result = runner.invoke(main, ["memory", "patterns", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Learned Patterns" in result.output
    assert "AIDC: test-aidc" in result.output
    assert "Min Occurrences: 3" in result.output
    assert "Min Confidence: 0.7" in result.output
    assert "Count: 1" in result.output
    assert "gpu contention" in result.output
    assert "vllm_latency_high" in result.output
    assert "occurrences=4" in result.output
    assert "confidence=0.74" in result.output


def test_memory_patterns_cli_handles_empty_known_patterns(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: empty-aidc",
                "memory:",
                f"  db_dir: {tmp_path / 'memory'}",
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["memory", "patterns", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Learned Patterns" in result.output
    assert "Count: 0" in result.output
    assert "- none" in result.output
