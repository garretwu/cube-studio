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


def _worker_configs(raw_config: dict[str, Any]) -> list[dict[str, Any]]:
    workers = raw_config.get("inventory", {}).get("workers", [])
    if not isinstance(workers, list) or not workers:
        pytest.skip(f"No workers configured in {_FAULT_INJECTOR_CONFIG}")
    configured = [worker for worker in workers if isinstance(worker, dict) and str(worker.get("name", "")).strip()]
    if not configured:
        pytest.skip(f"No named workers configured in {_FAULT_INJECTOR_CONFIG}")
    return configured


def _switch_configs(raw_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    switches = raw_config.get("switches", {})
    if not isinstance(switches, dict) or not switches:
        pytest.skip(f"No switches configured in {_FAULT_INJECTOR_CONFIG}")
    configured = {
        str(name): value
        for name, value in switches.items()
        if str(name).strip() and isinstance(value, dict)
    }
    if not configured:
        pytest.skip(f"No valid switch mappings configured in {_FAULT_INJECTOR_CONFIG}")
    return configured


def _first_switch_name(raw_config: dict[str, Any]) -> str:
    return sorted(_switch_configs(raw_config))[0]


def _interface_name(interface: Any) -> str:
    return str(
        getattr(interface, "abbreviated_name", "")
        or getattr(interface, "name", "")
        or getattr(interface, "if_index", "unknown")
    )


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
        workers = _worker_configs(raw_config)
        switches = _switch_configs(raw_config)
        worker_names = [str(worker["name"]).strip() for worker in workers]
        representative_worker = worker_names[0]

        graph = OntologyGraph(str(tmp_path / "ontology-scanners-live.db"))
        await graph.connect()
        try:
            await graph.add_nodes([_node(worker_name, EntityType.NODE, source="inventory") for worker_name in worker_names])

            bmc_systems: list[dict[str, Any]] = []
            for worker in workers:
                worker_name = str(worker["name"]).strip()
                redfish_cfg = worker.get("redfish")
                assert isinstance(redfish_cfg, dict), (
                    f"worker={worker_name} is missing redfish configuration in {_FAULT_INJECTOR_CONFIG}"
                )
                bmc_host = str(redfish_cfg.get("bmc_host", "")).strip()
                assert bmc_host, f"worker={worker_name} is missing redfish.bmc_host in {_FAULT_INJECTOR_CONFIG}"

                redfish = RedfishChannel(timeout=int(redfish_cfg.get("timeout", 30)))
                try:
                    auth = await redfish.authenticate(
                        bmc_host,
                        str(redfish_cfg["username"]),
                        str(redfish_cfg["password"]),
                        verify_tls=bool(redfish_cfg.get("verify_tls", True)),
                    )
                    assert auth.success is True, (
                        f"BMC auth failed for worker={worker_name} host={bmc_host}: {auth.error}"
                    )

                    info_result = await redfish.get_bmc_info(
                        bmc_host,
                        verify_tls=bool(redfish_cfg.get("verify_tls", True)),
                    )
                    assert info_result.success is True, (
                        f"BMC info query failed for worker={worker_name} host={bmc_host}: {info_result.error}"
                    )
                    try:
                        redfish_info = json.loads(info_result.output or "{}")
                    except json.JSONDecodeError as exc:
                        raise AssertionError(
                            f"BMC info payload was not valid JSON for worker={worker_name} host={bmc_host}: {exc}"
                        ) from exc
                finally:
                    await redfish.close()

                bmc_systems.append(
                    {
                        "node_id": worker_name,
                        "id": f"bmc:{worker_name}",
                        "name": redfish_info.get("manager", {}).get("name") or f"BMC {worker_name}",
                        "ip": bmc_host,
                        "firmware_version": redfish_info.get("manager", {}).get("firmware_version"),
                        "capabilities": redfish_info.get("manager", {}).get("actions", []),
                    }
                )

            bmc_nodes, bmc_edges = await BMCScanner(channel=object()).scan(bmc_systems)
            assert bmc_nodes, "expected live BMC discovery to return at least one BMC endpoint"
            assert bmc_edges, "expected live BMC discovery to return at least one manages edge"

            k8s_channel = _LiveK8sDiscoveryChannel()
            k8s_nodes, k8s_edges = await K8sScanner(channel=k8s_channel).scan(namespace="default", label_selector=None)
            assert k8s_nodes, "expected live K8s scanner to discover at least one pod"
            assert k8s_edges, "expected live K8s scanner to discover at least one hosted_on edge"
            k8s_node_ids = sorted({edge.target_id for edge in k8s_edges if edge.target_id})
            assert k8s_node_ids, "expected live K8s scanner to resolve at least one node target"

            inventory_node_ids = set(worker_names)
            extra_k8s_nodes = [_node(node_id, EntityType.NODE, source="k8s") for node_id in k8s_node_ids if node_id not in inventory_node_ids]
            if extra_k8s_nodes:
                await graph.add_nodes(extra_k8s_nodes)

            prom_queries = raw_config.get("monitor", {}).get("baseline_queries", {})
            missing_prom_queries = [name for name in ("cpu_util", "memory_util") if not str(prom_queries.get(name, "")).strip()]
            if missing_prom_queries:
                pytest.skip(
                    f"Missing Prometheus baseline queries in {_FAULT_INJECTOR_CONFIG}: {', '.join(missing_prom_queries)}"
                )
            prometheus_url = str(raw_config.get("monitor", {}).get("prometheus_url", "")).strip()
            if not prometheus_url:
                pytest.skip(f"Missing monitor.prometheus_url in {_FAULT_INJECTOR_CONFIG}")

            prom_channel = _LivePrometheusQueryChannel(
                base_url=prometheus_url,
                queries={
                    "cpu_util": prom_queries["cpu_util"],
                    "memory_util": prom_queries["memory_util"],
                },
            )
            try:
                prom_nodes, prom_edges = await PrometheusScanner(channel=prom_channel).scan(
                    {
                        "cpu_util": representative_worker,
                        "memory_util": k8s_node_ids[0],
                    }
                )
            finally:
                await prom_channel.close()
            assert prom_nodes, "expected live Prometheus scanner to return representative metric nodes"
            assert prom_edges, "expected live Prometheus scanner to return representative monitor edges"

            switch_timeout = max(int(config.get("timeout", 30)) for config in switches.values())
            switch_channel = SwitchChannel(
                devices=switches,
                dry_run=False,
                timeout=switch_timeout,
            )
            switch_payloads: list[dict[str, Any]] = []
            try:
                for switch_name, switch_cfg in sorted(switches.items()):
                    interfaces = switch_channel.get_all_interfaces(switch_name)
                    assert interfaces, (
                        "expected live switch channel to return at least one interface "
                        f"for switch={switch_name} host={switch_cfg.get('host')}"
                    )
                    interface = interfaces[0]
                    interface_name = _interface_name(interface)
                    switch_payloads.append(
                        {
                            "id": switch_name,
                            "name": switch_name,
                            "type": switch_cfg.get("type"),
                            "ports": [
                                {
                                    "id": f"{switch_name}:{interface_name}",
                                    "name": interface_name,
                                    "status": getattr(interface, "oper_status", "unknown"),
                                    "connected_to": representative_worker,
                                }
                            ],
                        }
                    )
            finally:
                switch_channel.close()

            switch_nodes, switch_edges = await SwitchScanner(channel=object()).scan(switch_payloads)
            assert switch_nodes, "expected live switch discovery to return switch entities"
            assert switch_edges, "expected live switch discovery to return switch edges"

            await graph.add_nodes(bmc_nodes + k8s_nodes + prom_nodes + switch_nodes)
            await graph.add_edges(bmc_edges + k8s_edges + prom_edges + switch_edges)

            summary = graph.summarize()
            managed_workers = [
                worker_name
                for worker_name in worker_names
                if graph.get_neighbors(worker_name, relation=RelationType.MANAGES)
            ]
            monitored_neighbors = graph.get_neighbors(representative_worker, relation=RelationType.MONITORS)
            first_pod_path = graph.get_path(k8s_nodes[0].id, k8s_edges[0].target_id)
            switch_entities = graph.find_entities(EntityType.SWITCH)
            switch_port_entities = graph.find_entities(EntityType.SWITCH_PORT)

            assert summary["node_count"] > 0
            assert summary["edge_count"] > 0
            assert summary["entity_type_counts"].get("bmc_endpoint", 0) == len(worker_names)
            assert summary["entity_type_counts"].get("k8s_pod", 0) >= 1
            assert summary["entity_type_counts"].get("metric_endpoint", 0) == len(prom_nodes)
            assert summary["entity_type_counts"].get("switch", 0) == len(switches)
            assert summary["entity_type_counts"].get("switch_port", 0) >= len(switches)
            assert managed_workers == worker_names
            assert monitored_neighbors, f"expected ontology graph to retain monitor edges for node={representative_worker}"
            assert first_pod_path, "expected ontology graph to provide at least one pod-to-node path"
            assert switch_entities, "expected ontology graph to contain discovered switch entities"
            assert switch_port_entities, "expected ontology graph to contain discovered switch port entities"
        finally:
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
