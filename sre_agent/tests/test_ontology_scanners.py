from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sre_agent.models.ontology import EntityType, OntologyNode, RelationType
from sre_agent.ontology.discovery.bmc_scanner import BMCScanner
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.discovery.prometheus_scanner import PrometheusScanner
from sre_agent.ontology.discovery.switch_scanner import SwitchScanner
from sre_agent.ontology.graph import OntologyGraph


def _node(node_id: str, entity_type: EntityType, **properties: str) -> OntologyNode:
    return OntologyNode(
        id=node_id,
        entity_type=entity_type,
        name=node_id,
        properties=properties,
        updated_at=datetime(2026, 3, 19, 12, 0, tzinfo=UTC),
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


class _FakePrometheusChannel:
    def __init__(self) -> None:
        self.values = {
            "gpu_temperature_celsius": 82.5,
            "vllm_request_latency_p95": 540.0,
        }

    async def query_instant(self, metric_name: str) -> float:
        return self.values[metric_name]


class TestOntologyScannersUnit:
    @pytest.mark.asyncio
    async def test_unit_returns_empty_results_when_bmc_and_switch_inputs_missing(self) -> None:
        bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan()
        switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan()

        assert bmc_nodes == []
        assert bmc_edges == []
        assert switch_nodes == []
        assert switch_edges == []

    @pytest.mark.asyncio
    async def test_unit_builds_switch_nodes_and_edges_when_port_payload_has_connections(self) -> None:
        scanner = SwitchScanner(channel=object())
        nodes, edges = await scanner.scan(
            [
                {
                    "id": "sw-leaf-1",
                    "name": "leaf-1",
                    "vendor": "h3c",
                    "ports": [
                        {
                            "name": "HundredGigE1/0/1",
                            "status": "up",
                            "speed": "100G",
                            "connected_to": "node-a",
                        }
                    ],
                }
            ]
        )

        assert {node.entity_type for node in nodes} == {EntityType.SWITCH, EntityType.SWITCH_PORT}
        assert {edge.relation for edge in edges} == {RelationType.PART_OF, RelationType.CONNECTED_TO}
        assert {edge.target_id for edge in edges} == {"sw-leaf-1", "node-a"}


class TestOntologyScannersIntegration:
    @pytest.mark.asyncio
    async def test_integration_scans_k8s_and_prometheus_when_channels_return_payloads(self) -> None:
        k8s_scanner = K8sScanner(channel=_FakeK8sChannel())
        prom_scanner = PrometheusScanner(channel=_FakePrometheusChannel())

        k8s_nodes, k8s_edges = await k8s_scanner.scan(namespace="infer", label_selector="app=vllm")
        prom_nodes, prom_edges = await prom_scanner.scan(
            {
                "gpu_temperature_celsius": "node-a",
                "vllm_request_latency_p95": "svc-vllm",
            }
        )

        assert len(k8s_nodes) == 1
        assert k8s_nodes[0].entity_type == EntityType.K8S_POD
        assert len(k8s_edges) == 1
        assert k8s_edges[0].relation == RelationType.HOSTED_ON

        assert len(prom_nodes) == 2
        assert all(node.entity_type == EntityType.METRIC_ENDPOINT for node in prom_nodes)
        assert len(prom_edges) == 2
        assert all(edge.relation == RelationType.MONITORS for edge in prom_edges)

    @pytest.mark.asyncio
    async def test_integration_scans_bmc_records_when_systems_provided(self) -> None:
        scanner = BMCScanner(channel=object())
        nodes, edges = await scanner.scan(
            [
                {
                    "node_id": "node-a",
                    "id": "bmc:node-a",
                    "name": "BMC node-a",
                    "ip": "10.0.0.11",
                    "firmware_version": "2.4.1",
                    "capabilities": ["power", "sensor"],
                }
            ]
        )

        assert len(nodes) == 1
        assert nodes[0].entity_type == EntityType.BMC_ENDPOINT
        assert nodes[0].properties["firmware_version"] == "2.4.1"
        assert len(edges) == 1
        assert edges[0].relation == RelationType.MANAGES
        assert edges[0].target_id == "node-a"


class TestOntologyScannersE2E:
    @pytest.mark.asyncio
    async def test_e2e_loads_all_scanner_outputs_into_graph_when_discovery_happy_path(self, tmp_path: Path) -> None:
        graph = OntologyGraph(str(tmp_path / "ontology-scanners.db"))
        await graph.connect()
        await graph.add_nodes(
            [
                _node("node-a", EntityType.NODE, rack="rack-a"),
                _node("svc-vllm", EntityType.INFERENCE_SERVICE, namespace="infer"),
            ]
        )

        bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan(
            [
                {
                    "node_id": "node-a",
                    "id": "bmc:node-a",
                    "name": "BMC node-a",
                    "ip": "10.0.0.11",
                }
            ]
        )
        k8s_nodes, k8s_edges = await K8sScanner(channel=_FakeK8sChannel()).scan(namespace="infer", label_selector="app=vllm")
        prom_nodes, prom_edges = await PrometheusScanner(channel=_FakePrometheusChannel()).scan(
            {
                "gpu_temperature_celsius": "node-a",
                "vllm_request_latency_p95": "svc-vllm",
            }
        )
        switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan(
            [
                {
                    "id": "sw-leaf-1",
                    "name": "leaf-1",
                    "ports": [{"name": "HundredGigE1/0/1", "connected_to": "node-a"}],
                }
            ]
        )

        await graph.add_nodes(bmc_nodes + k8s_nodes + prom_nodes + switch_nodes)
        await graph.add_edges(bmc_edges + k8s_edges + prom_edges + switch_edges)

        summary = graph.summarize()
        managed_neighbors = graph.get_neighbors("node-a", relation=RelationType.MANAGES)
        monitored_neighbors = graph.get_neighbors("node-a", relation=RelationType.MONITORS)
        pod_path = graph.get_path("pod:infer:vllm-0", "node-a")

        assert summary["node_count"] == 8
        assert summary["edge_count"] == 6
        assert summary["entity_type_counts"] == {
            "bmc_endpoint": 1,
            "inference_service": 1,
            "k8s_pod": 1,
            "metric_endpoint": 2,
            "node": 1,
            "switch": 1,
            "switch_port": 1,
        }
        assert [item["entity"].id for item in managed_neighbors] == ["bmc:node-a"]
        assert {item["entity"].id for item in monitored_neighbors} == {"metric:gpu_temperature_celsius:node-a"}
        assert pod_path == ["pod:infer:vllm-0", "node-a"]

        await graph.close()

    @pytest.mark.asyncio
    async def test_e2e_preserves_base_graph_when_all_scanners_return_empty_inputs(self) -> None:
        graph = OntologyGraph()
        await graph.connect()
        await graph.add_entity(_node("node-a", EntityType.NODE))

        bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan()
        switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan()
        prom_nodes, prom_edges = await PrometheusScanner(channel=_FakePrometheusChannel()).scan({})

        await graph.add_nodes(bmc_nodes + switch_nodes + prom_nodes)
        await graph.add_edges(bmc_edges + switch_edges + prom_edges)

        assert graph.summarize() == {
            "node_count": 1,
            "edge_count": 0,
            "entity_type_counts": {"node": 1},
        }
        assert graph.get_neighbors("node-a") == []
        assert graph.get_path("node-a", "missing-node") is None

        await graph.close()
