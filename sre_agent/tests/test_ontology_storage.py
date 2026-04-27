from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.graph import OntologyGraph

try:
    import networkx as nx  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import networkx as nx


def _node(node_id: str, entity_type: EntityType, **properties: str) -> OntologyNode:
    return OntologyNode(
        id=node_id,
        entity_type=entity_type,
        name=node_id,
        properties=properties,
        updated_at=datetime(2026, 3, 18, 12, 0, tzinfo=UTC),
    )


class _FakeK8sChannel:
    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, object]]:
        assert namespace == "infer"
        assert label_selector == "app=vllm"
        return [
            {
                "metadata": {"name": "vllm-0", "labels": {"app": "vllm"}},
                "status": {"phase": "Running"},
                "spec": {"nodeName": "node-a"},
            }
        ]


class TestOntologyStorageUnit:
    @pytest.mark.asyncio
    async def test_unit_returns_none_when_path_or_entity_missing(self) -> None:
        graph = OntologyGraph()
        await graph.connect()
        await graph.add_entity(_node("node-a", EntityType.NODE))

        assert graph.get_entity("missing-node") is None
        assert graph.get_path("node-a", "missing-node") is None

        await graph.close()

    @pytest.mark.asyncio
    async def test_unit_propagates_blast_radius_when_document_relations_match(self) -> None:
        graph = OntologyGraph()
        await graph.connect()
        await graph.add_nodes(
            [
                _node("service-a", EntityType.INFERENCE_SERVICE),
                _node("pod-a", EntityType.K8S_POD),
                _node("node-a", EntityType.NODE),
                _node("gpu-a", EntityType.GPU),
                _node("switch-a", EntityType.SWITCH),
            ]
        )
        await graph.add_edges(
            [
                OntologyEdge(source_id="service-a", target_id="pod-a", relation=RelationType.SERVES),
                OntologyEdge(source_id="pod-a", target_id="node-a", relation=RelationType.HOSTED_ON),
                OntologyEdge(source_id="node-a", target_id="gpu-a", relation=RelationType.PART_OF),
                OntologyEdge(source_id="switch-a", target_id="node-a", relation=RelationType.CONNECTED_TO),
            ]
        )

        radius = graph.get_blast_radius("node-a", max_depth=2)
        affected_ids = {entity.id for entity in radius["affected_entities"]}

        assert affected_ids == {"pod-a", "service-a", "gpu-a"}
        assert isinstance(graph.graph, nx.DiGraph)

        await graph.close()

    @pytest.mark.asyncio
    async def test_unit_filters_blast_radius_when_allowlist_namespace_and_budget_are_set(self) -> None:
        graph = OntologyGraph()
        await graph.connect()
        await graph.add_nodes(
            [
                _node("service-a", EntityType.INFERENCE_SERVICE, namespace="infer"),
                _node("pod-a", EntityType.K8S_POD, namespace="infer"),
                _node("node-a", EntityType.NODE, namespace="infer"),
                _node("gpu-a", EntityType.GPU, namespace="infer"),
                _node("service-infra", EntityType.INFERENCE_SERVICE, namespace="kube-system"),
            ]
        )
        await graph.add_edges(
            [
                OntologyEdge(source_id="service-a", target_id="pod-a", relation=RelationType.SERVES),
                OntologyEdge(source_id="pod-a", target_id="node-a", relation=RelationType.HOSTED_ON),
                OntologyEdge(source_id="node-a", target_id="gpu-a", relation=RelationType.PART_OF),
                OntologyEdge(source_id="service-infra", target_id="node-a", relation=RelationType.HOSTED_ON),
            ]
        )

        filtered = graph.get_blast_radius(
            "node-a",
            max_depth=2,
            relation_allowlist=[RelationType.SERVES, RelationType.HOSTED_ON],
            entity_type_allowlist=[EntityType.INFERENCE_SERVICE, EntityType.K8S_POD],
            namespace_scope="infer",
            max_entities=1,
        )
        filtered_ids = [entity.id for entity in filtered["affected_entities"]]

        assert filtered["raw_count"] >= 2
        assert filtered["filtered_count"] == 1
        assert filtered["affected_count"] == 1
        assert filtered_ids == ["pod-a"]
        assert filtered["dropped_by_policy"]["relation_filtered"] >= 1
        assert filtered["dropped_by_policy"]["namespace_filtered"] >= 1
        assert filtered["dropped_by_policy"]["budget_filtered"] >= 1

        await graph.close()


class TestOntologyStorageIntegration:
    @pytest.mark.asyncio
    async def test_integration_persists_and_restores_graph_when_sqlite_round_trip(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ontology.db"
        writer = OntologyGraph(str(db_path))
        await writer.connect()
        await writer.add_entity(_node("node-a", EntityType.NODE, rack="rack-1"))
        await writer.add_entity(_node("gpu-a", EntityType.GPU, slot="0"))
        await writer.add_relationship(
            OntologyEdge(source_id="gpu-a", target_id="node-a", relation=RelationType.PART_OF, properties={"slot": "0"})
        )
        await writer.close()

        reader = OntologyGraph(str(db_path))
        await reader.connect()

        assert reader.get_entity("node-a") is not None
        assert reader.find_entities(entity_type=EntityType.GPU)[0].id == "gpu-a"
        assert reader.get_path("gpu-a", "node-a") == ["gpu-a", "node-a"]

        await reader.close()

    @pytest.mark.asyncio
    async def test_integration_builds_nodes_and_edges_when_scanner_returns_pod_payload(self) -> None:
        scanner = K8sScanner(channel=_FakeK8sChannel())
        nodes, edges = await scanner.scan(namespace="infer", label_selector="app=vllm")

        assert {node.entity_type for node in nodes} == {EntityType.K8S_POD, EntityType.K8S_CLUSTER}
        assert any(node.id == "pod:infer:vllm-0" for node in nodes)
        assert any(node.id == "k8s:lab-cluster" for node in nodes)
        assert len(edges) == 3
        assert {edge.relation for edge in edges} == {RelationType.HOSTED_ON, RelationType.PART_OF}
        assert any(edge.relation == RelationType.HOSTED_ON and edge.target_id == "node-a" for edge in edges)


class TestOntologyStorageE2E:
    @pytest.mark.asyncio
    async def test_e2e_persists_and_queries_topology_when_scanner_output_loaded(self, tmp_path: Path) -> None:
        db_path = tmp_path / "ontology-e2e.db"
        graph = OntologyGraph(str(db_path))
        await graph.connect()

        scanner = K8sScanner(channel=_FakeK8sChannel())
        nodes, edges = await scanner.scan(namespace="infer", label_selector="app=vllm")
        await graph.add_nodes(nodes + [_node("node-a", EntityType.NODE, zone="az-1")])
        await graph.add_edges(edges)

        summary = graph.summarize()
        neighbors = graph.get_neighbors("node-a", relation=RelationType.HOSTED_ON)

        assert summary["node_count"] == 3
        assert summary["edge_count"] == 3
        assert summary["entity_type_counts"] == {"k8s_cluster": 1, "k8s_pod": 1, "node": 1}
        assert [item["entity"].id for item in neighbors] == ["pod:infer:vllm-0"]

        await graph.close()

    @pytest.mark.asyncio
    async def test_e2e_returns_empty_results_when_no_matching_entity_or_path(self) -> None:
        graph = OntologyGraph()
        await graph.connect()
        await graph.add_entity(_node("isolated-node", EntityType.NODE))

        assert graph.find_entities(entity_type=EntityType.GPU) == []
        assert graph.get_neighbors("isolated-node") == []
        assert graph.get_path("isolated-node", "missing-node") is None

        await graph.close()
