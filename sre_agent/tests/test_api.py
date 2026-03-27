from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.models.alert import Alert
from sre_agent.models.common import ErrorCode
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, Observation, RankedRootCause, ThinkingStep, ThinkingTrace
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.models.remediation import RemediationPlan, RemediationStep, VerificationConfig
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
                )
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


class _FakeKnowledge:
    async def search(self, query: str, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        return [{"query": query, "category": category or "all", "top_k": top_k}]

    async def list_documents(self) -> list[dict[str, Any]]:
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
    app = create_app(
        diagnosis_runner=_FakeDiagnosisRunner(),
        ontology=graph,
        memory=_FakeMemory(),
        knowledge=_FakeKnowledge(),
        tool_registry=registry,
        execution_context=context,
        chat_handler=lambda message: asyncio.sleep(0, result=f"echo:{message}"),
    )
    return TestClient(app), token


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestAPIUnit:
    def test_unit_returns_401_when_bearer_missing_on_protected_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)
        response = client.get("/api/memory/incidents")

        assert response.status_code == 401

    def test_unit_returns_401_when_bearer_missing_on_topology_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)

        response = client.get("/api/topology")

        assert response.status_code == 401


class TestAPIIntegration:
    def test_integration_runs_diagnose_route_when_runner_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["session_id"] == "fp-api-1"

    def test_integration_runs_handle_route_when_full_pipeline_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/handle", json=_alert_payload(), headers=_auth_headers(token))

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["outcome"] in {"resolved", "escalated", "re_diagnosed", "partially_resolved"}

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
        assert {item["id"] for item in payload["data"]["nodes"]} == {"node-a", "service:vllm"}
        assert payload["data"]["edges"] == [
            {
                "source_id": "service:vllm",
                "target_id": "node-a",
                "relation": "depends_on",
                "properties": {},
            }
        ]

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

        with client.websocket_connect(f"/ws/thinking-trace/{session_id}?token={token}") as websocket:
            first = websocket.receive_json()
            second = websocket.receive_json()

        assert first["type"] == EventType.REMEDIATION_PROGRESS.value
        assert first["data"]["stage"] == "execution_started"
        assert second["type"] == EventType.REMEDIATION_PROGRESS.value
        assert second["data"]["stage"] == "execution_succeeded"

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

        response = client.post(
            f"/api/remediate/{session_id}/rollback",
            headers=_auth_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True

        with client.websocket_connect(f"/ws/thinking-trace/{session_id}?token={token}&last_event_id=2") as websocket:
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
        fake_channel = _FakeAlertChannel([fake_alert])
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
            assert snapshot["success"] is True
            assert snapshot["data"]["alerts"][0]["fingerprint"] == "fp-api-1"
            assert fake_channel.connected is True
        assert fake_channel.connected is False

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
