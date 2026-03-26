from __future__ import annotations

import pytest

from sre_agent.models.alert import Alert
from sre_agent.scripts.alert_loki_test import (
    GPU_ALERT_NAME,
    TTFT_ALERT_NAME,
    SmokeTestSettings,
    build_bearer_token,
    build_phase1_record,
)


def _settings(tmp_path) -> SmokeTestSettings:
    return SmokeTestSettings(
        alertmanager_url="http://alertmanager",
        prometheus_url="http://prometheus",
        diagnose_url="http://api/api/diagnose",
        namespace="service",
        service="vllm-deepseek",
        node="gpu-1-1",
        pod="pod-1",
        ttft_query="ttft_query",
        gpu_query="gpu_query",
        alert_lookback="6h",
        poll_interval_seconds=15,
        poll_timeout_seconds=300,
        output_dir=tmp_path,
    )


def _alert(name: str, fingerprint: str) -> Alert:
    return Alert.model_validate(
        {
            "alert_name": name,
            "severity": "critical",
            "labels": {"alertname": name, "node": "gpu-1-1", "service": "vllm-deepseek"},
            "annotations": {"summary": name},
            "starts_at": "2026-03-24T08:00:00Z",
            "fingerprint": fingerprint,
            "status": "firing",
        }
    )


def test_build_bearer_token_prefers_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_DIAGNOSE_BEARER_TOKEN", "token-123")
    assert build_bearer_token() == "token-123"


def test_build_phase1_record_extracts_primary_fix(tmp_path) -> None:
    settings = _settings(tmp_path)
    record = build_phase1_record(
        settings=settings,
        baseline_metrics={"values": {"ttft_p99": {"value": 0.72}}},
        alert_evidence={TTFT_ALERT_NAME: {"rule_present": True}, GPU_ALERT_NAME: {"rule_present": True}},
        firing_alerts={
            TTFT_ALERT_NAME: _alert(TTFT_ALERT_NAME, "fp-ttft"),
            GPU_ALERT_NAME: _alert(GPU_ALERT_NAME, "fp-gpu"),
        },
        diagnose_response={
            "trace_id": "api-trace-1",
            "data": {
                "session_id": "session-1",
                "status": "diagnosed",
                "diagnosis_result": {
                    "root_cause": "GPU contention",
                    "confidence": 0.92,
                    "ranked_candidates": [
                        {
                            "rank": 1,
                            "root_cause": "GPU contention",
                            "confidence": 0.92,
                            "evidence_summary": "gpu-burn occupied GPU-0",
                            "recommended_fix": {"plan_id": "kill-gpu-burn"},
                        }
                    ],
                },
            },
        },
        trace_id="trace-1",
    )
    assert record["recommended_fix"] == {"plan_id": "kill-gpu-burn"}
    assert record["evidence_summary"] == "gpu-burn occupied GPU-0"
    assert record["expectations"]["status"] == "diagnosed"
