from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.models.alert import Alert
from sre_agent.models.diagnosis import DiagnosisResult, DiagnosisSession, RankedRootCause
from sre_agent.models.events import EventType, WSEvent
from sre_agent.models.ontology import EntityType, OntologyNode
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


class TestAPIUnit:
    def test_unit_returns_401_when_bearer_missing_on_protected_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = _build_client(monkeypatch)
        response = client.get("/api/memory/incidents")

        assert response.status_code == 401


class TestAPIIntegration:
    def test_integration_runs_diagnose_route_when_runner_injected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/diagnose", json=_alert_payload(), headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["session_id"] == "fp-api-1"

    def test_integration_runs_handle_route_when_full_pipeline_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        response = client.post("/api/handle", json=_alert_payload(), headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["outcome"] in {"resolved", "escalated", "re_diagnosed", "partially_resolved"}


class TestAPIE2E:
    def test_e2e_approve_route_executes_registered_plan_when_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, token = _build_client(monkeypatch)
        diagnose = client.post("/api/diagnose", json=_alert_payload(), headers={"Authorization": f"Bearer {token}"}).json()
        session_id = diagnose["data"]["session_id"]
        plan = diagnose["data"]["diagnosis_result"]["ranked_candidates"][0]["recommended_fix"]
        client.app.state.services.remediation_engine.register_plan(session_id, RemediationPlan.model_validate(plan))

        response = client.post(
            f"/api/remediate/{session_id}/approve",
            json={"approved": True, "user": "alice"},
            headers={"Authorization": f"Bearer {token}"},
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
