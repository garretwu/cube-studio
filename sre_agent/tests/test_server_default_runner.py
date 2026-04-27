from __future__ import annotations

from datetime import UTC, datetime
import asyncio

from fastapi.testclient import TestClient

from sre_agent.auth.jwt import CurrentUser, encode_token, resolve_jwt_settings
from sre_agent.config import SREAgentConfig
from sre_agent.models.alert import Alert
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.graph import OntologyGraph
from sre_agent.server import _build_alert_blast_radius_context, create_app
from sre_agent.tools import ToolExecutionContext


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _infer_kind_from_entity_payload(entity: dict[str, object]) -> str:
    entity_id = str(entity.get("id") or "").strip().lower()
    entity_type = str(entity.get("type") or "").strip().lower()
    if entity_type == EntityType.INFERENCE_SERVICE.value or entity_id.startswith("svc:"):
        return "service"
    if entity_type == EntityType.K8S_POD.value or entity_id.startswith("pod:"):
        return "pod"
    if entity_type == EntityType.NODE.value or entity_id.startswith("node:"):
        return "node"
    if entity_type == EntityType.GPU.value or entity_id.startswith("gpu:"):
        return "gpu"
    if entity_id.startswith("proc:") or "process" in entity_id:
        return "process"
    return "other"


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


def test_build_alert_blast_radius_context_limits_noise_for_large_topology(tmp_path) -> None:
    db_path = tmp_path / "topology.db"
    graph = OntologyGraph(str(db_path))
    asyncio.run(graph.connect())
    try:
        now = datetime.now(UTC)
        asyncio.run(
            graph.add_nodes(
                [
                    OntologyNode(
                        id="svc:service:qwen-main",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="qwen-main",
                        properties={"namespace": "service"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="pod:service:qwen-main-0",
                        entity_type=EntityType.K8S_POD,
                        name="qwen-main-0",
                        properties={"namespace": "service", "node": "worker-03"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="worker-03",
                        entity_type=EntityType.NODE,
                        name="worker-03",
                        properties={"namespace": "service"},
                        updated_at=now,
                    ),
                ]
                + [
                    OntologyNode(
                        id=f"svc:service:related-{idx}",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name=f"related-{idx}",
                        properties={"namespace": "service", "node": "worker-03"},
                        updated_at=now,
                    )
                    for idx in range(1, 30)
                ]
                + [
                    OntologyNode(
                        id=f"svc:ceph-csi:noise-{idx}",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name=f"ceph-csi-noise-{idx}",
                        properties={"namespace": "kube-system", "node": "worker-03"},
                        updated_at=now,
                    )
                    for idx in range(1, 8)
                ]
            )
        )
        asyncio.run(
            graph.add_edges(
                [
                    OntologyEdge(
                        source_id="svc:service:qwen-main",
                        target_id="pod:service:qwen-main-0",
                        relation=RelationType.SERVES,
                    ),
                    OntologyEdge(
                        source_id="pod:service:qwen-main-0",
                        target_id="worker-03",
                        relation=RelationType.HOSTED_ON,
                    ),
                    OntologyEdge(
                        source_id="svc:service:qwen-main",
                        target_id="worker-03",
                        relation=RelationType.HOSTED_ON,
                    ),
                ]
                + [
                    OntologyEdge(
                        source_id=f"svc:service:related-{idx}",
                        target_id="worker-03",
                        relation=RelationType.HOSTED_ON,
                    )
                    for idx in range(1, 30)
                ]
                + [
                    OntologyEdge(
                        source_id=f"svc:ceph-csi:noise-{idx}",
                        target_id="worker-03",
                        relation=RelationType.HOSTED_ON,
                    )
                    for idx in range(1, 8)
                ]
            )
        )

        alert = Alert(
            alert_name="AIServiceTTFTP99High",
            severity="warning",
            labels={"service": "qwen-main", "namespace": "service"},
            annotations={"summary": "ttft high"},
            starts_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC).isoformat(),
            fingerprint="fp-topology-noise",
            source="alertmanager",
            status="firing",
        )
        context = _build_alert_blast_radius_context(graph, alert)
    finally:
        asyncio.run(graph.close())

    assert context["filter_policy"] == "direct_l1_plus_scoped_l2_v1"
    assert context["affected_count"] <= 12
    assert context["filtered_affected_count"] == context["affected_count"]
    assert context["raw_affected_count"] >= context["affected_count"]
    assert context["dropped_count"] >= 1
    assert all("ceph-csi" not in str(entity.get("id", "")).lower() for entity in context["affected_entities"])
    assert context["mandatory_kept"]["service"] >= 1
    assert context["mandatory_kept"]["pod"] >= 1
    assert context["mandatory_kept"]["node"] >= 1
    assert "mandatory_kept={" in context["summary"]
    assert context["dropped_after_mandatory_budget"] == context["dropped_by_policy"]["dropped_after_mandatory_budget"]


def test_build_alert_blast_radius_context_keeps_node_gpu_for_temperature_alert(tmp_path) -> None:
    db_path = tmp_path / "topology-temp.db"
    graph = OntologyGraph(str(db_path))
    asyncio.run(graph.connect())
    try:
        now = datetime.now(UTC)
        asyncio.run(
            graph.add_nodes(
                [
                    OntologyNode(
                        id="svc:monitoring:dcgm-exporter",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="dcgm-exporter",
                        properties={"namespace": "monitoring"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="node:worker-07",
                        entity_type=EntityType.NODE,
                        name="worker-07",
                        properties={"namespace": "service", "node": "worker-07"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="gpu:worker-07:0",
                        entity_type=EntityType.GPU,
                        name="GPU-0",
                        properties={"namespace": "service", "node": "worker-07"},
                        updated_at=now,
                    ),
                ]
            )
        )
        asyncio.run(
            graph.add_edges(
                [
                    OntologyEdge(
                        source_id="svc:monitoring:dcgm-exporter",
                        target_id="node:worker-07",
                        relation=RelationType.HOSTED_ON,
                    ),
                    OntologyEdge(
                        source_id="node:worker-07",
                        target_id="gpu:worker-07:0",
                        relation=RelationType.HOSTED_ON,
                    ),
                ]
            )
        )
        alert = Alert(
            alert_name="GPUTemperatureHigh",
            severity="warning",
            labels={"service": "dcgm-exporter", "namespace": "monitoring"},
            annotations={"summary": "gpu temperature high"},
            starts_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC).isoformat(),
            fingerprint="fp-topology-temp",
            source="alertmanager",
            status="firing",
        )
        context = _build_alert_blast_radius_context(graph, alert)
    finally:
        asyncio.run(graph.close())

    kinds = {_entity_kind for _entity_kind in (_infer_kind_from_entity_payload(e) for e in context["affected_entities"])}
    assert "node" in kinds
    assert "gpu" in kinds


def test_build_alert_blast_radius_context_keeps_service_pod_node_for_ttft(tmp_path) -> None:
    db_path = tmp_path / "topology-ttft.db"
    graph = OntologyGraph(str(db_path))
    asyncio.run(graph.connect())
    try:
        now = datetime.now(UTC)
        asyncio.run(
            graph.add_nodes(
                [
                    OntologyNode(
                        id="svc:service:qwen-main",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="qwen-main",
                        properties={"namespace": "service"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="pod:service:qwen-main-0",
                        entity_type=EntityType.K8S_POD,
                        name="qwen-main-0",
                        properties={"namespace": "service", "node": "worker-03"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="node:worker-03",
                        entity_type=EntityType.NODE,
                        name="worker-03",
                        properties={"namespace": "service", "node": "worker-03"},
                        updated_at=now,
                    ),
                    OntologyNode(
                        id="svc:service:qwen-sidecar",
                        entity_type=EntityType.INFERENCE_SERVICE,
                        name="qwen-sidecar",
                        properties={"namespace": "service"},
                        updated_at=now,
                    ),
                ]
            )
        )
        asyncio.run(
            graph.add_edges(
                [
                    OntologyEdge(
                        source_id="svc:service:qwen-main",
                        target_id="pod:service:qwen-main-0",
                        relation=RelationType.SERVES,
                    ),
                    OntologyEdge(
                        source_id="pod:service:qwen-main-0",
                        target_id="node:worker-03",
                        relation=RelationType.HOSTED_ON,
                    ),
                    OntologyEdge(
                        source_id="svc:service:qwen-sidecar",
                        target_id="pod:service:qwen-main-0",
                        relation=RelationType.SERVES,
                    ),
                ]
            )
        )
        alert = Alert(
            alert_name="AIServiceTTFTP99High",
            severity="warning",
            labels={"service": "qwen-main", "namespace": "service"},
            annotations={"summary": "ttft p99 high"},
            starts_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC).isoformat(),
            fingerprint="fp-topology-ttft",
            source="alertmanager",
            status="firing",
        )
        context = _build_alert_blast_radius_context(graph, alert)
    finally:
        asyncio.run(graph.close())

    kinds = {_entity_kind for _entity_kind in (_infer_kind_from_entity_payload(e) for e in context["affected_entities"])}
    assert "service" in kinds
    assert "pod" in kinds
    assert "node" in kinds
    assert context["affected_count"] <= 12
    assert context["raw_affected_count"] >= context["affected_count"]


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


def test_infer_default_promql_uses_ttft_query_for_ttft_alert() -> None:
    from sre_agent.server import _infer_default_promql

    promql = _infer_default_promql(
        alert_name="AIServiceTTFTP99High",
        labels={"service": "qwen3-32b-fp8-202602261"},
    )

    assert "histogram_quantile(0.99" in promql
    assert 'service="qwen3-32b-fp8-202602261"' in promql


def test_apply_ttft_runtime_bootstrap_resolves_pod_to_node(monkeypatch, tmp_path) -> None:
    from sre_agent.models.alert import Alert
    from sre_agent.server import _apply_ttft_runtime_bootstrap

    class _FakeK8s:
        async def resolve_pod_names_for_service(self, namespace: str, service_name: str):  # noqa: ANN001
            _ = (namespace, service_name)
            return ["qwen3-32b-fp8-202602261-7df4474bbd-25cmh"]

        async def resolve_node_ip_for_pod(self, namespace: str, pod_name: str):  # noqa: ANN001
            _ = (namespace, pod_name)
            return "10.11.4.12"

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

    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )
    alert = Alert.model_validate(
        {
            "alert_name": "AIServiceTTFTP99High",
            "severity": "warning",
            "labels": {
                "namespace": "service",
                "service": "qwen3-32b-fp8-202602261",
            },
            "annotations": {"summary": "ttft high"},
            "starts_at": datetime(2026, 4, 14, 12, 0, tzinfo=UTC).isoformat(),
            "fingerprint": "ttft-bootstrap-1",
            "status": "firing",
        }
    )
    context = ToolExecutionContext(channels={"k8s": _FakeK8s()})
    payload = {
        "alert_name": alert.alert_name,
        "namespace": "service",
        "service": "qwen3-32b-fp8-202602261",
        "promql": "up",
    }

    updated = asyncio.run(
        _apply_ttft_runtime_bootstrap(
            payload=payload,
            alert=alert,
            context=context,
            cfg=config,
        )
    )
    assert updated["pod"] == "qwen3-32b-fp8-202602261-7df4474bbd-25cmh"
    assert updated["node_ip"] == "10.11.4.12"
    assert updated["node"] == "worker-03"
    assert "resolve_service_pods" in str(updated.get("target_resolution_chain", ""))


def test_apply_ttft_runtime_bootstrap_propagates_precheck_block_reason(tmp_path) -> None:
    from sre_agent.models.alert import Alert
    from sre_agent.server import _apply_ttft_runtime_bootstrap

    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "agent": {"ttft_external_process_default_node": "10.11.4.13"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )
    alert = Alert.model_validate(
        {
            "alert_name": "AIServiceTTFTP99High",
            "severity": "warning",
            "labels": {"namespace": "service", "service": "qwen3-32b-fp8-202602261"},
            "annotations": {"summary": "ttft high"},
            "starts_at": datetime(2026, 4, 14, 12, 0, tzinfo=UTC).isoformat(),
            "fingerprint": "ttft-bootstrap-precheck-1",
            "status": "firing",
        }
    )
    context = ToolExecutionContext(
        metadata={
            "ttft_external_ssh_precheck": {
                "node": "10.11.4.13",
                "ok": False,
                "error": "SSH precheck failed for TTFT external node 10.11.4.13: Permission denied",
            }
        }
    )
    payload = {
        "alert_name": alert.alert_name,
        "namespace": "service",
        "service": "qwen3-32b-fp8-202602261",
        "ttft_external_process_default_node": "10.11.4.13",
        "promql": "up",
    }

    updated = asyncio.run(
        _apply_ttft_runtime_bootstrap(
            payload=payload,
            alert=alert,
            context=context,
            cfg=config,
        )
    )

    assert updated["ttft_external_ssh_precheck_ok"] is False
    assert "ttft_external_probe_blocked_reason" in updated
    assert "Permission denied" in str(updated["ttft_external_probe_blocked_reason"])


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


def test_default_runner_ttft_alert_uses_ttft_allowed_tools(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    captured: dict[str, object] = {}

    async def _fake_run_diagnosis(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {
            "session_id": "ttft-session-1",
            "status": "diagnosed",
            "summary": "synthetic ttft diagnosis",
        }

    monkeypatch.setattr("sre_agent.server.run_diagnosis", _fake_run_diagnosis)

    settings = resolve_jwt_settings()
    token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "agent": {"ttft_external_process_default_node": "10.11.4.13"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config)) as client:
        response = client.post(
            "/api/diagnose",
            json={
                "alert_name": "AIServiceTTFTP99High",
                "severity": "warning",
                "labels": {"namespace": "service", "service": "qwen3-32b-fp8-202602261"},
                "annotations": {"summary": "ttft high"},
                "starts_at": datetime(2026, 4, 14, 12, 0, tzinfo=UTC).isoformat(),
                "fingerprint": "fp-ttft-allowed-tools-1",
                "status": "firing",
            },
            headers=_auth_headers(token),
        )

    assert response.status_code == 200
    allowed = captured.get("allowed_tool_names")
    assert isinstance(allowed, list)
    assert "k8s.resolve_service_pods" in allowed
    assert "k8s.resolve_pod_node_ip" in allowed
    assert "gpu.get_metrics" in allowed
    assert "process.find" in allowed
    assert "prometheus.query_instant" in allowed
    variables = captured.get("variables")
    assert isinstance(variables, dict)
    assert variables.get("ttft_external_process_default_node") == "10.11.4.13"


def test_create_app_injects_ttft_external_process_default_node_into_context_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    context = ToolExecutionContext()
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "agent": {"ttft_external_process_default_node": "10.11.4.13"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config, execution_context=context)):
        pass

    assert context.metadata.get("ttft_external_process_default_node") == "10.11.4.13"


def test_create_app_runs_ttft_external_ssh_precheck_and_sets_metadata(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")

    class _Result:
        success = True
        output = "yuyonghao\nwj-lab-cpt-04\n2202736 python3 -m load_simulator run --only inference\n"
        error = ""

    class _SSHChannel:
        async def run_command(self, node: str, command: str, use_sudo: bool = False):  # noqa: ANN001
            _ = command
            assert node == "10.11.4.13"
            assert use_sudo is False
            return _Result()

    context = ToolExecutionContext(channels={"ssh": _SSHChannel()})
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "agent": {"ttft_external_process_default_node": "10.11.4.13"},
            "ontology": {"db_path": str(tmp_path / "ontology.db")},
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config, execution_context=context)):
        pass

    precheck = context.metadata.get("ttft_external_ssh_precheck")
    assert isinstance(precheck, dict)
    assert precheck.get("node") == "10.11.4.13"
    assert precheck.get("ok") is True
    assert precheck.get("whoami") == "yuyonghao"
    assert precheck.get("hostname") == "wj-lab-cpt-04"


def test_build_ttft_query_includes_external_pressure_path_instruction(tmp_path) -> None:
    from sre_agent.models.alert import Alert
    from sre_agent.server import _build_diagnosis_query

    alert = Alert.model_validate(
        {
            "alert_name": "AIServiceTTFTP99High",
            "severity": "warning",
            "labels": {"namespace": "service", "service": "qwen3-32b-fp8-202602261"},
            "annotations": {"summary": "ttft high"},
            "starts_at": datetime(2026, 4, 14, 12, 0, tzinfo=UTC).isoformat(),
            "fingerprint": "fp-ttft-query-external-process-1",
            "status": "firing",
        }
    )
    query = _build_diagnosis_query(
        alert=alert,
        variables={
            "namespace": "service",
            "service": "qwen3-32b-fp8-202602261",
            "ttft_external_process_default_node": "10.11.4.13",
        },
        topology_context={"summary": "topology-summary", "affected_count": 1},
        extra_alerts=None,
    )
    assert "external pressure path" in query
    assert "10.11.4.13" in query
