from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from sre_agent.models.ontology import EntityType, OntologyNode, RelationType
from sre_agent.ontology.discovery.bmc_scanner import BMCScanner
from sre_agent.ontology.discovery.k8s_scanner import K8sScanner
from sre_agent.ontology.discovery.prometheus_scanner import PrometheusScanner
from sre_agent.ontology.discovery.switch_scanner import SwitchScanner
from sre_agent.ontology.graph import OntologyGraph

from lib.channels.redfish import RedfishChannel
from lib.channels.switch import SwitchChannel

try:  # pragma: no cover - import guard for live-path support
    from kubernetes import client as k8s_client
    from kubernetes import config as k8s_config
except ImportError:  # pragma: no cover - exercised only in envs without kubernetes
    k8s_client = None
    k8s_config = None


_LIVE_ONTOLOGY_ENV = "SRE_AGENT_LIVE_ONTOLOGY"
_FAULT_INJECTOR_CONFIG = Path("fault_injector/fault-injector-test.yaml")


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


class _LivePrometheusQueryChannel:
    def __init__(self, base_url: str, queries: dict[str, str]) -> None:
        from lib.channels.prometheus import PrometheusChannel

        self._channel = PrometheusChannel(base_url=base_url, timeout=15)
        self._queries = dict(queries)

    async def query_instant(self, metric_name: str) -> float:
        return await self._channel.query_instant(self._queries[metric_name])

    async def close(self) -> None:
        await self._channel.close()


class _LiveK8sDiscoveryChannel:
    def __init__(self, kubeconfig: str = "~/.kube/config") -> None:
        if k8s_client is None or k8s_config is None:  # pragma: no cover - env-dependent
            raise RuntimeError("kubernetes python client is not installed")
        self._kubeconfig = kubeconfig

    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        def _fetch() -> list[dict[str, Any]]:
            assert k8s_config is not None
            assert k8s_client is not None
            k8s_config.load_kube_config(config_file=self._kubeconfig)
            api = k8s_client.CoreV1Api()
            pods = api.list_namespaced_pod(namespace, label_selector=label_selector)
            payloads: list[dict[str, Any]] = []
            for pod in pods.items:
                payloads.append(
                    {
                        "metadata": {
                            "name": pod.metadata.name,
                            "labels": dict(pod.metadata.labels or {}),
                        },
                        "status": {"phase": pod.status.phase or "Unknown"},
                        "spec": {"nodeName": pod.spec.node_name},
                    }
                )
            return payloads

        import asyncio

        return await asyncio.to_thread(_fetch)


def _load_fault_injector_test_config() -> dict[str, Any]:
    if not _FAULT_INJECTOR_CONFIG.exists():
        pytest.skip(f"Missing live config: {_FAULT_INJECTOR_CONFIG}")
    payload = yaml.safe_load(_FAULT_INJECTOR_CONFIG.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        pytest.skip(f"Live config is not a mapping: {_FAULT_INJECTOR_CONFIG}")
    return payload


def _worker_config(raw_config: dict[str, Any], worker_name: str = "worker-01") -> dict[str, Any]:
    workers = raw_config.get("inventory", {}).get("workers", [])
    for worker in workers:
        if worker.get("name") == worker_name:
            return worker
    pytest.skip(f"Worker {worker_name!r} not configured in {_FAULT_INJECTOR_CONFIG}")


def _first_switch_name(raw_config: dict[str, Any]) -> str:
    switches = raw_config.get("switches", {})
    if not switches:
        pytest.skip(f"No switches configured in {_FAULT_INJECTOR_CONFIG}")
    return sorted(switches)[0]


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
    @pytest.mark.skipif(
        os.getenv(_LIVE_ONTOLOGY_ENV) != "1",
        reason=f"Set {_LIVE_ONTOLOGY_ENV}=1 to enable live ontology scanner tests.",
    )
    async def test_e2e_loads_all_scanner_outputs_into_graph_when_discovery_happy_path(self, tmp_path: Path) -> None:
        raw_config = _load_fault_injector_test_config()
        worker = _worker_config(raw_config, "worker-01")
        switch_name = _first_switch_name(raw_config)

        graph = OntologyGraph(str(tmp_path / "ontology-scanners-live.db"))
        await graph.connect()

        redfish_cfg = worker["redfish"]
        redfish = RedfishChannel(timeout=int(redfish_cfg.get("timeout", 30)))
        redfish_info: dict[str, Any]
        try:
            auth = await redfish.authenticate(
                redfish_cfg["bmc_host"],
                redfish_cfg["username"],
                redfish_cfg["password"],
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            assert auth.success is True, auth.error

            info_result = await redfish.get_bmc_info(
                redfish_cfg["bmc_host"],
                verify_tls=bool(redfish_cfg.get("verify_tls", True)),
            )
            assert info_result.success is True, info_result.error
            redfish_info = json.loads(info_result.output or "{}")
        finally:
            await redfish.close()

        k8s_channel = _LiveK8sDiscoveryChannel()
        k8s_nodes, k8s_edges = await K8sScanner(channel=k8s_channel).scan(namespace="default", label_selector=None)
        assert k8s_nodes, "expected live K8s scanner to discover at least one pod"
        assert k8s_edges, "expected live K8s scanner to discover at least one hosted_on edge"
        k8s_node_ids = sorted(
            {
                edge.target_id
                for edge in k8s_edges
                if edge.target_id
            }
        )

        prom_queries = raw_config.get("monitor", {}).get("baseline_queries", {})
        prom_channel = _LivePrometheusQueryChannel(
            base_url=str(raw_config.get("monitor", {}).get("prometheus_url", "")).strip(),
            queries={
                "cpu_util": prom_queries["cpu_util"],
                "memory_util": prom_queries["memory_util"],
            },
        )
        try:
            prom_nodes, prom_edges = await PrometheusScanner(channel=prom_channel).scan(
                {
                    "cpu_util": "worker-01",
                    "memory_util": k8s_node_ids[0],
                }
            )
        finally:
            await prom_channel.close()

        switch_channel = SwitchChannel(
            devices=raw_config["switches"],
            dry_run=False,
            timeout=int(raw_config["switches"][switch_name].get("timeout", 30)),
        )
        try:
            interfaces = switch_channel.get_all_interfaces(switch_name)
        finally:
            switch_channel.close()
        assert interfaces, "expected live switch channel to return at least one interface"

        await graph.add_nodes(
            [_node("worker-01", EntityType.NODE, source="inventory")]
            + [_node(node_id, EntityType.NODE, source="k8s") for node_id in k8s_node_ids if node_id != "worker-01"]
        )

        bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan(
            [
                {
                    "node_id": "worker-01",
                    "id": "bmc:worker-01",
                    "name": redfish_info.get("manager", {}).get("name") or "BMC worker-01",
                    "ip": redfish_cfg["bmc_host"],
                    "firmware_version": redfish_info.get("manager", {}).get("firmware_version"),
                    "capabilities": redfish_info.get("manager", {}).get("actions", []),
                }
            ]
        )
        switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan(
            [
                {
                    "id": switch_name,
                    "name": switch_name,
                    "type": raw_config["switches"][switch_name].get("type"),
                    "ports": [
                        {
                            "id": f"{switch_name}:{interfaces[0].abbreviated_name}",
                            "name": interfaces[0].abbreviated_name,
                            "status": interfaces[0].oper_status,
                            "connected_to": "worker-01",
                        }
                    ],
                }
            ]
        )

        await graph.add_nodes(bmc_nodes + k8s_nodes + prom_nodes + switch_nodes)
        await graph.add_edges(bmc_edges + k8s_edges + prom_edges + switch_edges)

        summary = graph.summarize()
        managed_neighbors = graph.get_neighbors("worker-01", relation=RelationType.MANAGES)
        monitored_neighbors = graph.get_neighbors("worker-01", relation=RelationType.MONITORS)
        first_pod_path = graph.get_path(k8s_nodes[0].id, k8s_edges[0].target_id)

        assert summary["entity_type_counts"]["bmc_endpoint"] == 1
        assert summary["entity_type_counts"]["k8s_pod"] >= 1
        assert summary["entity_type_counts"]["metric_endpoint"] == 2
        assert summary["entity_type_counts"]["switch"] == 1
        assert summary["entity_type_counts"]["switch_port"] == 1
        assert summary["edge_count"] == len(bmc_edges + k8s_edges + prom_edges + switch_edges)
        assert [item["entity"].id for item in managed_neighbors] == ["bmc:worker-01"]
        assert {item["entity"].id for item in monitored_neighbors} == {"metric:cpu_util:worker-01"}
        assert first_pod_path == [k8s_nodes[0].id, k8s_edges[0].target_id]

        await graph.close()

    @pytest.mark.asyncio
    async def test_e2e_loads_all_scanner_outputs_into_graph_when_discovery_happy_path_with_fakes(self, tmp_path: Path) -> None:
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
