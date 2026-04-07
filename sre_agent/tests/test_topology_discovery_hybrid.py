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
    ]
    dynamic_edges = [
        OntologyEdge(
            source_id="pod:default:demo",
            target_id="node-a",
            relation=RelationType.HOSTED_ON,
            properties={"namespace": "default"},
        )
    ]

    async def _fake_static(_: SREAgentConfig):
        return static_nodes, static_edges, {"switch": {"nodes": 1, "edges": 0}}

    async def _fake_k8s(_: SREAgentConfig):
        return dynamic_nodes, dynamic_edges, {"k8s_workload": {"nodes": 2, "edges": 1}}

    monkeypatch.setattr(discovery_module, "discover_static_snapshot", _fake_static)
    monkeypatch.setattr(discovery_module, "discover_k8s_workload_snapshot", _fake_k8s)

    nodes, edges, counts, fallback_reason = await discovery_module.discover_hybrid_snapshot(SREAgentConfig())

    assert fallback_reason is None
    assert {node.id for node in nodes} == {"node-a", "pod:default:demo"}
    node_a = next(node for node in nodes if node.id == "node-a")
    assert node_a.properties.get("source") == "lab_seed"
    assert len(edges) == 1
    assert edges[0].relation == RelationType.HOSTED_ON
    assert counts["switch"] == {"nodes": 1, "edges": 0}
    assert counts["k8s_workload"] == {"nodes": 2, "edges": 1}


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
