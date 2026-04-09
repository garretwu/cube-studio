from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sre_agent.config import SREAgentConfig
from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.topology import discovery as discovery_module


def _node(node_id: str, entity_type: EntityType, *, source: str) -> OntologyNode:
    return OntologyNode(
        id=node_id,
        entity_type=entity_type,
        name=node_id,
        properties={"source": source},
        status="online",
        updated_at=datetime(2026, 4, 7, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_discover_hybrid_snapshot_merges_static_and_dynamic(monkeypatch: pytest.MonkeyPatch) -> None:
    static_nodes = [_node("node-a", EntityType.NODE, source="lab_seed")]
    static_edges = []
    dynamic_nodes = [
        _node("node-a", EntityType.NODE, source="k8s"),  # must not override static node source
        _node("pod:default:demo", EntityType.K8S_POD, source="k8s"),
        _node("svc:team-a:demo", EntityType.INFERENCE_SERVICE, source="k8s"),
    ]
    dynamic_edges = [
        OntologyEdge(
            source_id="pod:default:demo",
            target_id="node-a",
            relation=RelationType.HOSTED_ON,
            properties={"namespace": "default"},
        ),
        OntologyEdge(
            source_id="svc:team-a:demo",
            target_id="node-a",
            relation=RelationType.HOSTED_ON,
            properties={"namespace": "team-a"},
        ),
    ]

    async def _fake_static(_: SREAgentConfig):
        return static_nodes, static_edges, {"switch": {"nodes": 1, "edges": 0}}

    async def _fake_k8s(_: SREAgentConfig):
        return dynamic_nodes, dynamic_edges, {"k8s_workload": {"nodes": 3, "edges": 2}}

    monkeypatch.setattr(discovery_module, "discover_static_snapshot", _fake_static)
    monkeypatch.setattr(discovery_module, "discover_k8s_workload_snapshot", _fake_k8s)

    nodes, edges, counts, fallback_reason = await discovery_module.discover_hybrid_snapshot(SREAgentConfig())

    assert fallback_reason is None
    assert {node.id for node in nodes} == {"node-a", "pod:default:demo", "svc:team-a:demo"}
    node_a = next(node for node in nodes if node.id == "node-a")
    assert node_a.properties.get("source") == "lab_seed"
    assert len(edges) == 2
    assert {edge.relation for edge in edges} == {RelationType.HOSTED_ON}
    assert counts["switch"] == {"nodes": 1, "edges": 0}
    assert counts["k8s_workload"] == {"nodes": 3, "edges": 2}


@pytest.mark.asyncio
async def test_discover_hybrid_snapshot_falls_back_to_static_on_k8s_error(monkeypatch: pytest.MonkeyPatch) -> None:
    static_nodes = [_node("node-a", EntityType.NODE, source="lab_seed")]
    static_edges = []

    async def _fake_static(_: SREAgentConfig):
        return static_nodes, static_edges, {"switch": {"nodes": 1, "edges": 0}}

    async def _fake_k8s(_: SREAgentConfig):
        raise RuntimeError("k8s auth failed")

    monkeypatch.setattr(discovery_module, "discover_static_snapshot", _fake_static)
    monkeypatch.setattr(discovery_module, "discover_k8s_workload_snapshot", _fake_k8s)

    nodes, edges, counts, fallback_reason = await discovery_module.discover_hybrid_snapshot(SREAgentConfig())

    assert [node.id for node in nodes] == ["node-a"]
    assert edges == []
    assert counts == {"switch": {"nodes": 1, "edges": 0}}
    assert isinstance(fallback_reason, str)
    assert "dynamic K8s workload discovery failed" in fallback_reason


@pytest.mark.asyncio
async def test_discover_k8s_workload_snapshot_discovers_all_non_system_namespaces_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_namespaces: list[str] = []

    class _FakeK8sChannel:
        async def list_namespaces(self) -> list[str]:
            return ["default", "kube-system", "inference", "default", "kube-public", "kube-node-lease"]

    class _FakeScanner:
        def __init__(self, channel: object) -> None:
            assert isinstance(channel, _FakeK8sChannel)

        async def scan(
            self,
            *,
            namespace: str,
            label_selector: str | None = None,
            cluster_name: str = "lab-cluster",
        ) -> tuple[list[OntologyNode], list[OntologyEdge]]:
            assert label_selector is None
            assert cluster_name == "lab-cluster"
            seen_namespaces.append(namespace)
            return ([_node(f"pod:{namespace}:demo", EntityType.K8S_POD, source="k8s")], [])

    monkeypatch.setattr(discovery_module, "_LiveK8sDiscoveryChannel", lambda kubeconfig: _FakeK8sChannel())
    monkeypatch.setattr(discovery_module, "K8sScanner", _FakeScanner)

    nodes, edges, counts = await discovery_module.discover_k8s_workload_snapshot(SREAgentConfig())

    assert seen_namespaces == ["default", "inference", "kube-node-lease", "kube-public"]
    assert {node.id for node in nodes} == {
        "pod:default:demo",
        "pod:inference:demo",
        "pod:kube-node-lease:demo",
        "pod:kube-public:demo",
    }
    assert edges == []
    assert counts["k8s_workload"] == {"nodes": 4, "edges": 0}


@pytest.mark.asyncio
async def test_discover_k8s_workload_snapshot_respects_explicit_namespace_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_namespaces: list[str] = []

    class _FakeK8sChannel:
        async def list_namespaces(self) -> list[str]:
            raise AssertionError("explicit namespace allowlist should skip dynamic namespace discovery")

    class _FakeScanner:
        def __init__(self, channel: object) -> None:
            assert isinstance(channel, _FakeK8sChannel)

        async def scan(
            self,
            *,
            namespace: str,
            label_selector: str | None = None,
            cluster_name: str = "lab-cluster",
        ) -> tuple[list[OntologyNode], list[OntologyEdge]]:
            assert label_selector is None
            assert cluster_name == "lab-cluster"
            seen_namespaces.append(namespace)
            return ([_node(f"pod:{namespace}:demo", EntityType.K8S_POD, source="k8s")], [])

    monkeypatch.setattr(discovery_module, "_LiveK8sDiscoveryChannel", lambda kubeconfig: _FakeK8sChannel())
    monkeypatch.setattr(discovery_module, "K8sScanner", _FakeScanner)

    config = SREAgentConfig.model_validate(
        {
            "ontology": {
                "discovery": {
                    "k8s_namespaces": ["inference", " default ", "inference"],
                }
            }
        }
    )

    nodes, edges, counts = await discovery_module.discover_k8s_workload_snapshot(config)

    assert seen_namespaces == ["inference", "default"]
    assert {node.id for node in nodes} == {"pod:default:demo", "pod:inference:demo"}
    assert edges == []
    assert counts["k8s_workload"] == {"nodes": 2, "edges": 0}
