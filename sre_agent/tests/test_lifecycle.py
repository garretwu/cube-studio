from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sre_agent.lifecycle.data_lifecycle import DataLifecycleManager
from sre_agent.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    IncidentRecord,
    LearnedPattern,
)
from sre_agent.memory.store import MemoryStore


def _alert() -> Alert:
    return Alert(
        alert_name="vllm_latency_high",
        severity=AlertSeverity.CRITICAL,
        labels={"instance": "vllm-0"},
        annotations={"summary": "p95 high"},
        starts_at=datetime(2026, 3, 17, 12, 0, tzinfo=UTC),
        fingerprint="fp-lifecycle",
        status=AlertStatus.FIRING,
    )


def test_data_lifecycle_archives_vectors_and_inactivates_low_confidence_patterns() -> None:
    async def _run() -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()

        old_time = datetime.now(UTC) - timedelta(days=200)
        await store.save_incident(
            IncidentRecord(
                incident_id="inc-old-1",
                aidc_id="aidc-lifecycle",
                timestamp=old_time,
                alert=_alert(),
                symptoms=["latency", "gpu"],
                diagnosis_trace=[],
                root_cause="gpu contention",
                root_cause_layer="hardware",
                root_cause_entities=["gpu-0"],
                hypotheses_tested=[],
                remediation_applied=None,
                outcome="resolved",
                resolution_time_seconds=120,
                tags=[],
            )
        )
        learned = (await store.get_all_patterns())[0]
        await store.save_pattern(
            LearnedPattern(
                pattern_id=learned.pattern_id,
                aidc_id=learned.aidc_id,
                symptom_signature=learned.symptom_signature,
                root_cause=learned.root_cause,
                effective_fix="kill process",
                occurrence_count=4,
                confidence=0.2,
                first_seen=old_time,
                last_seen=old_time,
                example_incidents=["inc-old-1"],
            )
        )

        manager = DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365)
        summary = await manager.run_lifecycle()
        assert summary["archived_incidents"] == 1
        assert summary["inactive_patterns"] == 1

        patterns = await store.get_all_patterns()
        assert patterns[0].status == "inactive"
        assert await store.list_incidents() == []

        vector_records = await asyncio.to_thread(store.incidents_collection.get)
        assert vector_records["ids"] == []
        await store.close()

    asyncio.run(_run())
