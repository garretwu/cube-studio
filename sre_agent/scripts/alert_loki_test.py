#!/usr/bin/env python3
"""
Phase-1 smoke test for the GPU-contention diagnosis path.

Flow:
1. Read baseline TTFT / GPU metrics from Prometheus.
2. Confirm AIServiceTTFTP99High and GPUUtilizationHigh exist as rules or history.
3. Poll firing alerts until both are active.
4. Call /api/diagnose and save a compact execution record to disk.

Usage:
    python -m sre_agent.scripts.alert_loki_test
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.channels.alert import AlertChannel
from lib.channels.prometheus import PrometheusChannel
from lib.tests._real_backends import PrometheusHttpBackend
from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.models.alert import Alert


DEFAULT_ALERTMANAGER_URL = "http://10.11.4.3:31013/"
DEFAULT_PROMETHEUS_URL = "http://10.11.4.3:31260/"
DEFAULT_DIAGNOSE_URL = "http://127.0.0.1:8000/api/diagnose"
DEFAULT_OUTPUT_DIR = Path("data/test_records")

DEFAULT_NAMESPACE = "service"
DEFAULT_SERVICE = "vllm-deepseek"
DEFAULT_NODE = "gpu-1-1"
DEFAULT_POD = "qwen3-32b-fp8-202602261-6c55d577f5-czzxs"

TTFT_ALERT_NAME = "AIServiceTTFTP99High"
GPU_ALERT_NAME = "GPUUtilizationHigh"

DEFAULT_TTFT_QUERY = (
    'histogram_quantile(0.99, sum by(le, namespace, service, model_name) '
    '(rate(vllm:time_to_first_token_seconds_bucket{namespace="service",service="vllm-deepseek"}[5m])))'
)
DEFAULT_GPU_UTIL_QUERY = 'max(DCGM_FI_DEV_GPU_UTIL{instance=~".*"})'


@dataclass(frozen=True)
class SmokeTestSettings:
    alertmanager_url: str
    prometheus_url: str
    diagnose_url: str
    namespace: str
    service: str
    node: str
    pod: str
    ttft_query: str
    gpu_query: str
    alert_lookback: str
    poll_interval_seconds: int
    poll_timeout_seconds: int
    output_dir: Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def load_settings() -> SmokeTestSettings:
    return SmokeTestSettings(
        alertmanager_url=_env("SRE_ALERTMANAGER_URL", DEFAULT_ALERTMANAGER_URL),
        prometheus_url=_env("SRE_PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL),
        diagnose_url=_env("SRE_DIAGNOSE_URL", DEFAULT_DIAGNOSE_URL),
        namespace=_env("SRE_TEST_NAMESPACE", DEFAULT_NAMESPACE),
        service=_env("SRE_TEST_SERVICE", DEFAULT_SERVICE),
        node=_env("SRE_TEST_NODE", DEFAULT_NODE),
        pod=_env("SRE_TEST_POD", DEFAULT_POD),
        ttft_query=_env("SRE_TTFT_QUERY", DEFAULT_TTFT_QUERY),
        gpu_query=_env("SRE_GPU_UTIL_QUERY", DEFAULT_GPU_UTIL_QUERY),
        alert_lookback=_env("SRE_TEST_ALERT_LOOKBACK", "6h"),
        poll_interval_seconds=int(_env("SRE_ALERT_POLL_INTERVAL", "15")),
        poll_timeout_seconds=int(_env("SRE_ALERT_POLL_TIMEOUT", "900")),
        output_dir=Path(_env("SRE_PHASE1_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))),
    )


def print_separator(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f" {title}")
    print(f"{'=' * 72}")


def _serialize_alert(alert: Alert | None) -> dict[str, Any] | None:
    if alert is None:
        return None
    return alert.model_dump(mode="json")


def build_bearer_token() -> str:
    explicit = os.getenv("SRE_DIAGNOSE_BEARER_TOKEN", "").strip()
    if explicit:
        return explicit
    settings = resolve_jwt_settings()
    user = CurrentUser(
        user_id=_env("SRE_DIAGNOSE_USER_ID", "phase1-smoke"),
        username=_env("SRE_DIAGNOSE_USERNAME", "phase1-smoke"),
        role=_env("SRE_DIAGNOSE_ROLE", "operator"),  # type: ignore[arg-type]
    )
    return encode_token(user, settings)


def build_headers(trace_id: str) -> dict[str, str]:
    token = build_bearer_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "x-trace-id": trace_id,
    }


async def collect_baseline_metrics(settings: SmokeTestSettings) -> dict[str, Any]:
    print_separator("1. Baseline Metrics")
    prom = PrometheusChannel(base_url=settings.prometheus_url)
    baseline: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "namespace": settings.namespace,
        "service": settings.service,
        "node": settings.node,
        "pod": settings.pod,
        "queries": {
            "ttft_p99": settings.ttft_query,
            "gpu_utilization": settings.gpu_query,
        },
        "values": {},
    }
    for name, query in baseline["queries"].items():
        try:
            value = await prom.query_instant(str(query))
            baseline["values"][name] = {"value": value, "ok": True}
            print(f"{name}: {value}")
        except Exception as exc:  # noqa: BLE001
            baseline["values"][name] = {"value": None, "ok": False, "error": str(exc)}
            print(f"{name}: ERROR {exc}")
    await prom.close()
    return baseline


async def collect_alert_evidence(channel: AlertChannel, settings: SmokeTestSettings) -> dict[str, Any]:
    print_separator("2. Alert Definitions / History")
    rules = await channel.get_alert_rules()
    rule_names = {rule.name for rule in rules}
    evidence: dict[str, Any] = {}
    for alert_name in (TTFT_ALERT_NAME, GPU_ALERT_NAME):
        history = await channel.get_alert_history(alert_name, lookback=settings.alert_lookback)
        evidence[alert_name] = {
            "rule_present": alert_name in rule_names,
            "history_count": len(history),
            "history_sample": [_serialize_alert(item) for item in history[:3]],
        }
        print(
            f"{alert_name}: rule_present={evidence[alert_name]['rule_present']} "
            f"history_count={evidence[alert_name]['history_count']}"
        )
    return evidence


async def wait_for_dual_alerts(channel: AlertChannel, settings: SmokeTestSettings) -> dict[str, Alert]:
    print_separator("3. Poll Firing Alerts")
    deadline = asyncio.get_running_loop().time() + settings.poll_timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        firing = await channel.get_firing_alerts()
        matched: dict[str, Alert] = {}
        for alert in firing:
            if alert.alert_name in {TTFT_ALERT_NAME, GPU_ALERT_NAME}:
                matched[alert.alert_name] = alert
        print(
            f"firing alerts: total={len(firing)} "
            f"ttft={TTFT_ALERT_NAME in matched} gpu={GPU_ALERT_NAME in matched}"
        )
        if TTFT_ALERT_NAME in matched and GPU_ALERT_NAME in matched:
            return matched
        await asyncio.sleep(settings.poll_interval_seconds)
    raise TimeoutError(
        f"did not observe both {TTFT_ALERT_NAME} and {GPU_ALERT_NAME} within "
        f"{settings.poll_timeout_seconds}s"
    )


def build_diagnose_payload(
    settings: SmokeTestSettings,
    *,
    ttft_alert: Alert | None,
    gpu_alert: Alert | None,
) -> dict[str, Any]:
    source = ttft_alert or gpu_alert
    now = datetime.now(UTC).isoformat()
    labels: dict[str, str] = {
        "namespace": settings.namespace,
        "service": settings.service,
        "node": settings.node,
        "pod": settings.pod,
    }
    annotations: dict[str, str] = {
        "summary": "vLLM TTFT P99 latency high with correlated GPU utilization alert",
        "description": "Phase-1 diagnosis input carrying TTFT + GPU contention context.",
        "correlated_alerts": json.dumps([TTFT_ALERT_NAME, GPU_ALERT_NAME], ensure_ascii=False),
    }
    if source is not None:
        labels.update(source.labels)
        annotations.update(source.annotations)
    if gpu_alert is not None:
        labels.update({k: v for k, v in gpu_alert.labels.items() if k not in labels or not labels[k]})
        annotations["gpu_alert_summary"] = gpu_alert.summary or GPU_ALERT_NAME
    annotations["correlated_alerts"] = json.dumps([TTFT_ALERT_NAME, GPU_ALERT_NAME], ensure_ascii=False)
    return {
        "alert_name": TTFT_ALERT_NAME,
        "severity": "critical",
        "labels": labels,
        "annotations": annotations,
        "starts_at": source.starts_at.isoformat() if source is not None else now,
        "fingerprint": source.fingerprint if source is not None else f"{TTFT_ALERT_NAME}:{settings.node}",
        "status": "firing",
        "source": "phase1-smoke",
    }


async def call_diagnose_api(
    settings: SmokeTestSettings,
    *,
    payload: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    print_separator("4. Diagnose API")
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            settings.diagnose_url,
            json=payload,
            headers=build_headers(trace_id),
        )
        response.raise_for_status()
        body = response.json()
        print(f"diagnose status: {response.status_code}, success={body.get('success')}")
        return body


def build_phase1_record(
    *,
    settings: SmokeTestSettings,
    baseline_metrics: dict[str, Any],
    alert_evidence: dict[str, Any],
    firing_alerts: dict[str, Alert],
    diagnose_response: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    session = diagnose_response.get("data") if isinstance(diagnose_response.get("data"), dict) else {}
    diagnosis_result = session.get("diagnosis_result") if isinstance(session.get("diagnosis_result"), dict) else {}
    ranked = diagnosis_result.get("ranked_candidates")
    primary_candidate = ranked[0] if isinstance(ranked, list) and ranked else {}
    recommended_fix = primary_candidate.get("recommended_fix") or diagnosis_result.get("recommended_fix")
    evidence_summary = primary_candidate.get("evidence_summary") or diagnosis_result.get("impact_summary")
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "test_name": "gpu_contention_phase1_diagnosis",
        "trace_id": trace_id,
        "baseline_metrics": baseline_metrics,
        "alert_rule_check": alert_evidence,
        "firing_alerts": {name: _serialize_alert(alert) for name, alert in firing_alerts.items()},
        "diagnosis_result": diagnosis_result,
        "evidence_summary": evidence_summary,
        "recommended_fix": recommended_fix,
        "session_id": session.get("session_id"),
        "api_trace_id": diagnose_response.get("trace_id"),
        "diagnose_response": diagnose_response,
        "expectations": {
            "status": session.get("status"),
            "root_cause": diagnosis_result.get("root_cause"),
            "confidence": diagnosis_result.get("confidence"),
        },
        "context": {
            "namespace": settings.namespace,
            "service": settings.service,
            "node": settings.node,
            "pod": settings.pod,
            "diagnose_url": settings.diagnose_url,
        },
    }


def save_record(record: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"gpu-contention-phase1-{ts}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def main() -> None:
    settings = load_settings()
    trace_id = uuid4().hex
    print_separator("GPU Contention Phase-1 Smoke Test")
    print(f"time: {datetime.now(UTC).isoformat()}")
    print(f"prometheus: {settings.prometheus_url}")
    print(f"diagnose api: {settings.diagnose_url}")
    print(f"trace id: {trace_id}")

    prometheus_backend = PrometheusHttpBackend(
        base_url=settings.prometheus_url,
        timeout=30.0,
        retries=3,
    )
    alert_channel = AlertChannel(
        alertmanager_url=settings.alertmanager_url,
        prometheus_url=settings.prometheus_url,
        metrics_backend=prometheus_backend,
    )

    await alert_channel.connect()
    try:
        baseline_metrics = await collect_baseline_metrics(settings)
        alert_evidence = await collect_alert_evidence(alert_channel, settings)
        firing_alerts = await wait_for_dual_alerts(alert_channel, settings)
        diagnose_payload = build_diagnose_payload(
            settings,
            ttft_alert=firing_alerts.get(TTFT_ALERT_NAME),
            gpu_alert=firing_alerts.get(GPU_ALERT_NAME),
        )
        diagnose_response = await call_diagnose_api(
            settings,
            payload=diagnose_payload,
            trace_id=trace_id,
        )
        record = build_phase1_record(
            settings=settings,
            baseline_metrics=baseline_metrics,
            alert_evidence=alert_evidence,
            firing_alerts=firing_alerts,
            diagnose_response=diagnose_response,
            trace_id=trace_id,
        )
        path = save_record(record, settings.output_dir)
        print_separator("Done")
        print(f"record saved to: {path}")
    finally:
        await alert_channel.disconnect()
        await prometheus_backend.aclose()


if __name__ == "__main__":
    asyncio.run(main())
