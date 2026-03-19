from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sre_agent.lifecycle.data_lifecycle import DataLifecycleManager
from sre_agent.memory.store import MemoryStore
from sre_agent.models import Alert, AlertSeverity, AlertStatus, IncidentRecord, LearnedPattern


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


async def _seed_old_incident_and_pattern(store: MemoryStore, *, old_time: datetime) -> LearnedPattern:
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
    stale_pattern = LearnedPattern(
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
    await store.save_pattern(stale_pattern)
    return stale_pattern


class TestLifecycleUnit:
    @pytest.mark.asyncio
    async def test_unit_marks_pattern_inactive_when_effective_confidence_below_threshold(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()
        old_time = datetime.now(UTC) - timedelta(days=200)
        await _seed_old_incident_and_pattern(store, old_time=old_time)

        summary = await DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365).run_lifecycle()
        patterns = await store.get_all_patterns()

        assert summary["inactive_patterns"] == 1
        assert patterns[0].status == "inactive"

        await store.close()

    @pytest.mark.asyncio
    async def test_unit_sets_storage_warning_flag_when_usage_exceeds_ratio(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()
        manager = DataLifecycleManager(store, warning_threshold_ratio=0.8)

        async def _fake_usage() -> object:
            return type("Usage", (), {"total_mb": 9.0, "quota_mb": 10.0})()

        store.get_storage_usage = _fake_usage  # type: ignore[method-assign]
        summary = await manager.run_lifecycle()

        assert summary["storage_warning"] == 1

        await store.close()


class TestLifecycleIntegration:
    @pytest.mark.asyncio
    async def test_integration_archives_incidents_and_cleans_vectors_when_retention_cutoff_hit(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()
        old_time = datetime.now(UTC) - timedelta(days=200)
        await _seed_old_incident_and_pattern(store, old_time=old_time)

        summary = await DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365).run_lifecycle()
        vector_records = await asyncio.to_thread(store.incidents_collection.get)

        assert summary["archived_incidents"] == 1
        assert await store.list_incidents() == []
        assert vector_records["ids"] == []

        await store.close()

    @pytest.mark.asyncio
    async def test_integration_preserves_active_patterns_when_effective_confidence_still_high(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()
        recent_time = datetime.now(UTC) - timedelta(days=1)
        pattern = LearnedPattern(
            pattern_id="pattern-recent",
            aidc_id="aidc-lifecycle",
            symptom_signature=["latency", "gpu"],
            root_cause="gpu contention",
            effective_fix="kill process",
            occurrence_count=4,
            confidence=0.8,
            first_seen=recent_time,
            last_seen=recent_time,
            example_incidents=["inc-recent"],
        )
        await store.save_pattern(pattern)

        summary = await DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365).run_lifecycle()
        patterns = await store.get_all_patterns()

        assert summary["inactive_patterns"] == 0
        assert patterns[0].status == "active"

        await store.close()


class TestLifecycleE2E:
    @pytest.mark.asyncio
    async def test_e2e_returns_lifecycle_summary_when_old_incident_and_pattern_exist(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()
        await _seed_old_incident_and_pattern(store, old_time=datetime.now(UTC) - timedelta(days=200))

        summary = await DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365).run_lifecycle()

        assert summary["archived_incidents"] == 1
        assert summary["inactive_patterns"] == 1
        assert "storage_total_mb" in summary
        assert "storage_quota_mb" in summary

        await store.close()

    @pytest.mark.asyncio
    async def test_e2e_returns_empty_changes_when_nothing_expires(self) -> None:
        store = MemoryStore(aidc_id="aidc-lifecycle", db_dir=":memory:")
        await store.connect()

        summary = await DataLifecycleManager(store, hot_retention_days=90, warm_retention_days=365).run_lifecycle()

        assert summary["archived_incidents"] == 0
        assert summary["inactive_patterns"] == 0

        await store.close()
