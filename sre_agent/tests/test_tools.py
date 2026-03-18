from __future__ import annotations

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
        self.assertIn("k8s.list_pods", readonly_names)
        self.assertIn("network.set_bmc_vlan", critical_names)
        self.assertNotIn("network.set_bmc_vlan", readonly_names)

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

