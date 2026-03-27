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
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause
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


class _FakeKnowledge:
    async def search(self, query: str, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        return [{"query": query, "category": category or "all", "top_k": top_k}]


class _FakeMemory:
    async def list_recent(self, last: int = 10):  # noqa: ANN201
        return []

    async def get_known_patterns(self):  # noqa: ANN201
        return []


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
        plan = diagnose["data"]["diagnosis_result"]["ranked_candidates"][0]["recommended_fix"]
        client.app.state.services.remediation_engine.register_plan(session_id, RemediationPlan.model_validate(plan))

        response = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers=_auth_headers(token),
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_e2e_websocket_streams_trace_when_token_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        event = WSEvent(type=EventType.THINKING_STEP, session_id="fp-api-1", data={"event_id": "evt-1", "content": "thinking"})
        asyncio.run(client.app.state.services.trace_publisher.publish(event))

        with client.websocket_connect(f"/ws/thinking-trace/fp-api-1?token={token}") as websocket:
            payload = websocket.receive_json()

        assert payload["type"] == "thinking_step"

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
