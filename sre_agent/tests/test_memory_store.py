from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sre_agent.memory.config_memory import ConfigMemory
from sre_agent.memory.store import MemoryStore
from sre_agent.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    ConfigBaseline,
    DiagnosisResult,
    DiagnosisSession,
    Hypothesis,
    RemediationAction,
    RemediationPlan,
    ThinkingTrace,
    ThinkingStep,
    VerificationConfig,
)
from sre_agent.models.memory import LearnedPattern


def _alert(instance: str = "vllm-0") -> Alert:
    return Alert(
        alert_name="vllm_latency_high",
        severity=AlertSeverity.CRITICAL,
        labels={"instance": instance, "namespace": "infer"},
        annotations={"summary": "p95 high"},
        starts_at=datetime(2026, 3, 17, 12, 0, tzinfo=UTC),
        fingerprint=f"fp-{instance}",
        status=AlertStatus.FIRING,
    )


def _plan() -> RemediationPlan:
    return RemediationPlan(
        plan_id="plan-1",
        root_cause="gpu contention",
        description="kill rogue process",
        steps=[
            RemediationAction(
                step_id=1,
                description="kill gpu-burn",
                tool="kill_process",
                params={"node": "node-a", "pid": 1},
                verification=VerificationConfig(method="wait", wait_seconds=1),
            )
        ],
        estimated_impact="low",
        confidence=0.9,
        priority="P1",
    )


def _session(session_id: str = "sess-1", instance: str = "vllm-0", outcome: str = "resolved") -> DiagnosisSession:
    diagnosis = DiagnosisResult(
        root_cause="gpu contention",
        root_cause_layer="hardware",
        root_cause_entities=["gpu-1"],
        confidence=0.9,
        hypotheses=[Hypothesis(description="gpu contention", status="confirmed", confidence=0.9)],
        impact_summary="latency spike",
        affected_services=["vllm"],
        recommended_fix=_plan(),
        triage_priority="P1",
        ranked_candidates=[],
        diagnosis_certainty="confirmed",
    )
    return DiagnosisSession(
        session_id=session_id,
        alert=_alert(instance=instance),
        status=outcome,
        diagnosis_result=diagnosis,
        trace=ThinkingTrace(steps=[ThinkingStep(step=1, thought="inspect gpu", action_type="tool_call")]),
        duration_seconds=42,
        outcome=outcome,
    )


class TestMemoryStoreUnit:
    def test_unit_decays_pattern_confidence_when_time_elapses(self) -> None:
        pattern = LearnedPattern(
            pattern_id="pattern-1",
            aidc_id="aidc-demo",
            symptom_signature=["latency", "gpu"],
            root_cause="gpu contention",
            effective_fix="kill gpu-burn",
            occurrence_count=4,
            confidence=0.8,
            first_seen=datetime(2026, 1, 1, tzinfo=UTC),
            last_seen=datetime(2026, 1, 1, tzinfo=UTC),
            example_incidents=["inc-1"],
        )

        effective = pattern.effective_confidence(datetime(2026, 4, 1, tzinfo=UTC))

        assert effective < pattern.confidence
        assert effective > 0.0

    @pytest.mark.asyncio
    async def test_unit_filters_known_patterns_and_handles_missing_baseline_when_thresholds_applied(self) -> None:
        store = MemoryStore(aidc_id="aidc-demo", db_dir=":memory:")
        await store.connect()
        old_pattern = LearnedPattern(
            pattern_id="pattern-old",
            aidc_id="aidc-demo",
            symptom_signature=["latency", "gpu"],
            root_cause="gpu contention",
            effective_fix="kill gpu-burn",
            occurrence_count=4,
            confidence=0.9,
            first_seen=datetime(2025, 1, 1, tzinfo=UTC),
            last_seen=datetime(2025, 1, 1, tzinfo=UTC),
            example_incidents=["inc-old"],
        )
        fresh_pattern = old_pattern.model_copy(
            update={
                "pattern_id": "pattern-fresh",
                "last_seen": datetime.now(UTC),
                "first_seen": datetime(2026, 3, 1, tzinfo=UTC),
            }
        )
        await store.save_pattern(old_pattern)
        await store.save_pattern(fresh_pattern)

        patterns = await store.get_known_patterns(min_occurrence=3, min_effective_confidence=0.7)

        assert [pattern.pattern_id for pattern in patterns] == ["pattern-fresh"]
        assert await store.get_config_baseline() is None

        await store.close()


class TestMemoryStoreIntegration:
    @pytest.mark.asyncio
    async def test_integration_records_searches_updates_patterns_and_baseline_when_memory_chain_runs(self) -> None:
        store = MemoryStore(aidc_id="aidc-demo", db_dir=":memory:")
        await store.connect()

        incident_id = await store.record_incident(_session())
        incidents = await store.list_incidents(last=5)
        similar = await store.search_similar({"instance": "vllm-0", "alert": "latency"}, top_k=3)
        patterns = await store.get_all_patterns()

        baseline = ConfigBaseline(
            aidc_id="aidc-demo",
            version=1,
            metric_baselines={"vllm_p95_ms": 180},
            safety_thresholds={"vllm_p95_warn": 500},
            custom_rules={"night_window": "00:00-06:00"},
        )
        cfg = ConfigMemory(store)
        await cfg.save(baseline)
        restored = await cfg.get()

        assert incident_id
        assert len(incidents) == 1
        assert similar
        assert len(patterns) == 1
        assert patterns[0].effective_confidence(datetime.now(UTC)) <= patterns[0].confidence
        assert restored is not None
        assert restored.metric_baselines["vllm_p95_ms"] == 180

        await store.close()

    @pytest.mark.asyncio
    async def test_integration_archives_old_incidents_and_cleans_vectors_when_cutoff_reached(self) -> None:
        store = MemoryStore(aidc_id="aidc-demo", db_dir=":memory:")
        await store.connect()
        await store.record_incident(_session())

        archived = await store.archive_incidents_before(datetime.now(UTC) + timedelta(seconds=1))
        vectors = await asyncio.to_thread(store.incidents_collection.get)

        assert archived == 1
        assert await store.list_incidents() == []
        assert vectors["ids"] == []

        await store.close()


class TestMemoryStoreE2E:
    @pytest.mark.asyncio
    async def test_e2e_runs_incident_history_flow_when_session_recorded_and_archived(self) -> None:
        store = MemoryStore(aidc_id="aidc-demo", db_dir=":memory:")
        await store.connect()

        incident_id = await store.record_incident(_session(session_id="sess-e2e"))
        listed = await store.list_recent(last=10)
        fetched = await store.get_incident(incident_id)
        similar = await store.search_similar(["instance", "vllm_latency_high"], top_k=3)
        archived = await store.archive_incidents_before(datetime.now(UTC) + timedelta(seconds=1))

        assert incident_id
        assert listed[0].incident_id == incident_id
        assert fetched is not None
        assert similar and similar[0].incident_id == incident_id
        assert archived == 1
        assert await store.list_recent(last=10) == []

        await store.close()

    @pytest.mark.asyncio
    async def test_e2e_returns_empty_results_when_store_has_no_matching_history(self) -> None:
        store = MemoryStore(aidc_id="aidc-empty", db_dir=":memory:")
        await store.connect()

        assert await store.list_recent(last=10) == []
        assert await store.search_similar("unknown symptom", top_k=3) == []
        assert await store.get_incident("missing-incident") is None

        await store.close()
