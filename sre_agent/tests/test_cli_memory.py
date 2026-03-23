from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner

from sre_agent.cli import main
from sre_agent.memory.store import MemoryStore
from sre_agent.models import Alert, AlertSeverity, AlertStatus, IncidentRecord


def _alert(name: str, fingerprint: str) -> Alert:
    return Alert(
        alert_name=name,
        severity=AlertSeverity.CRITICAL,
        labels={"instance": "svc-a"},
        annotations={"summary": "latency high"},
        starts_at=datetime(2026, 3, 18, 9, 0, tzinfo=UTC),
        fingerprint=fingerprint,
        status=AlertStatus.FIRING,
    )


def test_memory_incidents_cli_reads_recent_history(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "memory:",
                f"  db_dir: {memory_dir}",
            ]
        ),
        encoding="utf-8",
    )

    async def _seed() -> None:
        store = MemoryStore(aidc_id="test-aidc", db_dir=str(memory_dir))
        await store.connect()
        await store.save_incident(
            IncidentRecord(
                incident_id="inc-older",
                aidc_id="test-aidc",
                timestamp=datetime(2026, 3, 18, 9, 30, tzinfo=UTC),
                alert=_alert("gpu_temp_high", "fp-older"),
                symptoms=["gpu_temp_high", "instance"],
                diagnosis_trace=[],
                root_cause="thermal throttling",
                root_cause_layer="hardware",
                root_cause_entities=["gpu-0"],
                hypotheses_tested=[],
                remediation_applied=None,
                outcome="resolved",
                resolution_time_seconds=180,
                tags=["hardware"],
            )
        )
        await store.save_incident(
            IncidentRecord(
                incident_id="inc-newer",
                aidc_id="test-aidc",
                timestamp=datetime(2026, 3, 18, 10, 0, tzinfo=UTC),
                alert=_alert("vllm_latency_high", "fp-newer"),
                symptoms=["instance", "vllm_latency_high"],
                diagnosis_trace=[],
                root_cause="gpu contention",
                root_cause_layer="hardware",
                root_cause_entities=["gpu-1"],
                hypotheses_tested=[],
                remediation_applied=None,
                outcome="failed",
                resolution_time_seconds=300,
                tags=["latency"],
            )
        )
        await store.close()

    asyncio.run(_seed())

    runner = CliRunner()
    result = runner.invoke(main, ["memory", "incidents", "--config", str(config_path), "--last", "1"])

    assert result.exit_code == 0
    assert "Incident History" in result.output
    assert "AIDC: test-aidc" in result.output
    assert "Last: 1" in result.output
    assert "Count: 1" in result.output
    assert "inc-newer" in result.output
    assert "vllm_latency_high" in result.output
    assert "gpu contention" in result.output
    assert "inc-older" not in result.output


def test_memory_incidents_cli_handles_empty_history(tmp_path: Path) -> None:
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
    result = runner.invoke(main, ["memory", "incidents", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Incident History" in result.output
    assert "Count: 0" in result.output
    assert "- none" in result.output
