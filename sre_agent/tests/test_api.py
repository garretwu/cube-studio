from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sre_agent.alerts_filter import DEFAULT_BLOCKED_ALERT_NAMES
from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, Observation, RankedRootCause, ThinkingStep, ThinkingTrace
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.models.remediation import RemediationPlan, RemediationResult, RemediationStep, VerificationConfig
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.server import create_app
from sre_agent.tools import SafetyLevel, ToolDefinition, ToolExecutionContext, ToolRegistry


class _FakeDiagnosisRunner:
    async def adiagnose(self, alert: Alert) -> DiagnosisSession:
        plan = RemediationPlan(
            plan_id=f"plan-{alert.fingerprint}",
            root_cause="gpu contention",
            description="delete pod",
            estimated_impact="minor",
            confidence=0.9,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="delete pod",
                    tool="k8s.delete_pod",
                    params={"namespace": "infer", "pod_name": "vllm-0"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
                RemediationStep(
                    step_id=2,
                    description="delete canary pod",
                    tool="k8s.delete_pod",
                    params={"namespace": "infer", "pod_name": "vllm-canary-0"},
                    verification=VerificationConfig(method="wait", wait_seconds=1),
                ),
            ],
        )
        diagnosis = DiagnosisResult(
            root_cause="gpu contention",
            root_cause_layer="service",
            confidence=0.9,
            impact_summary="latency spike",
            triage_priority="P1",
            diagnosis_certainty="confirmed",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause="gpu contention",
                    root_cause_layer="service",
                    confidence=0.9,
                    evidence_summary="high util",
                    recommended_fix=plan,
                )
            ],
        )
        return DiagnosisSession(session_id=alert.fingerprint, alert=alert, status="diagnosed", diagnosis_result=diagnosis)


class _CountingDiagnosisRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def adiagnose(self, alert: Alert, trace_callback=None) -> DiagnosisSession:  # noqa: ANN001
        _ = trace_callback
        self.calls.append(alert.fingerprint)
        return DiagnosisSession(
            session_id=f"session-{len(self.calls)}",
            alert=alert,
            status="diagnosed",
            diagnosis_result=None,
            trace=None,
        )


class _FlakyDiagnosisRunner:
    def __init__(self) -> None:
        self.calls = 0

    async def adiagnose(self, alert: Alert, trace_callback=None) -> DiagnosisSession:  # noqa: ANN001
        _ = trace_callback
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated diagnosis failure")
        return DiagnosisSession(
            session_id=f"session-{self.calls}",
            alert=alert,
            status="diagnosed",
            diagnosis_result=None,
            trace=None,
        )


class _FakeStreamingDiagnosisRunner:
    def __init__(self) -> None:
        self.callback_count = 0

    async def adiagnose(self, alert: Alert, trace_callback=None) -> DiagnosisSession:  # noqa: ANN001
        if trace_callback is not None:
            await trace_callback(
                {
                    "type": EventType.TOOL_CALL.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "step": 1,
                        "timestamp": "2026-03-18T12:00:00Z",
                        "thought": "collect gpu metrics",
                        "action_type": "tool_call",
                        "tool_name": "gpu.get_metrics",
                        "tool_params": {"node": "node-a"},
                    },
                }
            )
            self.callback_count += 1
            await trace_callback(
                {
                    "type": EventType.TOOL_RESULT.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "tool": "gpu.get_metrics",
                        "params": {"node": "node-a"},
                        "result": {"success": True, "data": {"utilization": 97}},
                        "timestamp": "2026-03-18T12:00:01Z",
                    },
                }
            )
            self.callback_count += 1
            await trace_callback(
                {
                    "type": EventType.THINKING_STEP.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "step": 2,
                        "timestamp": "2026-03-18T12:00:02Z",
                        "thought": "high GPU utilization correlates with vLLM workload",
                        "action_type": "conclude",
                        "confidence": 0.9,
                    },
                }
            )
            self.callback_count += 1
            await trace_callback(
                {
                    "type": EventType.DIAGNOSIS_RESULT.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "root_cause": "gpu contention",
                        "root_cause_layer": "service",
                        "root_cause_entities": ["node-a"],
                        "confidence": 0.9,
                        "hypotheses": [],
                        "propagation_chain": [],
                        "impact_summary": "latency spike",
                        "affected_services": ["vllm"],
                        "recommended_fix": None,
                        "triage_priority": "P1",
                        "ranked_candidates": [
                            {
                                "rank": 1,
                                "root_cause": "gpu contention",
                                "root_cause_layer": "service",
                                "root_cause_entities": ["node-a"],
                                "confidence": 0.9,
                                "evidence_summary": "high util",
                                "recommended_fix": None,
                                "distinguishing_verification": None,
                            }
                        ],
                        "diagnosis_certainty": "confirmed",
                    },
                }
            )
            self.callback_count += 1
        diagnosis = DiagnosisResult(
            root_cause="gpu contention",
            root_cause_layer="service",
            confidence=0.9,
            impact_summary="latency spike",
            triage_priority="P1",
            diagnosis_certainty="confirmed",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause="gpu contention",
                    root_cause_layer="service",
                    confidence=0.9,
                    evidence_summary="high util",
                )
            ],
        )
        trace = ThinkingTrace(
            steps=[
                ThinkingStep(
                    step=1,
                    thought="collect gpu metrics",
                    action_type="tool_call",
                    tool_name="gpu.get_metrics",
                    tool_params={"node": "node-a"},
                ),
                Observation(
                    tool="gpu.get_metrics",
                    params={"node": "node-a"},
                    result={"success": True, "data": {"utilization": 97}},
                ),
                ThinkingStep(
                    step=2,
                    thought="high GPU utilization correlates with vLLM workload",
                    action_type="conclude",
                    confidence=0.9,
                ),
            ]
        )
        return DiagnosisSession(
            session_id=alert.fingerprint,
            alert=alert,
            status="diagnosed",
            diagnosis_result=diagnosis,
            trace=trace,
        )


class _FakePartialStreamingDiagnosisRunner:
    def __init__(self) -> None:
        self.callback_count = 0

    async def adiagnose(self, alert: Alert, trace_callback=None) -> DiagnosisSession:  # noqa: ANN001
        if trace_callback is not None:
            await trace_callback(
                {
                    "type": EventType.TOOL_CALL.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "step": 1,
                        "timestamp": "2026-03-18T12:00:00Z",
                        "thought": "collect gpu metrics",
                        "action_type": "tool_call",
                        "tool_name": "gpu.get_metrics",
                        "tool_params": {"node": "node-a"},
                    },
                }
            )
            self.callback_count += 1
            await trace_callback(
                {
                    "type": EventType.TOOL_RESULT.value,
                    "session_id": alert.fingerprint,
                    "data": {
                        "tool": "gpu.get_metrics",
                        "params": {"node": "node-a"},
                        "result": {"success": True, "data": {"utilization": 97}},
                        "timestamp": "2026-03-18T12:00:01Z",
                    },
                }
            )
            self.callback_count += 1

        plan = RemediationPlan(
            plan_id=f"plan-{alert.fingerprint}",
            root_cause="gpu contention",
            description="terminate abnormal benchmark process",
            estimated_impact="minor",
            confidence=0.91,
            priority="P1",
            steps=[
                RemediationStep(
                    step_id=1,
                    description="terminate gpu-burn",
                    tool="shell_command",
                    params={"command": "pkill -f gpu-burn"},
                    verification=VerificationConfig(method="wait", wait_seconds=2),
                )
            ],
        )
        diagnosis = DiagnosisResult(
            root_cause="gpu contention",
            root_cause_layer="service",
            confidence=0.91,
            impact_summary="latency spike",
            triage_priority="P1",
            diagnosis_certainty="confirmed",
            ranked_candidates=[
                RankedRootCause(
                    rank=1,
                    root_cause="gpu contention",
                    root_cause_layer="service",
                    confidence=0.91,
                    evidence_summary="high util",
                    recommended_fix=plan,
                )
            ],
        )
        trace = ThinkingTrace(
            steps=[
                ThinkingStep(
                    step=1,
                    thought="collect gpu metrics",
                    action_type="tool_call",
                    tool_name="gpu.get_metrics",
                    tool_params={"node": "node-a"},
                ),
                Observation(
                    tool="gpu.get_metrics",
                    params={"node": "node-a"},
                    result={"success": True, "data": {"utilization": 97}},
                ),
            ]
        )
        return DiagnosisSession(
            session_id=alert.fingerprint,
            alert=alert,
            status="diagnosed",
            diagnosis_result=diagnosis,
            trace=trace,
        )


class _FakeKnowledge:
    def __init__(self) -> None:
        self.last_documents_dataset_id: str | None = None
        self.last_document_detail_dataset_id: str | None = None
        self.last_segments_dataset_id: str | None = None
        self.last_search_category: str | None = None

    async def list_datasets(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        _ = (args, kwargs)
        return [
            {
                "id": "dataset-default",
                "name": "SRE Dataset",
                "description": "dataset for test",
                "document_count": 1,
                "word_count": 1234,
                "status": "ready",
            },
            {
                "id": "dataset-network",
                "name": "Network Dataset",
                "description": "network specific docs",
                "document_count": 1,
                "word_count": 800,
                "status": "ready",
            },
        ]

    async def search(self, query: str, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        self.last_search_category = category
        return [
            {
                "id": "seg-search-1",
                "query": query,
                "category": category or "all",
                "top_k": top_k,
                "content": f"match for {query}",
                "source": "kb://runbooks/roce",
                "score": 0.88,
                "document_id": "doc-1",
            }
        ]

    async def list_documents(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        _ = kwargs
        if args:
            self.last_documents_dataset_id = str(args[0])
        else:
            self.last_documents_dataset_id = None
        return [
            {
                "id": "doc-1",
                "title": "RoCEv2 Troubleshooting Guide",
                "source": "kb://runbooks/roce",
                "category": "network",
                "excerpt": "ECN and PFC checks for packet loss bursts.",
                "tags": ["roce", "network"],
                "score": 0.92,
            }
        ]

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        return {
            "id": dataset_id,
            "name": "SRE Dataset",
            "description": "dataset for test",
            "document_count": 1,
            "word_count": 1234,
            "status": "ready",
        }

    async def get_document(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        self.last_document_detail_dataset_id = dataset_id
        return {
            "id": document_id,
            "title": "RoCEv2 Troubleshooting Guide",
            "source": "kb://runbooks/roce",
            "category": "network",
            "excerpt": "ECN and PFC checks for packet loss bursts.",
            "tags": ["roce", "network"],
            "score": 0.92,
        }

    async def list_document_segments(
        self,
        dataset_id: str,
        document_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = (page, limit, keyword)
        self.last_segments_dataset_id = dataset_id
        return [
            {
                "id": "seg-1",
                "document_id": document_id,
                "content": "Check ECN and PFC settings first.",
                "status": "enabled",
                "score": 0.9,
            }
        ]


class _FakeChatHandler:
    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}

    async def __call__(self, message: str) -> str:
        return await self.chat(message, user_id="anonymous")

    async def chat(self, message: str, *, user_id: str = "anonymous", session_id: str | None = None) -> str:
        key = user_id or "anonymous"
        items = self._history.setdefault(key, [])
        now = datetime.now(UTC).isoformat()
        metadata = {"session_id": session_id} if session_id else None
        items.append(
            {
                "id": f"user-{len(items) + 1}",
                "role": "user",
                "content": message,
                "created_at": now,
                "metadata": metadata,
            }
        )
        reply = f"echo:{message}"
        items.append(
            {
                "id": f"assistant-{len(items) + 1}",
                "role": "assistant",
                "content": reply,
                "created_at": now,
                "metadata": metadata,
            }
        )
        return reply

    async def chat_stream(self, message: str, *, user_id: str = "anonymous", session_id: str | None = None):  # noqa: ANN201
        reply = await self.chat(message, user_id=user_id, session_id=session_id)
        midpoint = max(1, len(reply) // 2)
        yield reply[:midpoint]
        yield reply[midpoint:]

    async def get_history(self, *, user_id: str = "anonymous", session_id: str | None = None) -> list[dict[str, Any]]:
        history = list(self._history.get(user_id or "anonymous", []))
        if not session_id:
            return history
        return [item for item in history if item.get("metadata", {}).get("session_id") == session_id]


class _FakeMemory:
    async def list_recent(self, last: int = 10):  # noqa: ANN201
        return []

    async def get_known_patterns(self):  # noqa: ANN201
        return []

    async def get_config_baseline(self, aidc_id: str | None = None) -> dict[str, Any]:
        return {
            "aidc_id": aidc_id or "aidc-demo",
            "version": 2,
            "metric_baselines": {"latency_p95_ms": 35},
            "safety_thresholds": {"latency_p95_ms": 60},
            "custom_rules": {"network_guard": "enabled"},
            "updated_at": "2026-03-18T12:00:00Z",
        }


class _FakeAlertChannel:
    def __init__(self, alerts: list[Alert]) -> None:
        self._alerts = alerts
        self.connected = False

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> bool:
        self.connected = False
        return True

    async def get_firing_alerts(self, filter_labels: dict[str, str] | None = None) -> list[Alert]:
        _ = filter_labels
        return list(self._alerts)


class _MutableAlertChannel:
    def __init__(self, alerts: list[Alert]) -> None:
        self._alerts = list(alerts)
        self.connected = False

    def set_alerts(self, alerts: list[Alert]) -> None:
        self._alerts = list(alerts)

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> bool:
        self.connected = False
        return True

    async def get_firing_alerts(self, filter_labels: dict[str, str] | None = None) -> list[Alert]:
        _ = filter_labels
        return list(self._alerts)


class _FakeOntologyNoRefresh:
    def find_entities(self, entity_type: str | None = None, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [
            {
                "id": "node-no-refresh",
                "entity_type": entity_type or "node",
                "filters": filters or {},
            }
        ]

    def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        return {
            "root_entity_id": entity_id,
            "affected_count": 0,
            "affected_entities": [],
        }

    def get_path(self, from_id: str, to_id: str) -> list[str]:
        return [from_id, "switch:compat", to_id]


class _FakePrometheus:
    def __init__(self, *, before: float = 900.0, after: float = 100.0, switch_after_calls: int = 3) -> None:
        self.before = before
        self.after = after
        self.switch_after_calls = max(0, switch_after_calls)
        self.calls = 0

    async def query_instant(self, promql: str) -> float:
        _ = promql
        self.calls += 1
        if self.calls <= self.switch_after_calls:
            return self.before
        return self.after


def _registry() -> tuple[ToolRegistry, ToolExecutionContext]:
    registry = ToolRegistry()

    async def _delete_pod(params: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        _ = context
        return {"deleted": params["pod_name"]}

    registry.register(
        ToolDefinition(
            name="k8s.delete_pod",
            description="delete pod",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["namespace", "pod_name"]},
            needs_approval=True,
        ),
        _delete_pod,
    )
    context = ToolExecutionContext(write_approved=True)
    return registry, context


def _alert_payload() -> dict[str, Any]:
    return {
        "alert_name": "vllm_latency_high",
        "severity": "critical",
        "labels": {"instance": "vllm-0", "node": "node-a"},
        "annotations": {},
        "starts_at": "2026-03-18T12:00:00Z",
        "fingerprint": "fp-api-1",
        "status": "firing",
    }


def _blocked_alert_payload() -> dict[str, Any]:
    return {
        "alert_name": "GPUUtilizationHigh",
        "severity": "warning",
        "labels": {"alertname": "GPU utilization is high", "instance": "gpu-operator-0"},
        "annotations": {"summary": "temporarily blocked in test stage"},
        "starts_at": "2026-03-18T12:00:00Z",
        "fingerprint": "fp-api-blocked-1",
        "status": "firing",
    }


def _kube_client_errors_alert_payload(*, fingerprint: str = "fp-kube-client-errors-1") -> dict[str, Any]:
    return {
        "alert_name": "KubeClientErrors",
        "severity": "warning",
        "labels": {"alertname": "KubeClientErrors", "instance": "apiserver-0", "node": "node-a"},
        "annotations": {"summary": "kube client request errors are high"},
        "starts_at": "2026-03-18T12:00:00Z",
        "fingerprint": fingerprint,
        "status": "firing",
    }


def _demo_blocked_alert_payload(
    *,
    alert_name: str = "TargetDown",
    fingerprint: str = "fp-target-down-1",
) -> dict[str, Any]:
    return {
        "alert_name": alert_name,
        "severity": "warning",
        "labels": {"alertname": alert_name, "instance": "monitoring-0", "node": "node-a"},
        "annotations": {"summary": f"{alert_name} is firing in demo"},
        "starts_at": "2026-03-18T12:00:00Z",
        "fingerprint": fingerprint,
        "status": "firing",
    }


def _cube_web_latency_alert_payload(*, fingerprint: str = "fp-cube-latency-1") -> dict[str, Any]:
    return {
        "alert_name": "CubeStudioWebLatencyP95High",
        "severity": "critical",
        "labels": {"instance": "cube-studio-web-0", "node": "node-a", "alertname": "CubeStudioWebLatencyP95High"},
        "annotations": {"summary": "web latency p95 is high"},
        "starts_at": "2026-03-18T12:00:00Z",
        "fingerprint": fingerprint,
        "status": "firing",
    }


@pytest.fixture(autouse=True)
def _ensure_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")


def _build_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("JWT_SECRET", "secret")
    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    graph = OntologyGraph()
    asyncio.run(graph.connect())
    asyncio.run(
        graph.add_entity(
            OntologyNode(
                id="node-a",
                entity_type=EntityType.NODE,
                name="node-a",
                properties={"zone": "az-1"},
                updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
            )
        )
    )
    registry, context = _registry()
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "aidc-demo"},
            "remediation": {"execution_mode": "mock", "observation_seconds": 0},
        }
    )
    app = create_app(
        config=config,
        diagnosis_runner=_FakeDiagnosisRunner(),
        ontology=graph,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tool_registry=registry,
        execution_context=context,
        chat_handler=_FakeChatHandler(),
    )
    return TestClient(app), token


def _build_real_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    prometheus: Any | None = None,
) -> tuple[TestClient, str]:
    monkeypatch.setenv("JWT_SECRET", "secret")
    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    graph = OntologyGraph()
    asyncio.run(graph.connect())
    asyncio.run(
        graph.add_entity(
            OntologyNode(
                id="node-a",
                entity_type=EntityType.NODE,
                name="node-a",
                properties={"zone": "az-1"},
                updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
            )
        )
    )
    registry, context = _registry()
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "aidc-demo"},
            "remediation": {"execution_mode": "real", "observation_seconds": 0},
        }
    )
    app = create_app(
        config=config,
        diagnosis_runner=_FakeDiagnosisRunner(),
        ontology=graph,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        prometheus=prometheus,
        tool_registry=registry,
        execution_context=context,
        chat_handler=_FakeChatHandler(),
    )
    return TestClient(app), token


def _set_tool_channel_health(
    client: TestClient,
    channel_name: str,
    *,
    health: str,
    mode: str = "test",
    last_error: str | None = None,
) -> None:
    services = client.app.state.services
    existing = dict(services.tool_channel_status.get(channel_name, {}))
    existing.update(
        {
            "name": channel_name,
            "health": health,
            "required_by_tools": existing.get("required_by_tools", []),
            "enabled": True,
            "mode": mode,
            "last_error": last_error,
            "last_checked_at": datetime.now(UTC).isoformat(),
        }
    )
    services.tool_channel_status[channel_name] = existing


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAPIUnit:
    def test_unit_default_blocked_alert_names_include_demo_noise_alerts(self) -> None:
        config = SREAgentConfig.model_validate({})

        assert config.global_.blocked_alert_names == list(DEFAULT_BLOCKED_ALERT_NAMES)

    def test_unit_default_remediation_execution_mode_is_real(self) -> None:
        config = SREAgentConfig.model_validate({})

        assert config.remediation.execution_mode == "real"

    def test_unit_returns_401_when_bearer_missing_on_protected_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)
        response = client.get("/api/memory/incidents")

        assert response.status_code == 401

    def test_unit_returns_401_when_bearer_missing_on_topology_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)

        response = client.get("/api/topology")

        assert response.status_code == 401

    def test_unit_returns_401_when_bearer_missing_on_tool_channel_status_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)

        response = client.get("/api/tools/channels/status")

        assert response.status_code == 401

    def test_unit_cors_preflight_returns_allow_headers_for_topology_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)

        response = client.options(
            "/api/topology",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,x-trace-id",
            },
        )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
        allow_headers = response.headers.get("access-control-allow-headers", "")
        assert "authorization" in allow_headers.lower()
        assert "x-trace-id" in allow_headers.lower()


class TestAPIIntegration:
    def test_integration_runs_diagnose_route_when_runner_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["session_id"] == "fp-api-1"

    def test_integration_diagnose_start_returns_diagnosing_then_advances(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose/start", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["status"] == "diagnosing"
        session_id = payload["data"]["session_id"]
        assert session_id

        finalized: dict[str, Any] | None = None
        for _ in range(30):
            current = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert current.status_code == 200
            data = current.json()["data"]
            if data["status"] != "diagnosing":
                finalized = data
                break
            time.sleep(0.05)

        assert finalized is not None
        assert finalized["status"] in {"diagnosed", "approval_required", "failed"}

    def test_integration_diagnose_start_emits_approval_required_when_plan_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose/start", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        session_id = response.json()["data"]["session_id"]

        found = False
        for _ in range(30):
            events = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
            assert events.status_code == 200
            event_types = [item["type"] for item in events.json()["data"]]
            if EventType.APPROVAL_REQUIRED.value in event_types:
                found = True
                break
            time.sleep(0.05)
        assert found is True

    def test_integration_diagnose_start_backfills_diagnosis_result_when_live_stream_misses_tail_event(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc"},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
                "loop_orchestrator": {"enable_re_diagnosis": False},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakePartialStreamingDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            start = client.post("/api/diagnose/start", json=_alert_payload(), headers=_auth_headers(token))
            assert start.status_code == 200
            session_id = start.json()["data"]["session_id"]
            assert session_id

            found_diagnosis_result = False
            for _ in range(40):
                events = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
                assert events.status_code == 200
                payload = events.json()
                if any(item["type"] == EventType.DIAGNOSIS_RESULT.value for item in payload["data"]):
                    found_diagnosis_result = True
                    break
                time.sleep(0.05)

            assert found_diagnosis_result is True
            session_response = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_response.status_code == 200
            session_payload = session_response.json()
            assert session_payload["success"] is True
            assert session_payload["data"]["status"] == "approval_required"
            assert session_payload["data"]["diagnosis_result"]["ranked_candidates"][0]["recommended_fix"]["steps"]

    def test_integration_diagnose_emits_approval_required_event_when_plan_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token))
        assert response.status_code == 200
        session_id = response.json()["data"]["session_id"]

        events = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
        assert events.status_code == 200
        payload = events.json()
        assert payload["success"] is True
        assert any(item["type"] == EventType.APPROVAL_REQUIRED.value for item in payload["data"])

    def test_integration_runs_handle_route_when_full_pipeline_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["outcome"] in {"resolved", "escalated", "re_diagnosed", "partially_resolved"}

    def test_integration_tool_channel_status_route_returns_runtime_channel_matrix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/tools/channels/status", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["runtime_mode"] in {"degraded", "strict"}
        channels = payload["data"]["channels"]
        assert isinstance(channels, list)
        names = {item["name"] for item in channels}
        assert "ontology" in names
        assert "k8s" in names
        assert "ssh" in names

    def test_integration_handle_duplicate_returns_existing_session_id_in_error_details(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_client(monkeypatch)
        first = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))
        assert first.status_code == 200
        duplicate = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))

        assert duplicate.status_code == 200
        payload = duplicate.json()
        assert payload["success"] is True
        assert payload["error"]["code"] == ErrorCode.ALERT_DUPLICATE.value
        assert payload["error"]["details"]["session_id"] == "fp-api-1"

    def test_integration_get_sessions_returns_recent_first_and_honors_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        first_payload = _alert_payload()
        second_payload = _alert_payload() | {"fingerprint": "fp-api-2"}

        first = client.post("/api/diagnose", json=first_payload, headers=_auth_headers(token))
        assert first.status_code == 200
        time.sleep(0.01)
        second = client.post("/api/diagnose", json=second_payload, headers=_auth_headers(token))
        assert second.status_code == 200

        response = client.get("/api/sessions", params={"limit": 1}, headers=_auth_headers(token))
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert len(payload["data"]) == 1
        assert payload["data"][0]["session_id"] == "fp-api-2"
        assert payload["data"][0]["alert_name"] == "vllm_latency_high"
        assert payload["data"][0]["fingerprint"] == "fp-api-2"

    def test_integration_chat_history_returns_user_scoped_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        first = client.post("/api/chat", json={"content": "hello"}, headers=_auth_headers(token))
        assert first.status_code == 200
        assert first.json()["success"] is True

        response = client.get("/api/chat/history", headers=_auth_headers(token))
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert len(payload["data"]) >= 2
        assert payload["data"][0]["role"] == "user"
        assert payload["data"][1]["role"] == "assistant"
        assert payload["data"][1]["content"] == "echo:hello"

    def test_integration_chat_history_supports_session_scope(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        alert_a = _alert_payload()
        alert_a["fingerprint"] = "sess-a"
        alert_b = _alert_payload()
        alert_b["fingerprint"] = "sess-b"
        diagnose_a = client.post("/api/diagnose", json=alert_a, headers=_auth_headers(token))
        diagnose_b = client.post("/api/diagnose", json=alert_b, headers=_auth_headers(token))
        assert diagnose_a.status_code == 200
        assert diagnose_b.status_code == 200

        first = client.post(
            "/api/chat",
            json={"content": "hello", "session_id": "sess-a"},
            headers=_auth_headers(token),
        )
        second = client.post(
            "/api/chat",
            json={"content": "world", "session_id": "sess-b"},
            headers=_auth_headers(token),
        )
        assert first.status_code == 200
        assert second.status_code == 200

        response = client.get(
            "/api/chat/history",
            params={"session_id": "sess-b"},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert len(payload["data"]) == 2
        assert payload["data"][0]["content"] == "world"
        assert payload["data"][0]["metadata"]["session_id"] == "sess-b"
        assert payload["data"][1]["display"]["answer"] == "echo:world"

    def test_integration_chat_response_meta_reports_session_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token))
        assert diagnose.status_code == 200
        session_id = diagnose.json()["data"]["session_id"]

        response = client.post(
            "/api/chat",
            json={"content": "why this root cause?", "session_id": session_id},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["reply"] == "echo:why this root cause?"
        assert payload["data"]["meta"]["context_applied"] is True
        assert payload["data"]["meta"]["session_id"] == session_id
        assert payload["data"]["meta"]["trace_steps_used"] >= 0
        assert payload["data"]["display"]["answer"] == "echo:why this root cause?"
        assert payload["data"]["display"]["has_thinking"] is False

    def test_integration_chat_returns_404_when_session_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post(
            "/api/chat",
            json={"content": "hello", "session_id": "missing-session"},
            headers=_auth_headers(token),
        )

        assert response.status_code == 404
        assert "diagnosis session not found" in response.json().get("detail", "")

    def test_integration_runtime_llm_status_returns_masked_health_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/runtime/llm/status", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["api_key_configured"] is True
        assert payload["data"]["api_key_length"] > 0
        assert payload["data"]["api_key_source"] in {"SRE_OPENAI_API_KEY", "OPENAI_API_KEY"}

    def test_integration_get_session_trace_returns_serialized_steps(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc"},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
                "loop_orchestrator": {"enable_re_diagnosis": False},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeStreamingDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
                chat_handler=_FakeChatHandler(),
            )
        ) as client:
            handle_response = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))
            assert handle_response.status_code == 200
            assert handle_response.json()["success"] is True
            trace_response = client.get("/api/sessions/fp-api-1/trace", headers=_auth_headers(token))

        assert trace_response.status_code == 200
        payload = trace_response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert any("thought" in step for step in payload["data"])

    def test_integration_get_ontology_route_returns_entities_when_graph_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/ontology", params={"entity_type": "node"}, headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"][0]["id"] == "node-a"

    def test_integration_get_topology_route_returns_nodes_and_edges_when_graph_seeded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        graph = client.app.state.services.ontology
        asyncio.run(
            graph.add_entity(
                OntologyNode(
                    id="service:vllm",
                    entity_type=EntityType.INFERENCE_SERVICE,
                    name="vllm",
                    properties={"namespace": "default"},
                    updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
                )
            )
        )
        asyncio.run(
            graph.add_edge(
                OntologyEdge(
                    source_id="service:vllm",
                    target_id="node-a",
                    relation=RelationType.DEPENDS_ON,
                    properties={},
                )
            )
        )

        response = client.get("/api/topology", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["active_alerts"] == 0
        assert payload["data"]["recent_events"] == []
        assert payload["data"]["sync_state"] == "idle"
        assert {item["id"] for item in payload["data"]["nodes"]} == {"node-a", "service:vllm"}
        assert payload["data"]["edges"] == [
            {
                "source_id": "service:vllm",
                "target_id": "node-a",
                "relation": "depends_on",
                "properties": {},
            }
        ]

    def test_integration_get_topology_status_returns_status_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/topology/status", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["sync_state"] in {"idle", "syncing", "ready", "degraded", "error"}
        assert "scanner_counts" in payload["data"]

    def test_integration_get_topology_explorer_returns_mapped_topology_view(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        graph = client.app.state.services.ontology
        asyncio.run(
            graph.add_nodes(
                [
                    OntologyNode(
                        id="node-a",
                        entity_type=EntityType.NODE,
                        name="node-a",
                        properties={"source": "lab_seed"},
                        updated_at=datetime(2026, 4, 8, 12, 0, tzinfo=UTC),
                    ),
                    OntologyNode(
                        id="svc:team-a:demo",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="demo",
                        properties={"namespace": "team-a", "cluster_id": "k8s:lab-cluster"},
                        updated_at=datetime(2026, 4, 8, 12, 1, tzinfo=UTC),
                    ),
                    OntologyNode(
                        id="pod:team-a:demo-0",
                        entity_type=EntityType.K8S_POD,
                        name="demo-0",
                        properties={"namespace": "team-a", "cluster_id": "k8s:lab-cluster", "phase": "Running"},
                        status="Running",
                        updated_at=datetime(2026, 4, 8, 12, 2, tzinfo=UTC),
                    ),
                ]
            )
        )
        asyncio.run(
            graph.add_edges(
                [
                    OntologyEdge(
                        source_id="svc:team-a:demo",
                        target_id="pod:team-a:demo-0",
                        relation=RelationType.SERVES,
                        properties={"namespace": "team-a"},
                    ),
                    OntologyEdge(
                        source_id="pod:team-a:demo-0",
                        target_id="node-a",
                        relation=RelationType.HOSTED_ON,
                        properties={"namespace": "team-a"},
                    ),
                ]
            )
        )

        response = client.get("/api/topology-explorer", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["site"]["id"] == "aidc-site"
        assert payload["data"]["paths"] == []
        nodes = {item["id"]: item for item in payload["data"]["nodes"]}
        assert nodes["svc:team-a:demo"]["name"] == "team-a/demo"
        assert nodes["svc:team-a:demo"]["type"] == "service"
        assert nodes["pod:team-a:demo-0"]["name"] == "team-a/demo-0"
        assert nodes["pod:team-a:demo-0"]["metrics"]["phase"] == "Running"
        assert "namespace=team-a" in nodes["pod:team-a:demo-0"]["summary"]
        edges = {item["id"]: item for item in payload["data"]["edges"]}
        assert len(edges) == 2
        assert {item["relationType"] for item in edges.values()} == {"depends_on", "runs_on"}

    def test_integration_post_topology_discover_runs_manual_sync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/topology/discover", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["sync_state"] in {"ready", "degraded"}

    def test_integration_get_alerts_snapshot_returns_recorded_alerts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        create_response = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))
        assert create_response.status_code == 200

        response = client.get("/api/alerts", headers=_auth_headers(token))
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["alerts"][0]["fingerprint"] == "fp-api-1"
        assert isinstance(payload["data"]["clusters"], list)

    def test_integration_diagnose_rejects_filtered_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post("/api/diagnose", json=_kube_client_errors_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert "temporarily filtered" in payload["error"]["message"]

        sessions = client.get("/api/sessions", headers=_auth_headers(token)).json()["data"]
        assert all(item["fingerprint"] != "fp-kube-client-errors-1" for item in sessions)

    def test_integration_diagnose_start_rejects_filtered_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post("/api/diagnose/start", json=_kube_client_errors_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert "temporarily filtered" in payload["error"]["message"]

        sessions = client.get("/api/sessions", headers=_auth_headers(token)).json()["data"]
        assert all(item["fingerprint"] != "fp-kube-client-errors-1" for item in sessions)

    def test_integration_handle_rejects_filtered_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post("/api/handle", json=_kube_client_errors_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert "temporarily filtered" in payload["error"]["message"]

        sessions = client.get("/api/sessions", headers=_auth_headers(token)).json()["data"]
        assert all(item["fingerprint"] != "fp-kube-client-errors-1" for item in sessions)

    def test_integration_diagnose_start_rejects_default_demo_blocked_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post("/api/diagnose/start", json=_demo_blocked_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert "temporarily filtered" in payload["error"]["message"]

        sessions = client.get("/api/sessions", headers=_auth_headers(token)).json()["data"]
        assert all(item["fingerprint"] != "fp-target-down-1" for item in sessions)

    def test_integration_handle_rejects_default_demo_blocked_alert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post("/api/handle", json=_demo_blocked_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
        assert "temporarily filtered" in payload["error"]["message"]

        sessions = client.get("/api/sessions", headers=_auth_headers(token)).json()["data"]
        assert all(item["fingerprint"] != "fp-target-down-1" for item in sessions)

    def test_integration_get_knowledge_documents_returns_document_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/documents", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert payload["data"][0]["id"] == "doc-1"
        assert payload["data"][0]["title"] == "RoCEv2 Troubleshooting Guide"
        assert payload["data"][0]["tags"] == ["roce", "network"]

    def test_integration_get_knowledge_dataset_returns_dataset_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/dataset", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["name"] == "SRE Dataset"
        assert payload["data"]["status"] == "ready"

    def test_integration_get_knowledge_datasets_returns_dataset_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/datasets", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert payload["data"][0]["id"] == "dataset-default"

    def test_integration_get_knowledge_bases_returns_summary_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/bases", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert payload["data"][0]["id"] == "dataset-default"
        assert payload["data"][0]["scope"] in {"shared", "private"}
        assert payload["data"][0]["status"] in {"enabled", "disabled"}
        assert payload["data"][0]["index_status"] in {"ready", "indexing", "failed", "pending"}
        assert "code" in payload["data"][0]

    def test_integration_get_knowledge_base_detail_returns_preview_documents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        knowledge = client.app.state.services.knowledge

        response = client.get("/api/knowledge/bases/dataset-network", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["id"] == "dataset-network"
        assert payload["data"]["knowledge_base_id"] == "dataset-network"
        assert isinstance(payload["data"]["documents"], list)
        assert payload["data"]["documents"][0]["id"] == "doc-1"
        assert payload["data"]["documents"][0]["source_type"] in {"file", "manual", "link"}
        assert "preview" in payload["data"]["documents"][0]
        assert payload["data"]["documents"][0]["preview"]["sections"]
        assert knowledge.last_documents_dataset_id == "dataset-network"

    def test_integration_get_knowledge_base_detail_returns_404_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/bases/not-exist", headers=_auth_headers(token))

        assert response.status_code == 404
        payload = response.json()
        assert "knowledge base not found" in str(payload.get("detail", "")).lower()

    def test_integration_knowledge_routes_forward_dataset_id_query(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        knowledge = client.app.state.services.knowledge

        docs_response = client.get(
            "/api/knowledge/documents",
            params={"dataset_id": "dataset-network"},
            headers=_auth_headers(token),
        )
        detail_response = client.get(
            "/api/knowledge/documents/doc-1",
            params={"dataset_id": "dataset-network"},
            headers=_auth_headers(token),
        )
        segments_response = client.get(
            "/api/knowledge/documents/doc-1/segments",
            params={"dataset_id": "dataset-network"},
            headers=_auth_headers(token),
        )
        search_response = client.get(
            "/api/knowledge/segments/search",
            params={"query": "roce", "dataset_id": "dataset-network"},
            headers=_auth_headers(token),
        )

        assert docs_response.status_code == 200
        assert detail_response.status_code == 200
        assert segments_response.status_code == 200
        assert search_response.status_code == 200
        assert knowledge.last_documents_dataset_id == "dataset-network"
        assert knowledge.last_document_detail_dataset_id == "dataset-network"
        assert knowledge.last_segments_dataset_id == "dataset-network"
        assert knowledge.last_search_category == "dataset-network"

    def test_integration_get_knowledge_document_detail_returns_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/documents/doc-1", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["id"] == "doc-1"
        assert payload["data"]["title"] == "RoCEv2 Troubleshooting Guide"

    def test_integration_get_knowledge_document_segments_returns_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/knowledge/documents/doc-1/segments", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert payload["data"][0]["id"] == "seg-1"
        assert payload["data"][0]["document_id"] == "doc-1"

    def test_integration_get_knowledge_segments_search_returns_hits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get(
            "/api/knowledge/segments/search",
            params={"query": "roce", "top_k": 3},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert payload["data"][0]["id"] == "seg-search-1"

    def test_integration_get_memory_baseline_returns_enveloped_baseline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/memory/baseline", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["aidc_id"] == "aidc-demo"
        assert payload["data"]["version"] == 2
        assert payload["data"]["metric_baselines"]["latency_p95_ms"] == 35

    def test_integration_get_skills_returns_enveloped_skill_descriptors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.get("/api/skills", headers=_auth_headers(token))

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert isinstance(payload["data"], list)
        assert len(payload["data"]) >= 1
        assert {"id", "name", "scope", "summary", "source", "permissions", "match_score"} <= set(payload["data"][0].keys())

    def test_integration_get_skills_with_origin_returns_cors_header(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        headers = _auth_headers(token) | {"Origin": "http://127.0.0.1:5173"}

        response = client.get("/api/skills", headers=headers)

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"

    def test_integration_post_ontology_query_returns_filtered_entities(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        response = client.post(
            "/api/ontology/query",
            json={"entity_type": "node", "filters": {"zone": "az-1"}},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"][0]["properties"]["zone"] == "az-1"

    def test_integration_get_and_post_ontology_path_return_same_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        get_response = client.get(
            "/api/ontology/path",
            params={"from_id": "node-a", "to_id": "node-a"},
            headers=_auth_headers(token),
        )
        post_response = client.post(
            "/api/ontology/path",
            json={"from_id": "node-a", "to_id": "node-a"},
            headers=_auth_headers(token),
        )

        assert get_response.status_code == 200
        assert post_response.status_code == 200
        assert get_response.json()["data"] == ["node-a"]
        assert post_response.json()["data"] == ["node-a"]

    def test_integration_get_and_post_blast_radius_return_same_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)

        get_response = client.get("/api/ontology/node-a/blast-radius", headers=_auth_headers(token))
        post_response = client.post(
            "/api/ontology/blast",
            json={"entity_id": "node-a"},
            headers=_auth_headers(token),
        )

        assert get_response.status_code == 200
        assert post_response.status_code == 200
        assert get_response.json()["data"]["root_entity_id"] == "node-a"
        assert get_response.json()["data"] == post_response.json()["data"]
        assert "entities" in get_response.json()["data"]

    def test_integration_refresh_fallback_returns_non_error_payload_when_not_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        app = create_app(
            diagnosis_runner=_FakeDiagnosisRunner(),
            ontology=_FakeOntologyNoRefresh(),
            memory=_FakeMemory(),
            knowledge=_FakeKnowledge(),
            tool_registry=registry,
            execution_context=context,
        )
        client = TestClient(app)

        response = client.post(
            "/api/ontology/refresh",
            json={"entity_id": "node-no-refresh"},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["data"]["entity_id"] == "node-no-refresh"
        assert payload["data"]["refreshed"] is False
        assert "not implemented" in payload["data"]["reason"]


class TestAPIE2E:
    def test_e2e_approve_route_executes_registered_plan_when_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]

        response = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_e2e_approve_route_rejects_when_session_not_waiting_for_approval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]

        first = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )
        assert first.status_code == 200
        assert first.json()["success"] is True

        second = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )
        assert second.status_code == 200
        payload = second.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    def test_e2e_approve_route_allows_single_success_when_concurrent_requests_arrive(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]

        def _approve() -> dict[str, Any]:
            response = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert response.status_code == 200
            return response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            payloads = list(executor.map(lambda _: _approve(), range(2)))

        success_count = sum(1 for payload in payloads if payload["success"] is True)
        failure_payloads = [payload for payload in payloads if payload["success"] is False]
        assert success_count == 1
        assert len(failure_payloads) == 1
        assert failure_payloads[0]["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    def test_e2e_approve_route_returns_plan_invalid_when_plan_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        alert = Alert.model_validate(_alert_payload())
        session = DiagnosisSession(session_id="missing-plan-session", alert=alert, status="approval_required", diagnosis_result=None)
        client.app.state.services.session_store.put(session)

        response = client.post(
            "/api/remediate/missing-plan-session/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.REMEDIATION_PLAN_INVALID.value

    def test_e2e_approve_route_rejected_flow_updates_status_and_returns_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]

        response = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": False, "user": "alice", "reason": "manual reject"},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.REMEDIATION_APPROVAL_DENIED.value

        get_session = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token)).json()
        assert get_session["data"]["status"] == "rejected"

    def test_e2e_approve_route_emits_remediation_progress_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]

        response = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        events_response = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
        assert events_response.status_code == 200
        events_payload = events_response.json()
        assert events_payload["success"] is True
        remediation_events = [
            item for item in events_payload["data"] if item["type"] == EventType.REMEDIATION_PROGRESS.value
        ]
        assert len(remediation_events) >= 5
        assert remediation_events[0]["data"]["stage"] == "approval_accepted"
        assert remediation_events[1]["data"]["stage"] == "execution_started"
        assert remediation_events[2]["data"]["stage"] == "pre_remediation_baseline_collected"
        assert remediation_events[2]["data"]["baseline_alert"] is not None
        assert isinstance(remediation_events[2]["data"]["baseline_metrics"], list)
        assert remediation_events[-2]["data"]["stage"] == "observation_result"
        assert "alert_review" in remediation_events[-2]["data"]
        assert "metric_reviews" in remediation_events[-2]["data"]
        assert remediation_events[-1]["data"]["stage"] == "execution_succeeded"
        assert isinstance(remediation_events[-1]["data"].get("step_results"), list)
        assert remediation_events[-1]["data"]["step_results"]
        assert remediation_events[-1]["data"]["step_results"][0]["command"].startswith("mock::")

    def test_e2e_approve_route_real_mode_rejects_when_required_channels_not_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_real_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            payload = approve.json()
            assert payload["success"] is False
            assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
            assert "required channels not ready" in payload["error"]["message"]
            details = payload["error"]["details"]
            assert details["execution_mode"] == "real"
            assert {item["name"] for item in details["unready_channels"]} >= {"k8s", "prometheus"}

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            assert session_resp.json()["data"]["status"] == "approval_required"
        finally:
            client.close()

    def test_e2e_approve_route_real_mode_requires_ssh_for_network_clear_tc_qdisc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_real_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            services = client.app.state.services
            session = services.session_store.get(session_id)
            assert session is not None
            assert session.diagnosis_result is not None
            assert session.diagnosis_result.recommended_fix is not None
            replacement_plan = session.diagnosis_result.recommended_fix.model_copy(
                update={
                    "steps": [
                        RemediationStep(
                            step_id=1,
                            description="clear netem qdisc",
                            tool="network.clear_tc_qdisc",
                            params={"node": "worker-03", "iface": "roce", "parent": "8016:10"},
                            verification=VerificationConfig(method="wait", wait_seconds=1),
                        )
                    ]
                }
            )
            updated_diagnosis = session.diagnosis_result.model_copy(update={"recommended_fix": replacement_plan})
            services.session_store.put(session.model_copy(update={"diagnosis_result": updated_diagnosis}))
            services.remediation_engine.register_plan(session_id, replacement_plan)

            _set_tool_channel_health(client, "prometheus", health="ready")
            _set_tool_channel_health(client, "ssh", health="unavailable", last_error="ssh channel offline")

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            payload = approve.json()
            assert payload["success"] is False
            assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
            details = payload["error"]["details"]
            assert details["execution_mode"] == "real"
            assert "ssh" in details["required_channels"]
            assert {item["name"] for item in details["unready_channels"]} >= {"ssh"}
        finally:
            client.close()

    def test_e2e_approve_route_real_mode_blocks_placeholder_iface_before_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_real_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            services = client.app.state.services
            session = services.session_store.get(session_id)
            assert session is not None
            assert session.diagnosis_result is not None
            assert session.diagnosis_result.recommended_fix is not None

            invalid_plan = session.diagnosis_result.recommended_fix.model_copy(
                update={
                    "steps": [
                        RemediationStep(
                            step_id=1,
                            description="clear netem qdisc with placeholder iface",
                            tool="network.clear_tc_qdisc",
                            params={"node": "worker-03", "iface": "", "parent": ":3f"},
                            verification=VerificationConfig(method="wait", wait_seconds=1),
                        )
                    ]
                }
            )
            services.remediation_engine.register_plan(session_id, invalid_plan)

            _set_tool_channel_health(client, "k8s", health="ready")
            _set_tool_channel_health(client, "prometheus", health="ready")
            _set_tool_channel_health(client, "ssh", health="ready")

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            payload = approve.json()
            assert payload["success"] is False
            assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value
            assert "placeholder value" in payload["error"]["message"]

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            assert session_resp.json()["data"]["status"] == "approval_required"
        finally:
            client.close()

    def test_e2e_approve_route_real_mode_executes_and_resolves_after_observation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_real_client(monkeypatch, prometheus=_FakePrometheus(before=900.0, after=100.0))
        try:
            _set_tool_channel_health(client, "k8s", health="ready")
            _set_tool_channel_health(client, "prometheus", health="ready")
            services = client.app.state.services
            original_approve = services.remediation_engine.approve_and_execute

            async def _approve_and_clear_alert(
                target_session_id: str,
                approval_input: Any,
                *,
                progress_callback: Any | None = None,
            ) -> Any:
                result = await original_approve(
                    target_session_id,
                    approval_input,
                    progress_callback=progress_callback,
                )
                services.alert_store.replace([])
                return result

            services.remediation_engine.approve_and_execute = _approve_and_clear_alert  # type: ignore[method-assign]

            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            assert approve.json()["success"] is True

            events_resp = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
            assert events_resp.status_code == 200
            remediation_events = [
                item for item in events_resp.json()["data"] if item["type"] == EventType.REMEDIATION_PROGRESS.value
            ]
            stages = [item["data"]["stage"] for item in remediation_events]
            assert "execution_mocked" not in stages
            assert "observation_result" in stages
            observation = next(item for item in remediation_events if item["data"]["stage"] == "observation_result")
            assert observation["data"]["alert_cleared"] is True
            assert observation["data"]["metrics_improved"] is True
            succeeded = remediation_events[-1]
            assert succeeded["data"]["stage"] == "execution_succeeded"
            assert isinstance(succeeded["data"].get("step_results"), list)
            assert succeeded["data"]["step_results"]
            assert all(item.get("mocked") is not True for item in succeeded["data"]["step_results"])

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            assert session_resp.json()["data"]["status"] == "resolved"
        finally:
            client.close()

    def test_e2e_approve_route_real_mode_escalates_when_alert_persists_after_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_real_client(monkeypatch, prometheus=_FakePrometheus(before=900.0, after=1200.0))
        try:
            _set_tool_channel_health(client, "k8s", health="ready")
            _set_tool_channel_health(client, "prometheus", health="ready")
            services = client.app.state.services

            async def _approve_and_keep_alert(
                target_session_id: str,
                approval_input: Any,
                *,
                progress_callback: Any | None = None,
            ) -> Any:
                _ = (approval_input, progress_callback)
                plan = services.remediation_engine.get_plan(target_session_id)
                services.alert_store.replace([Alert.model_validate(_alert_payload())])
                return RemediationResult(
                    plan_id=plan.plan_id if plan is not None else "plan-missing",
                    success=True,
                    steps_completed=len(plan.steps) if plan is not None else 0,
                    steps_total=len(plan.steps) if plan is not None else 0,
                    verification_results=[{"step_id": 1, "verified": True}],
                )

            services.remediation_engine.approve_and_execute = _approve_and_keep_alert  # type: ignore[method-assign]

            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            payload = approve.json()
            assert payload["success"] is False
            assert payload["error"]["code"] == ErrorCode.REMEDIATION_EXECUTION_FAILED.value

            events_resp = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
            assert events_resp.status_code == 200
            remediation_events = [
                item for item in events_resp.json()["data"] if item["type"] == EventType.REMEDIATION_PROGRESS.value
            ]
            observation = next(item for item in remediation_events if item["data"]["stage"] == "observation_result")
            assert observation["data"]["alert_cleared"] is False
            assert observation["data"]["metrics_improved"] is False
            assert remediation_events[-1]["data"]["stage"] == "escalation_required"

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            assert session_resp.json()["data"]["status"] == "escalated"
        finally:
            client.close()

    def test_e2e_approve_route_rejects_outdated_plan_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            revise = client.post(
                f"/api/remediate/{session_id}/plan/revise",
                json={"instruction": "调整为先观察后执行"},
                headers=_auth_headers(token),
            )
            assert revise.status_code == 200
            assert revise.json()["success"] is True
            assert revise.json()["data"]["plan_version"] == 2

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice", "plan_version": 1},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            payload = approve.json()
            assert payload["success"] is False
            assert payload["error"]["code"] == ErrorCode.REMEDIATION_PLAN_VERSION_OUTDATED.value
            assert payload["error"]["message"] == "plan_version_outdated"
        finally:
            client.close()

    def test_e2e_revise_plan_removes_step_and_updates_session_plan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            revise = client.post(
                f"/api/remediate/{session_id}/plan/revise",
                json={"instruction": "移除步骤2"},
                headers=_auth_headers(token),
            )
            assert revise.status_code == 200
            payload = revise.json()
            assert payload["success"] is True
            plan_steps = payload["data"]["plan"]["steps"]
            assert len(plan_steps) == 1
            assert plan_steps[0]["step_id"] == 1

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            session_payload = session_resp.json()
            assert session_payload["data"]["diagnosis_result"]["recommended_fix"]["steps"] == plan_steps
        finally:
            client.close()

    def test_e2e_approve_route_timeout_sets_timeout_status_and_records_progress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, token = _build_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            services = client.app.state.services
            client.app.state.config.remediation.execution_timeout_seconds = 1
            original_approve = services.remediation_engine.approve_and_execute

            async def _slow_approve(target_session_id: str, approval_input: Any) -> Any:
                await asyncio.sleep(1.2)
                return await original_approve(target_session_id, approval_input)

            services.remediation_engine.approve_and_execute = _slow_approve  # type: ignore[method-assign]

            approve = client.post(
                f"/api/remediate/{session_id}/approve",
                json={"approved": True, "user": "alice"},
                headers=_auth_headers(token),
            )
            assert approve.status_code == 200
            approve_payload = approve.json()
            assert approve_payload["success"] is False
            assert "timeout" in str(approve_payload["error"]["message"]).lower()

            session_resp = client.get(f"/api/sessions/{session_id}", headers=_auth_headers(token))
            assert session_resp.status_code == 200
            assert session_resp.json()["data"]["status"] == "timeout"

            events_resp = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
            assert events_resp.status_code == 200
            stages = [
                str(item.get("data", {}).get("stage", ""))
                for item in events_resp.json()["data"]
                if item.get("type") == EventType.REMEDIATION_PROGRESS.value
            ]
            assert "execution_timeout" in stages
        finally:
            client.close()

    def test_e2e_sessions_events_returns_revision_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        try:
            diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
            session_id = diagnose["data"]["session_id"]

            revise = client.post(
                f"/api/remediate/{session_id}/plan/revise",
                json={"instruction": "观察阶段延长到180秒"},
                headers=_auth_headers(token),
            )
            assert revise.status_code == 200
            assert revise.json()["success"] is True

            events = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
            assert events.status_code == 200
            payload = events.json()
            assert payload["success"] is True
            event_types = [item["type"] for item in payload["data"]]
            assert EventType.PLAN_REVISED.value in event_types
            assert EventType.APPROVAL_REQUIRED.value in event_types
        finally:
            client.close()

    def test_e2e_rollback_route_returns_success_and_emits_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token)).json()
        session_id = diagnose["data"]["session_id"]
        approve = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )
        assert approve.status_code == 200
        assert approve.json()["success"] is True
        existing_events = client.get(f"/api/sessions/{session_id}/events", headers=_auth_headers(token))
        assert existing_events.status_code == 200
        existing_payload = existing_events.json()
        assert existing_payload["success"] is True
        last_event_id = str(existing_payload["data"][-1]["data"]["event_id"])

        response = client.post(
            f"/api/remediate/{session_id}/rollback",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True

        with client.websocket_connect(
            f"/ws/thinking-trace/{session_id}?token={token}&last_event_id={last_event_id}"
        ) as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()
        assert first["type"] == EventType.REMEDIATION_PROGRESS.value
        assert first["data"]["stage"] == "rollback_started"
        assert second["type"] == EventType.REMEDIATION_PROGRESS.value
        assert second["data"]["stage"] == "rollback_succeeded"

    def test_e2e_rollback_route_returns_validation_error_for_unknown_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post(
            "/api/remediate/unknown-session/rollback",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is False
        assert payload["error"]["code"] == ErrorCode.VALIDATION_ERROR.value

    def test_e2e_websocket_streams_trace_when_token_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        event = WSEvent(type=EventType.THINKING_STEP, session_id="fp-api-1", data={"event_id": "evt-1", "content": "thinking"})
        asyncio.run(client.app.state.services.trace_publisher.publish(event))

        with client.websocket_connect(f"/ws/thinking-trace/fp-api-1?token={token}") as websocket:
            payload = websocket.receive_json()

        assert payload["type"] == "thinking_step"

    def test_e2e_websocket_chat_streams_chunk_and_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        with client.websocket_connect(f"/ws/chat?token={token}") as websocket:
            websocket.send_json({"content": "status"})
            first = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()

        assert first["type"] == "chunk"
        assert "echo:status" in f"{first['data']['content']}{second['data']['content']}"
        assert third["type"] == "done"

    def test_e2e_handle_streams_live_trace_events_before_replay(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        runner = _FakeStreamingDiagnosisRunner()
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc"},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
                "loop_orchestrator": {"enable_re_diagnosis": False},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            response = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))
            assert response.status_code == 200
            payload = response.json()
            assert payload["success"] is True
            assert runner.callback_count >= 4
            with client.websocket_connect(f"/ws/thinking-trace/fp-api-1?token={token}") as websocket:
                first = websocket.receive_json()
                second = websocket.receive_json()
                third = websocket.receive_json()
                fourth = websocket.receive_json()

        assert first["type"] == EventType.TOOL_CALL.value
        assert second["type"] == EventType.TOOL_RESULT.value
        assert third["type"] == EventType.THINKING_STEP.value
        assert fourth["type"] == EventType.DIAGNOSIS_RESULT.value

    def test_e2e_diagnose_start_streams_live_trace_events(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        runner = _FakeStreamingDiagnosisRunner()
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc"},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
                "loop_orchestrator": {"enable_re_diagnosis": False},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            response = client.post("/api/diagnose/start", json=_alert_payload(), headers=_auth_headers(token))
            assert response.status_code == 200
            assert response.json()["success"] is True
            session_id = response.json()["data"]["session_id"]
            assert session_id
            for _ in range(20):
                if runner.callback_count >= 4:
                    break
                time.sleep(0.05)
            assert runner.callback_count >= 4

            with client.websocket_connect(f"/ws/thinking-trace/{session_id}?token={token}") as websocket:
                first = websocket.receive_json()
                second = websocket.receive_json()
                third = websocket.receive_json()
                fourth = websocket.receive_json()

        assert first["session_id"] == session_id
        assert second["session_id"] == session_id
        assert third["session_id"] == session_id
        assert fourth["session_id"] == session_id
        assert first["type"] == EventType.TOOL_CALL.value
        assert second["type"] == EventType.TOOL_RESULT.value
        assert third["type"] == EventType.THINKING_STEP.value
        assert fourth["type"] == EventType.DIAGNOSIS_RESULT.value

    def test_e2e_websocket_alerts_support_last_event_id_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        first = WSEvent(type=EventType.ALERT, session_id="alerts", data={"event_id": "1", "fingerprint": "a1"})
        second = WSEvent(type=EventType.ALERT, session_id="alerts", data={"event_id": "2", "fingerprint": "a2"})
        asyncio.run(client.app.state.services.trace_publisher.publish(first))
        asyncio.run(client.app.state.services.trace_publisher.publish(second))

        with client.websocket_connect(f"/ws/alerts?token={token}&last_event_id=1") as websocket:
            payload = websocket.receive_json()

        assert payload["type"] == "alert"
        assert payload["data"]["fingerprint"] == "a2"

    def test_e2e_websocket_topology_supports_last_event_id_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        first = WSEvent(type=EventType.TOPOLOGY, session_id="topology", data={"event_id": "1", "action": "sync_started"})
        second = WSEvent(type=EventType.TOPOLOGY, session_id="topology", data={"event_id": "2", "action": "sync_succeeded"})
        asyncio.run(client.app.state.services.trace_publisher.publish(first))
        asyncio.run(client.app.state.services.trace_publisher.publish(second))

        with client.websocket_connect(f"/ws/topology?token={token}&last_event_id=1") as websocket:
            payload = websocket.receive_json()

        assert payload["type"] == "topology"
        assert payload["data"]["action"] == "sync_succeeded"

    def test_e2e_websocket_resume_replays_available_history_when_last_event_id_evicted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc", "ws_max_events_per_session": 3},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            for i in range(1, 6):
                asyncio.run(
                    client.app.state.services.trace_publisher.publish(
                        WSEvent(type=EventType.ALERT, session_id="alerts", data={"event_id": str(i), "fingerprint": f"a{i}"})
                    )
                )
            with client.websocket_connect(f"/ws/alerts?token={token}&last_event_id=1") as websocket:
                payload = websocket.receive_json()

        assert payload["type"] == "alert"
        # retention=3, so event_id 1/2 are evicted; resume should replay earliest available event (id=3).
        assert payload["data"]["event_id"] == "3"
        assert payload["data"]["fingerprint"] == "a3"

    def test_e2e_alert_poller_syncs_alertmanager_alerts_to_snapshot_and_ws(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        fake_alert = Alert.model_validate(_alert_payload())
        blocked_alert = Alert.model_validate(_blocked_alert_payload())
        fake_channel = _FakeAlertChannel([fake_alert, blocked_alert])
        context.channels["alert"] = fake_channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.2,
                    "blocked_alert_names": ["GPUUtilizationHigh", "GPU utilization is high"],
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            snapshot = None
            for _ in range(20):
                response = client.get("/api/alerts", headers=_auth_headers(token))
                if response.status_code == 200 and response.json().get("data", {}).get("alerts"):
                    snapshot = response.json()
                    break
                time.sleep(0.1)
            assert snapshot is not None
            assert snapshot["success"] is True
            fingerprints = {item["fingerprint"] for item in snapshot["data"]["alerts"]}
            assert "fp-api-1" in fingerprints
            assert "fp-api-blocked-1" not in fingerprints
            assert fake_channel.connected is True
        assert fake_channel.connected is False

    def test_e2e_alert_poller_filters_kube_client_errors_by_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        fake_alert = Alert.model_validate(_alert_payload())
        kube_alert = Alert.model_validate(_kube_client_errors_alert_payload())
        fake_channel = _FakeAlertChannel([fake_alert, kube_alert])
        context.channels["alert"] = fake_channel
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc", "alert_poll_interval_seconds": 0.2},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            snapshot = None
            for _ in range(20):
                response = client.get("/api/alerts", headers=_auth_headers(token))
                if response.status_code == 200 and response.json().get("data", {}).get("alerts"):
                    snapshot = response.json()
                    break
                time.sleep(0.1)
            assert snapshot is not None
            fingerprints = {item["fingerprint"] for item in snapshot["data"]["alerts"]}
            assert "fp-api-1" in fingerprints
            assert "fp-kube-client-errors-1" not in fingerprints

    def test_e2e_alert_poller_filters_target_down_by_default(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        fake_alert = Alert.model_validate(_alert_payload())
        target_down_alert = Alert.model_validate(_demo_blocked_alert_payload())
        fake_channel = _FakeAlertChannel([fake_alert, target_down_alert])
        context.channels["alert"] = fake_channel
        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc", "alert_poll_interval_seconds": 0.2},
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            snapshot = None
            for _ in range(20):
                response = client.get("/api/alerts", headers=_auth_headers(token))
                if response.status_code == 200 and response.json().get("data", {}).get("alerts"):
                    snapshot = response.json()
                    break
                time.sleep(0.1)
            assert snapshot is not None
            fingerprints = {item["fingerprint"] for item in snapshot["data"]["alerts"]}
            assert "fp-api-1" in fingerprints
            assert "fp-target-down-1" not in fingerprints

    def test_e2e_default_blocked_kube_client_errors_prevents_auto_diagnose_even_when_targeted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        kube_alert = Alert.model_validate(_kube_client_errors_alert_payload())
        channel = _MutableAlertChannel([kube_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["KubeClientErrors"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            time.sleep(0.4)
            assert len(runner.calls) == 0

    def test_e2e_default_blocked_target_down_prevents_auto_diagnose_even_when_targeted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_down_alert = Alert.model_validate(_demo_blocked_alert_payload())
        channel = _MutableAlertChannel([target_down_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["TargetDown"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            time.sleep(0.4)
            assert len(runner.calls) == 0

    def test_e2e_blocked_alert_names_empty_allows_kube_client_errors(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        kube_alert = Alert.model_validate(_kube_client_errors_alert_payload())
        fake_channel = _FakeAlertChannel([kube_alert])
        context.channels["alert"] = fake_channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.2,
                    "blocked_alert_names": [],
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )
        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            snapshot = None
            for _ in range(20):
                response = client.get("/api/alerts", headers=_auth_headers(token))
                if response.status_code == 200 and response.json().get("data", {}).get("alerts"):
                    snapshot = response.json()
                    break
                time.sleep(0.1)
            assert snapshot is not None
            fingerprints = {item["fingerprint"] for item in snapshot["data"]["alerts"]}
            assert "fp-kube-client-errors-1" in fingerprints

            diagnose = client.post("/api/diagnose", json=_kube_client_errors_alert_payload(), headers=_auth_headers(token))
            assert diagnose.status_code == 200
            assert diagnose.json()["success"] is True

    def test_e2e_alert_poller_auto_diagnoses_target_alert_and_creates_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ) as client:
            for _ in range(40):
                if len(runner.calls) >= 1:
                    break
                time.sleep(0.05)
            assert len(runner.calls) == 1
            assert runner.calls[0] == target_alert.fingerprint

            sessions_payload = []
            for _ in range(40):
                response = client.get("/api/sessions", headers=_auth_headers(token))
                assert response.status_code == 200
                sessions_payload = response.json().get("data", [])
                if sessions_payload:
                    break
                time.sleep(0.05)
            assert any(item["alert_name"] == "CubeStudioWebLatencyP95High" for item in sessions_payload)

    def test_e2e_alert_poller_auto_diagnose_dedups_same_fingerprint_during_single_firing_lifecycle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            for _ in range(40):
                if len(runner.calls) >= 1:
                    break
                time.sleep(0.05)
            assert len(runner.calls) == 1
            time.sleep(0.4)
            assert len(runner.calls) == 1

    def test_e2e_alert_poller_auto_diagnose_allows_retrigger_after_alert_resolved_then_refired(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            for _ in range(40):
                if len(runner.calls) >= 1:
                    break
                time.sleep(0.05)
            assert len(runner.calls) == 1

            channel.set_alerts([])
            time.sleep(0.2)
            channel.set_alerts([target_alert])

            for _ in range(40):
                if len(runner.calls) >= 2:
                    break
                time.sleep(0.05)
            assert len(runner.calls) == 2

    def test_e2e_alert_poller_auto_diagnose_retries_when_auto_trigger_run_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _FlakyDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            for _ in range(60):
                if runner.calls >= 2:
                    break
                time.sleep(0.05)
            assert runner.calls >= 2

    def test_e2e_alert_poller_auto_diagnose_skips_non_target_alerts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        non_target_alert = Alert.model_validate(_alert_payload())
        channel = _MutableAlertChannel([non_target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            time.sleep(0.4)
            assert len(runner.calls) == 0

    def test_e2e_alert_poller_auto_diagnose_cancels_if_alert_disappears_before_delay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.4,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            time.sleep(0.1)
            channel.set_alerts([])
            time.sleep(0.45)
            assert len(runner.calls) == 0

    def test_e2e_alert_poller_auto_diagnose_respects_blocked_alert_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        registry, context = _registry()
        runner = _CountingDiagnosisRunner()
        target_alert = Alert.model_validate(_cube_web_latency_alert_payload())
        channel = _MutableAlertChannel([target_alert])
        context.channels["alert"] = channel
        config = SREAgentConfig.model_validate(
            {
                "global": {
                    "aidc_id": "test-aidc",
                    "alert_poll_interval_seconds": 0.05,
                    "blocked_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_alert_names": ["CubeStudioWebLatencyP95High"],
                    "auto_diagnose_delay_seconds": 0.05,
                },
                "ontology": {"db_path": str(tmp_path / "ontology.db")},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=runner,
                ontology=OntologyGraph(),
                memory=_FakeMemory(),
                knowledge=_FakeKnowledge(),
                tool_registry=registry,
                execution_context=context,
            )
        ):
            time.sleep(0.4)
            assert len(runner.calls) == 0

    def test_e2e_startup_loads_persisted_ontology_when_app_creates_internal_graph(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)

        db_path = tmp_path / "ontology.db"
        writer = OntologyGraph(str(db_path))
        asyncio.run(writer.connect())
        asyncio.run(
            writer.add_nodes(
                [
                    OntologyNode(
                        id="node-persisted",
                        entity_type=EntityType.NODE,
                        name="node-persisted",
                        properties={"zone": "az-2"},
                        updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
                    ),
                    OntologyNode(
                        id="service:vllm",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="vllm",
                        properties={"namespace": "service"},
                        updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
                    ),
                ]
            )
        )
        asyncio.run(
            writer.add_edge(
                OntologyEdge(
                    source_id="service:vllm",
                    target_id="node-persisted",
                    relation=RelationType.DEPENDS_ON,
                    properties={},
                )
            )
        )
        asyncio.run(writer.close())

        config = SREAgentConfig.model_validate(
            {
                "global": {"aidc_id": "test-aidc"},
                "ontology": {"db_path": str(db_path)},
                "memory": {"db_dir": str(tmp_path / "memory")},
            }
        )

        with TestClient(
            create_app(
                config=config,
                diagnosis_runner=_FakeDiagnosisRunner(),
                knowledge=_FakeKnowledge(),
                tool_registry=_registry()[0],
                execution_context=_registry()[1],
            )
        ) as client:
            ontology_response = client.post(
                "/api/ontology/query",
                json={"entity_type": "node", "filters": {"id": "node-persisted"}},
                headers=_auth_headers(token),
            )
            path_response = client.post(
                "/api/ontology/path",
                json={"from_id": "service:vllm", "to_id": "node-persisted"},
                headers=_auth_headers(token),
            )

        assert ontology_response.status_code == 200
        assert ontology_response.json()["success"] is True
        assert ontology_response.json()["data"][0]["id"] == "node-persisted"
        assert path_response.status_code == 200
        assert path_response.json()["data"] == ["service:vllm", "node-persisted"]
