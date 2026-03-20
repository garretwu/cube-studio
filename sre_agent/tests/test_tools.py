from __future__ import annotations

import datetime as dt
import unittest
from dataclasses import dataclass
from typing import Any

from sre_agent.tools import (
    SafetyLevel,
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolRegistryError,
    build_default_registry,
)


@dataclass
class _FakeChannelResult:
    success: bool = True
    data: Any = None
    output: str = ""
    error: str = ""


class _FakeK8sChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        self.calls.append({"action": "list_pods", "namespace": namespace, "label_selector": label_selector})
        return [{"namespace": namespace, "name": "pod-a"}]

    async def get_pod_status(self, namespace: str, pod_name: str) -> str:
        self.calls.append({"action": "get_pod_status", "namespace": namespace, "pod_name": pod_name})
        return "Running"

    async def count_pending_pods(self, namespace: str, label_selector: str | None = None) -> int:
        self.calls.append({"action": "count_pending_pods", "namespace": namespace, "label_selector": label_selector})
        return 2

    async def count_oomkilled_pods(self, namespace: str, label_selector: str | None = None) -> int:
        self.calls.append({"action": "count_oomkilled_pods", "namespace": namespace, "label_selector": label_selector})
        return 1

    async def delete_pod(self, label_selector: str, namespace: str = "default", dry_run: bool | None = None) -> dict[str, Any]:
        self.calls.append({"action": "delete_pod", "label_selector": label_selector, "namespace": namespace})
        return {"deleted": label_selector, "namespace": namespace, "dry_run": bool(dry_run)}

    async def scale_deployment(self, name: str, namespace: str, replicas: int, dry_run: bool | None = None) -> dict[str, Any]:
        self.calls.append({"action": "scale_deployment", "name": name, "namespace": namespace, "replicas": replicas})
        return {"name": name, "namespace": namespace, "replicas": replicas, "dry_run": bool(dry_run)}

    async def execute(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"action": action, "params": params})
        return {"action": action, "params": params}


class _FakeLogChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def read_pod_logs(
        self,
        pod: str,
        namespace: str,
        *,
        tail: int = 200,
        since: str | None = None,
    ) -> list[str]:
        self.calls.append({"pod": pod, "namespace": namespace, "tail": tail, "since": since})
        return ["line-1", "line-2"]


class _FakePrometheusChannel:
    async def query_instant(self, promql: str) -> float:
        return 1.0 if promql else 0.0

    async def query_range(self, promql: str, start: Any, end: Any, *, step: str = "15s") -> list[tuple[float, float]]:
        _ = promql, start, end, step
        return [(1.0, 2.0)]


class _FakeSSHChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_command(self, node: str, command: str, use_sudo: bool = False) -> _FakeChannelResult:
        self.calls.append({"node": node, "command": command, "use_sudo": use_sudo})
        return _FakeChannelResult(output=f"{node}:{command}")


class _FailingSSHChannel:
    async def run_command(self, node: str, command: str, use_sudo: bool = False) -> _FakeChannelResult:
        _ = node, command, use_sudo
        return _FakeChannelResult(success=False, error="ssh command failed")


class _FakeSwitchChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action: str, params: dict[str, Any]) -> _FakeChannelResult:
        self.calls.append({"action": action, "params": params})
        return _FakeChannelResult(output=f"{action}:{params}")

    def bringup_port(self, switch: str, interface: str) -> _FakeChannelResult:
        self.calls.append({"action": "bringup_port", "switch": switch, "interface": interface})
        return _FakeChannelResult(output=f"up:{switch}:{interface}")

    def shutdown_port(self, switch: str, interface: str) -> _FakeChannelResult:
        self.calls.append({"action": "shutdown_port", "switch": switch, "interface": interface})
        return _FakeChannelResult(output=f"down:{switch}:{interface}")

    def apply_raw_config(self, switch: str, config_xml: str) -> _FakeChannelResult:
        self.calls.append({"action": "apply_raw_config", "switch": switch, "config_xml": config_xml})
        return _FakeChannelResult(output=f"config:{switch}")


class _FailingSwitchChannel:
    def bringup_port(self, switch: str, interface: str) -> _FakeChannelResult:
        _ = switch, interface
        return _FakeChannelResult(success=False, error="port enable failed")


class _FakeOntologyChannel:
    async def query(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [{"entity_type": entity_type, "filters": filters or {}}]

    async def get_path(self, from_id: str, to_id: str) -> list[str]:
        return [from_id, "mid", to_id]

    async def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        return {"entity_id": entity_id, "count": 3}


class _FakeMemoryStore:
    async def search_incidents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return [{"query": query, "kind": "incident", "limit": limit}]

    async def search_patterns(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        return [{"query": query, "kind": "pattern", "limit": limit}]

    async def get_config_baseline(self, aidc_id: str) -> dict[str, Any]:
        return {"aidc_id": aidc_id, "baseline": {"max_canary_pct": 0.1}}


class _FakeRemediationEngine:
    async def execute_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {"executed": True, "plan": plan}


class _FakeRedfishChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action: str, params: dict[str, Any]) -> _FakeChannelResult:
        self.calls.append({"action": action, "params": params})
        return _FakeChannelResult(data={"action": action, "params": params})


class _FailingRedfishChannel:
    async def execute(self, action: str, params: dict[str, Any]) -> _FakeChannelResult:
        _ = params
        return _FakeChannelResult(success=False, error=f"Unknown action: {action}")


class _ExecuteOnlyK8sChannel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, action: str, params: dict[str, Any]) -> _FakeChannelResult:
        self.calls.append({"action": action, "params": params})
        if action == "list_pods":
            return _FakeChannelResult(success=False, error="Unknown action: list_pods")
        if action == "get_pods":
            return _FakeChannelResult(output="pod-a\npod-b")
        return _FakeChannelResult(success=False, error=f"Unknown action: {action}")


class _UnsupportedK8sWriteChannel:
    async def execute(self, action: str, params: dict[str, Any]) -> _FakeChannelResult:
        _ = params
        return _FakeChannelResult(success=False, error=f"Unknown action: {action}")


class _IntegrationK8sClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_pods(self, namespace: str, label_selector: str | None = None) -> list[dict[str, Any]]:
        self.calls.append({"action": "list_pods", "namespace": namespace, "label_selector": label_selector})
        return [
            {"name": "pod-a", "status": {"phase": "Pending", "containerStatuses": []}},
            {
                "name": "pod-b",
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"state": {"terminated": {"reason": "OOMKilled"}}, "lastState": {}}],
                },
            },
        ]

    def get_pod(self, namespace: str, pod_name: str) -> dict[str, Any]:
        self.calls.append({"action": "get_pod", "namespace": namespace, "pod_name": pod_name})
        return {"name": pod_name, "status": {"phase": "Running"}}

    def delete_pods(self, namespace: str, label_selector: str) -> dict[str, Any]:
        self.calls.append({"action": "delete_pods", "namespace": namespace, "label_selector": label_selector})
        return {"deleted": True, "namespace": namespace, "label_selector": label_selector}

    def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        self.calls.append({"action": "get_deployment", "namespace": namespace, "name": name})
        return {"spec": {"replicas": 2}}

    def scale_deployment(self, namespace: str, name: str, replicas: int) -> dict[str, Any]:
        self.calls.append({"action": "scale_deployment", "namespace": namespace, "name": name, "replicas": replicas})
        return {"scaled": True, "namespace": namespace, "name": name, "replicas": replicas}


class _IntegrationLokiBackend:
    async def query(self, query: str, limit: int = 200, direction: str = "backward") -> dict[str, Any]:
        _ = query, limit, direction
        return {"data": {"result": []}}

    async def query_range(
        self,
        query: str,
        start: Any,
        end: Any,
        step: str = "30s",
        limit: int = 200,
        direction: str = "backward",
    ) -> dict[str, Any]:
        _ = query, start, end, step, limit, direction
        return {
            "data": {
                "result": [
                    {"stream": {"pod": "pod-a"}, "values": [["1", "line-1"], ["2", "line-2"]]},
                ]
            }
        }


class _IntegrationOntologyBackend:
    async def find_entities(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return [{"entity_type": entity_type, "filters": filters or {}, "id": "svc-1"}]

    async def get_path(self, from_id: str, to_id: str) -> list[str]:
        return [from_id, "switch-1", to_id]

    async def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        return {"entity_id": entity_id, "affected": 3}


class TestToolRegistryUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_register_duplicate_rejected(self) -> None:
        registry = ToolRegistry()

        async def _handler(params: dict[str, Any], context: ToolExecutionContext) -> Any:
            _ = context
            return params

        definition = ToolDefinition(
            name="prometheus.query",
            description="query",
            safety_level=SafetyLevel.READ_ONLY,
        )
        registry.register(definition, _handler)
        with self.assertRaises(ToolRegistryError):
            registry.register(definition, _handler)

    async def test_unit_list_and_filter_by_safety_level(self) -> None:
        registry = build_default_registry()
        readonly_names = {tool.name for tool in registry.get_tools_by_level(SafetyLevel.READ_ONLY)}
        critical_names = {tool.name for tool in registry.get_tools_by_level(SafetyLevel.CRITICAL)}
        critical_names_by_str = {tool.name for tool in registry.get_tools_by_level("critical")}
        self.assertIn("k8s.list_pods", readonly_names)
        self.assertIn("network.set_bmc_vlan", critical_names)
        self.assertNotIn("network.set_bmc_vlan", readonly_names)
        self.assertEqual(critical_names, critical_names_by_str)

    async def test_unit_enable_disable(self) -> None:
        registry = build_default_registry()
        self.assertTrue(registry.is_enabled("k8s.list_pods"))
        registry.disable_tool("k8s.list_pods")
        self.assertFalse(registry.is_enabled("k8s.list_pods"))

        result = await registry.execute(
            "k8s.list_pods",
            {"namespace": "default"},
            ToolExecutionContext(channels={"k8s": _FakeK8sChannel()}),
        )
        self.assertFalse(result.success)
        self.assertIn("disabled", result.error)

        registry.enable_tool("k8s.list_pods")
        result2 = await registry.execute(
            "k8s.list_pods",
            {"namespace": "default"},
            ToolExecutionContext(channels={"k8s": _FakeK8sChannel()}),
        )
        self.assertTrue(result2.success)

    async def test_unit_execute_dispatches_to_handler(self) -> None:
        registry = build_default_registry()
        result = await registry.execute(
            "k8s.list_pods",
            {"namespace": "default"},
            ToolExecutionContext(channels={"k8s": _FakeK8sChannel()}),
        )
        self.assertTrue(result.success)
        self.assertEqual(result.data[0]["name"], "pod-a")

    async def test_unit_bmc_write_hard_blocked_without_explicit_allow(self) -> None:
        registry = build_default_registry()
        redfish = _FakeRedfishChannel()
        result = await registry.execute(
            "network.set_bmc_vlan",
            {"bmc_host": "10.0.0.1", "vlan_id": "100"},
            ToolExecutionContext(channels={"redfish": redfish}, write_approved=True),
        )
        self.assertFalse(result.success)
        self.assertIn("hard-blocked", result.error)
        self.assertEqual(redfish.calls, [])

    async def test_unit_bmc_write_allowed_with_approval_and_explicit_flag(self) -> None:
        registry = build_default_registry()
        redfish = _FakeRedfishChannel()
        result = await registry.execute(
            "network.set_bmc_mtu",
            {"bmc_host": "10.0.0.2", "mtu": "9000"},
            ToolExecutionContext(
                channels={"redfish": redfish},
                write_approved=True,
                metadata={"allow_bmc_network_write": True},
            ),
        )
        self.assertTrue(result.success)
        self.assertEqual(redfish.calls[0]["action"], "set_mtu")

    async def test_unit_channel_failure_propagates_for_read_tool(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(channels={"ssh": _FailingSSHChannel()})
        result = await registry.execute("network.get_rdma_stats", {"node": "gpu-1-1"}, context)
        self.assertFalse(result.success)
        self.assertIn("ssh command failed", result.error)

    async def test_unit_channel_failure_propagates_for_write_tool(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={"switch": _FailingSwitchChannel()},
            write_approved=True,
        )
        result = await registry.execute(
            "network.switch_port_enable",
            {"switch": "sw-1", "interface": "GE1/0/1"},
            context,
        )
        self.assertFalse(result.success)
        self.assertIn("port enable failed", result.error)

    async def test_unit_redfish_unsupported_actions_fail_fast(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={"redfish": _FailingRedfishChannel()},
            write_approved=True,
            metadata={"allow_bmc_network_write": True},
        )
        result = await registry.execute(
            "network.set_bmc_vlan",
            {"bmc_host": "10.0.0.7", "vlan_id": "100"},
            context,
        )
        self.assertFalse(result.success)
        self.assertIn("does not support set_vlan", result.error)

    async def test_unit_k8s_read_tools_fallback_to_execute_get_pods(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(channels={"k8s": _ExecuteOnlyK8sChannel()})

        list_result = await registry.execute("k8s.list_pods", {"namespace": "default"}, context)
        self.assertTrue(list_result.success)
        self.assertEqual([item["name"] for item in list_result.data], ["pod-a", "pod-b"])

        describe_result = await registry.execute(
            "k8s.describe_pod",
            {"namespace": "default", "pod_name": "pod-a"},
            context,
        )
        self.assertTrue(describe_result.success)
        self.assertEqual(describe_result.data["status"], "Unknown")

        pending_result = await registry.execute("k8s.top_pending", {"namespace": "default"}, context)
        self.assertFalse(pending_result.success)
        self.assertIn("capability gap", pending_result.error)

        oom_result = await registry.execute("k8s.top_oomkilled", {"namespace": "default"}, context)
        self.assertFalse(oom_result.success)
        self.assertIn("capability gap", oom_result.error)

    async def test_unit_k8s_write_unsupported_action_fails_fast(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={"k8s": _UnsupportedK8sWriteChannel()},
            write_approved=True,
        )
        result = await registry.execute(
            "k8s.apply_manifest",
            {"manifest": {"apiVersion": "v1", "kind": "Pod"}},
            context,
        )
        self.assertFalse(result.success)
        self.assertIn("does not support apply_manifest", result.error)

    async def test_unit_handler_calls_expected_channels(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={
                "k8s": _FakeK8sChannel(),
                "log": _FakeLogChannel(),
                "prometheus": _FakePrometheusChannel(),
                "ssh": _FakeSSHChannel(),
                "switch": _FakeSwitchChannel(),
                "ontology": _FakeOntologyChannel(),
                "memory": _FakeMemoryStore(),
                "remediation": _FakeRemediationEngine(),
                "redfish": _FakeRedfishChannel(),
            },
            write_approved=True,
            metadata={"allow_bmc_network_write": True},
        )

        results = [
            await registry.execute("k8s.describe_pod", {"namespace": "default", "pod_name": "pod-a"}, context),
            await registry.execute("k8s.read_pod_logs", {"namespace": "default", "pod_name": "pod-a"}, context),
            await registry.execute("prometheus.query_instant", {"promql": "up"}, context),
            await registry.execute("gpu.get_metrics", {"node": "gpu-1-1"}, context),
            await registry.execute("network.get_switch_port_counters", {"switch": "sw-1", "interface": "GE1/0/1"}, context),
            await registry.execute("ontology.path", {"from_id": "a", "to_id": "b"}, context),
            await registry.execute("memory.search_patterns", {"query": "gpu timeout"}, context),
            await registry.execute("remediation.execute_plan", {"plan": {"id": "p-1"}}, context),
            await registry.execute("network.switch_port_enable", {"switch": "sw-1", "interface": "GE1/0/1"}, context),
        ]
        self.assertTrue(all(item.success for item in results))


class TestToolRegistryContract(unittest.IsolatedAsyncioTestCase):
    async def test_contract_fake_channel_bundle_is_sufficient(self) -> None:
        registry = build_default_registry()
        context = ToolExecutionContext(channels={"memory": _FakeMemoryStore()})
        result = await registry.execute("memory.search_incidents", {"query": "nccl timeout", "limit": 3}, context)
        self.assertTrue(result.success)
        self.assertEqual(result.data[0]["kind"], "incident")

    async def test_integration_graph_facing_readonly_export_and_remediation_path(self) -> None:
        registry = build_default_registry()
        desc = registry.get_tool_descriptions(safety_levels=[SafetyLevel.READ_ONLY])
        self.assertIn("k8s.list_pods", desc)
        self.assertNotIn("network.set_bmc_vlan", desc)

        redfish = _FakeRedfishChannel()
        result = await registry.execute(
            "network.set_bmc_vlan",
            {"bmc_host": "10.0.0.5", "vlan_id": "200"},
            ToolExecutionContext(
                channels={"redfish": redfish},
                write_approved=True,
                metadata={"allow_bmc_network_write": True},
            ),
        )
        self.assertTrue(result.success)
        self.assertEqual(redfish.calls[0]["action"], "set_vlan")


class TestToolRegistryIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_registry_with_real_k8s_channel(self) -> None:
        from lib.channels.kubernetes import K8sChannel

        registry = build_default_registry()
        k8s_client = _IntegrationK8sClient()
        k8s_channel = K8sChannel(client=k8s_client)
        context = ToolExecutionContext(channels={"k8s": k8s_channel}, write_approved=True)

        list_result = await registry.execute("k8s.list_pods", {"namespace": "default"}, context)
        self.assertTrue(list_result.success)
        self.assertEqual(len(list_result.data), 2)

        describe_result = await registry.execute(
            "k8s.describe_pod",
            {"namespace": "default", "pod_name": "pod-a"},
            context,
        )
        self.assertTrue(describe_result.success)
        self.assertEqual(describe_result.data["status"], "Running")

        pending_result = await registry.execute("k8s.top_pending", {"namespace": "default"}, context)
        self.assertTrue(pending_result.success)
        self.assertEqual(pending_result.data, 1)

        oom_result = await registry.execute("k8s.top_oomkilled", {"namespace": "default"}, context)
        self.assertTrue(oom_result.success)
        self.assertEqual(oom_result.data, 1)

        delete_result = await registry.execute(
            "k8s.delete_pod",
            {"namespace": "default", "label_selector": "app=test"},
            context,
        )
        self.assertTrue(delete_result.success)
        self.assertTrue(delete_result.data["deleted"])

        scale_result = await registry.execute(
            "k8s.scale_deployment",
            {"namespace": "default", "name": "svc", "replicas": 5},
            context,
        )
        self.assertTrue(scale_result.success)
        self.assertEqual(scale_result.data["replicas"], 5)

    async def test_integration_registry_with_real_log_prom_ontology_channels(self) -> None:
        from lib.channels.log import LogChannel
        from lib.channels.ontology import OntologyChannel
        from lib.channels.prometheus import PrometheusChannel

        class _IntegrationPrometheusChannel(PrometheusChannel):
            def _http_get_json(self, url: str) -> dict[str, Any]:
                if "query_range" in url:
                    return {"data": {"result": [{"values": [[1.0, "2"], [2.0, "3"]]}]}}
                return {"data": {"result": [{"value": [1.0, "5.0"]}]}}

        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={
                "log": LogChannel(loki=_IntegrationLokiBackend()),
                "prometheus": _IntegrationPrometheusChannel(base_url="http://prom"),
                "ontology": OntologyChannel(ontology=_IntegrationOntologyBackend()),
            }
        )

        logs_result = await registry.execute(
            "k8s.read_pod_logs",
            {"namespace": "default", "pod_name": "pod-a", "tail": 2},
            context,
        )
        self.assertTrue(logs_result.success)
        self.assertEqual(logs_result.data, ["line-1", "line-2"])

        instant_result = await registry.execute(
            "prometheus.query_instant",
            {"promql": "up"},
            context,
        )
        self.assertTrue(instant_result.success)
        self.assertEqual(instant_result.data, 5.0)

        now = dt.datetime.now()
        range_result = await registry.execute(
            "prometheus.query_range",
            {"promql": "up", "start": now, "end": now + dt.timedelta(minutes=1), "step": "15s"},
            context,
        )
        self.assertTrue(range_result.success)
        self.assertEqual(range_result.data, [(1.0, 2.0), (2.0, 3.0)])

        path_result = await registry.execute(
            "ontology.path",
            {"from_id": "node-a", "to_id": "node-b"},
            context,
        )
        self.assertTrue(path_result.success)
        self.assertEqual(path_result.data, ["node-a", "switch-1", "node-b"])


class TestToolRegistryE2E(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_mocked_incident_workflow(self) -> None:
        from lib.channels.kubernetes import K8sChannel
        from lib.channels.log import LogChannel
        from lib.channels.ontology import OntologyChannel
        from lib.channels.prometheus import PrometheusChannel

        class _IntegrationPrometheusChannel(PrometheusChannel):
            def _http_get_json(self, url: str) -> dict[str, Any]:
                if "query_range" in url:
                    return {"data": {"result": [{"values": [[1.0, "2"]]}]}}
                return {"data": {"result": [{"value": [1.0, "1.2"]}]}}

        registry = build_default_registry()
        context = ToolExecutionContext(
            channels={
                "k8s": K8sChannel(client=_IntegrationK8sClient()),
                "log": LogChannel(loki=_IntegrationLokiBackend()),
                "prometheus": _IntegrationPrometheusChannel(base_url="http://prom"),
                "ontology": OntologyChannel(ontology=_IntegrationOntologyBackend()),
                "memory": _FakeMemoryStore(),
            },
            write_approved=True,
        )

        steps = [
            await registry.execute("k8s.list_pods", {"namespace": "default"}, context),
            await registry.execute("k8s.read_pod_logs", {"namespace": "default", "pod_name": "pod-a"}, context),
            await registry.execute("prometheus.query_instant", {"promql": "up"}, context),
            await registry.execute("ontology.blast_radius", {"entity_id": "svc-1"}, context),
            await registry.execute("memory.search_incidents", {"query": "pod-a oomkilled"}, context),
            await registry.execute(
                "k8s.scale_deployment",
                {"namespace": "default", "name": "svc", "replicas": 3},
                context,
            ),
        ]
        self.assertTrue(all(item.success for item in steps))
        self.assertEqual(steps[0].data[0]["name"], "pod-a")
        self.assertEqual(steps[1].data, ["line-1", "line-2"])
        self.assertEqual(steps[2].data, 1.2)
        self.assertEqual(steps[3].data["entity_id"], "svc-1")
        self.assertEqual(steps[4].data[0]["kind"], "incident")
        self.assertEqual(steps[5].data["replicas"], 3)

