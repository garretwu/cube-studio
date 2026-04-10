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

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
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
    assert "topology_blast_radius" in (captured.get("variables") or {})
    assert captured["reasoning_context_strategy"] == "state_rebuilt"
    assert captured["reasoning_overflow_behavior"] == "fail"
    assert captured["reasoning_input_target_tokens"] == 180000
    assert captured["reasoning_model_family"] == "MiniMax-M2.7"
    assert payload["data"]["alert"]["annotations"].get("topology_blast_radius_summary")
    channels = client.app.state.services.remediation_engine.execution_context.channels
    assert "ontology" in channels
    assert "k8s" in channels
    assert "prometheus" in channels
    assert "ssh" in channels
    assert "remediation" in channels


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


def test_create_app_exposes_default_re_diagnose_runner(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    app = create_app(config=config)

    assert app.state.services.incident_handler.loop.re_diagnose_runner is not None


def test_default_runner_injects_network_runtime_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            "session_id": "default-session-network-1",
            "status": "diagnosed",
            "summary": "synthetic network diagnosis",
        }

    monkeypatch.setattr("sre_agent.server.run_diagnosis", _fake_run_diagnosis)

    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
inventory:
  workers:
    - name: worker-03
      ssh:
        host: 10.11.4.12
        user: demo
    - name: worker-04
      ssh:
        host: 10.11.4.13
        user: demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SRE_SSH_INVENTORY_PATH", str(inventory_path))

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
                "alert_name": "NetworkLatencyHigh100ms",
                "severity": "warning",
                "labels": {"instance": "10.11.0.12", "phase": "rtt", "device_role": "rdma"},
                "annotations": {"summary": "network latency high"},
                "starts_at": datetime(2026, 3, 26, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-network-1",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    variables = captured.get("variables") or {}
    assert variables["node"] == "worker-03"
    assert variables["namespace"] == "service"
    assert variables["promql"] == 'probe_icmp_duration_seconds{instance="10.11.0.12"}'


def test_default_runner_allows_explicit_transcript_compact_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            "session_id": "default-session-compact-1",
            "status": "diagnosed",
            "summary": "synthetic compact diagnosis",
        }

    monkeypatch.setattr("sre_agent.server.run_diagnosis", _fake_run_diagnosis)

    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
            "agent": {
                "reasoning_context_strategy": "transcript_compact",
                "reasoning_overflow_behavior": "compact",
                "reasoning_input_target_tokens": 8192,
                "reasoning_model_family": "custom-small-context",
            },
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
                "fingerprint": "fp-default-compact-1",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    assert captured["reasoning_context_strategy"] == "transcript_compact"
    assert captured["reasoning_overflow_behavior"] == "compact"
    assert captured["reasoning_input_target_tokens"] == 8192
    assert captured["reasoning_model_family"] == "custom-small-context"


def test_default_runner_maps_10_11_0_13_to_worker_04(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
inventory:
  workers:
    - name: worker-03
      ssh:
        host: 10.11.4.12
        user: demo
    - name: worker-04
      ssh:
        host: 10.11.4.13
        user: demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SRE_SSH_INVENTORY_PATH", str(inventory_path))

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"session_id": "default-session-network-2", "status": "diagnosed", "summary": "synthetic network diagnosis"}

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
                "alert_name": "NetworkLatencyHigh100ms",
                "severity": "warning",
                "labels": {"instance": "10.11.0.13", "phase": "rtt", "device_role": "rdma"},
                "annotations": {"summary": "network latency high"},
                "starts_at": datetime(2026, 3, 26, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-network-2",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    variables = captured.get("variables") or {}
    assert variables["node"] == "worker-04"


def test_default_runner_keeps_node_unset_when_instance_not_mapped(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    inventory_path = tmp_path / "inventory.yaml"
    inventory_path.write_text(
        """
inventory:
  workers:
    - name: worker-03
      ssh:
        host: 10.11.4.12
        user: demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SRE_SSH_INVENTORY_PATH", str(inventory_path))

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"session_id": "default-session-network-3", "status": "diagnosed", "summary": "synthetic network diagnosis"}

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
                "alert_name": "NetworkLatencyHigh100ms",
                "severity": "warning",
                "labels": {"instance": "10.22.0.13", "phase": "rtt", "device_role": "rdma"},
                "annotations": {"summary": "network latency high"},
                "starts_at": datetime(2026, 3, 26, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-network-3",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    variables = captured.get("variables") or {}
    assert "node" not in variables


def test_create_app_strict_mode_rejects_when_core_channels_not_ready(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")
    monkeypatch.delenv("SRE_PROMETHEUS_URL", raising=False)
    monkeypatch.delenv("SRE_SSH_INVENTORY_PATH", raising=False)

    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
            "tool_runtime": {"mode": "strict", "core_required_channels": ["ssh", "prometheus"]},
        }
    )

    try:
        create_app(config=config)
    except RuntimeError as exc:
        assert "strict mode startup blocked" in str(exc)
    else:
        raise AssertionError("expected strict mode startup failure")


def test_create_app_fails_fast_when_llm_key_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.delenv("SRE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SRE_LLM_MODEL", raising=False)

    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    try:
        create_app(config=config)
    except RuntimeError as exc:
        assert "SRE_OPENAI_API_KEY or OPENAI_API_KEY is required" in str(exc)
    else:
        raise AssertionError("expected startup failure when llm key is missing")
