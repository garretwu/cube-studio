from __future__ import annotations

import asyncio
from pathlib import Path

from sre_agent.models.ontology import EntityType, OntologyEdge, OntologyNode, RelationType
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.graph import OntologyGraph

try:
    import networkx as nx  # type: ignore
except ImportError:
    from sre_agent._compat import networkx as nx


class _FakeK8sChannel:
    async def list_pods(self, namespace: str, label_selector: str | None = None):
        _ = label_selector
        return [
            {
                "metadata": {"name": "vllm-0", "labels": {"app": "vllm"}},
                "status": {"phase": "Running"},
                "spec": {"nodeName": "node-a"},
            }
        ]


def test_ontology_graph_queries_and_persistence(tmp_path: Path) -> None:
    async def _run() -> None:
        db_path = str(tmp_path / "ontology.db")
        graph = OntologyGraph(db_path=db_path)
        await graph.connect()
        assert isinstance(graph.graph, nx.DiGraph)
        await graph.add_node(OntologyNode(id="node-a", entity_type=EntityType.NODE, name="node-a", status="online"))
        await graph.add_node(OntologyNode(id="svc-a", entity_type=EntityType.INFERENCE_SERVICE, name="svc-a"))
        await graph.add_edge(OntologyEdge(source_id="svc-a", target_id="node-a", relation=RelationType.HOSTED_ON))

        assert graph.get_entity("node-a") is not None
        assert graph.get_path("svc-a", "node-a") == ["svc-a", "node-a"]
        neighbors = graph.get_neighbors("node-a")
        assert len(neighbors) == 1
        assert neighbors[0]["entity"].id == "svc-a"
        blast = graph.get_blast_radius("node-a")
        assert blast["affected_count"] == 1
        await graph.close()

        restored = OntologyGraph(db_path=db_path)
        await restored.connect()
        assert restored.get_entity("svc-a") is not None
        await restored.close()

    asyncio.run(_run())


def test_ontology_blast_radius_respects_documented_direction_rules() -> None:
    async def _run() -> None:
        graph = OntologyGraph(db_path=":memory:")
        await graph.connect()
        await graph.add_node(OntologyNode(id="node-a", entity_type=EntityType.NODE, name="node-a"))
        await graph.add_node(OntologyNode(id="pod-a", entity_type=EntityType.K8S_POD, name="pod-a"))
        await graph.add_node(OntologyNode(id="svc-a", entity_type=EntityType.INFERENCE_SERVICE, name="svc-a"))
        await graph.add_node(OntologyNode(id="gpu-a", entity_type=EntityType.GPU, name="gpu-a"))
        await graph.add_edge(OntologyEdge(source_id="pod-a", target_id="node-a", relation=RelationType.HOSTED_ON))
        await graph.add_edge(OntologyEdge(source_id="svc-a", target_id="pod-a", relation=RelationType.SERVES))
        await graph.add_edge(OntologyEdge(source_id="gpu-a", target_id="node-a", relation=RelationType.PART_OF))

        blast = graph.get_blast_radius("node-a")
        affected_ids = {node.id for node in blast["affected_entities"]}
        assert affected_ids == {"pod-a", "svc-a"}
        assert "gpu-a" not in affected_ids
        await graph.close()

    asyncio.run(_run())


def test_k8s_scanner_builds_nodes_and_edges() -> None:
    async def _run() -> None:
        scanner = K8sScanner(_FakeK8sChannel())
        nodes, edges = await scanner.scan(namespace="infer")
        assert nodes[0].entity_type == EntityType.K8S_POD
        assert edges[0].relation == RelationType.HOSTED_ON
        assert edges[0].target_id == "node-a"

    asyncio.run(_run())
