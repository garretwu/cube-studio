from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

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
from sre_agent.memory.config_memory import ConfigMemory
from sre_agent.memory.store import MemoryStore


def _alert() -> Alert:
    return Alert(
        alert_name="vllm_latency_high",
        severity=AlertSeverity.CRITICAL,
        labels={"instance": "vllm-0", "namespace": "infer"},
        annotations={"summary": "p95 high"},
        starts_at=datetime(2026, 3, 17, 12, 0, tzinfo=UTC),
        fingerprint="fp-1",
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


def _session() -> DiagnosisSession:
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
        session_id="sess-1",
        alert=_alert(),
        status="resolved",
        diagnosis_result=diagnosis,
        trace=ThinkingTrace(steps=[ThinkingStep(step=1, thought="inspect gpu", action_type="tool_call")]),
        duration_seconds=42,
        outcome="resolved",
    )


def test_memory_store_incidents_patterns_and_baseline() -> None:
    async def _run() -> None:
        store = MemoryStore(aidc_id="aidc-demo", db_dir=":memory:")
        await store.connect()
        incident_id = await store.record_incident(_session())
        assert incident_id

        incidents = await store.list_incidents(last=5)
        assert len(incidents) == 1
        similar = await store.search_similar({"instance": "vllm-0", "alert": "latency"}, top_k=3)
        assert similar

        patterns = await store.get_all_patterns()
        assert len(patterns) == 1
        assert patterns[0].effective_confidence(datetime.now(UTC)) <= patterns[0].confidence

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
        assert restored is not None
        assert restored.metric_baselines["vllm_p95_ms"] == 180

        archived = await store.archive_incidents_before(datetime.now(UTC) + timedelta(seconds=1))
        assert archived == 1
        assert await store.list_incidents() == []
        await store.close()

    asyncio.run(_run())
