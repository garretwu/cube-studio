from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.server import create_app


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_app_assembles_default_diagnosis_runner_and_serves_diagnose(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        _ = kwargs
        return {
            "session_id": "default-session-1",
            "status": "diagnosed",
            "summary": "synthetic diagnosis",
            "trace_items": [
                {
                    "type": "thought",
                    "step": 1,
                    "content": "collect metrics",
                    "action": "tool_call",
                    "tool_name": "gpu.get_metrics",
                    "tool_params": {"node": "worker-03"},
                },
                {
                    "type": "observation",
                    "tool": "gpu.get_metrics",
                    "params": {"node": "worker-03"},
                    "result": {"success": True, "data": {"utilization": 90}},
                },
            ],
        }

    monkeypatch.setattr("sre_agent.server.run_diagnosis", _fake_run_diagnosis)

    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config)) as client:
        response = client.post(
            "/api/diagnose",
            json={
                "alert_name": "GPUUtilizationHigh",
                "severity": "warning",
                "labels": {"node": "worker-03"},
                "annotations": {"summary": "gpu high"},
                "starts_at": datetime(2026, 3, 26, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-default-1",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["session_id"] == "default-session-1"
    assert payload["data"]["diagnosis_result"]["root_cause"] == "synthetic diagnosis"
    assert payload["data"]["trace"] is not None
    assert len(payload["data"]["trace"]["steps"]) == 2


def test_create_app_default_runner_supports_handle_without_missing_runner_error(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        _ = kwargs
        return {
            "session_id": "default-session-handle-1",
            "status": "diagnosed",
            "summary": "synthetic diagnosis handle",
        }

    monkeypatch.setattr("sre_agent.server.run_diagnosis", _fake_run_diagnosis)

    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config)) as client:
        response = client.post(
            "/api/handle",
            json={
                "alert_name": "GPUUtilizationHigh",
                "severity": "warning",
                "labels": {"node": "worker-03"},
                "annotations": {"summary": "gpu high"},
                "starts_at": datetime(2026, 3, 26, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-default-handle-1",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["outcome"] in {"resolved", "escalated", "re_diagnosed", "partially_resolved"}
