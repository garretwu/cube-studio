from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from collections.abc import Iterable
from enum import Enum
from typing import Any

ToolHandler = Any


class ToolRegistryError(RuntimeError):
    """Base error for tool registry operations."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a tool name is not registered."""


class ToolBlockedError(ToolRegistryError):
    """Raised when a tool is blocked by policy."""


class ToolApprovalRequiredError(ToolRegistryError):
    """Raised when a tool requires explicit approval."""


class ToolValidationError(ToolRegistryError):
    """Raised for invalid tool parameters or execution context."""


class SafetyLevel(str, Enum):
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    safety_level: SafetyLevel = SafetyLevel.READ_ONLY
    params_schema: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    needs_approval: bool = False


@dataclass
class ToolExecutionContext:
    channels: dict[str, Any] = field(default_factory=dict)
    write_approved: bool = False
    approval_token: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool: str
    success: bool
    data: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Registry for tool definitions, handlers, and policy-enforced execution."""

    _HARD_BLOCKED_BMC_TOOLS = {"network.set_bmc_vlan", "network.set_bmc_mtu"}

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._disabled: set[str] = set()

    def register(self, tool: ToolDefinition, handler: ToolHandler) -> None:
        name = tool.name.strip()
        if not name:
            raise ToolValidationError("tool name must not be blank")
        if name in self._tools:
            raise ToolRegistryError(f"tool already registered: {name}")
        self._tools[name] = tool
        self._handlers[name] = handler

    def get_tool(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(f"tool not found: {name}")
        return tool

    def disable_tool(self, name: str) -> None:
        self.get_tool(name)
        self._disabled.add(name)

    def enable_tool(self, name: str) -> None:
        self.get_tool(name)
        self._disabled.discard(name)

    def is_enabled(self, name: str) -> bool:
        self.get_tool(name)
        return name not in self._disabled

    def list_tools(
        self,
        safety_levels: Iterable[SafetyLevel | str] | None = None,
        tool_names: Iterable[str] | None = None,
    ) -> list[ToolDefinition]:
        selected_names = None if tool_names is None else {str(name).strip() for name in tool_names if str(name).strip()}
        if safety_levels is None:
            names = sorted(self._tools)
            if selected_names is not None:
                names = [name for name in names if name in selected_names]
            return [self._tools[name] for name in names]
        allowed = {self._normalize_level(level) for level in safety_levels}
        out: list[ToolDefinition] = []
        for name in sorted(self._tools):
            if selected_names is not None and name not in selected_names:
                continue
            tool = self._tools[name]
            if tool.safety_level in allowed:
                out.append(tool)
        return out

    def get_tools_by_level(
        self,
        level: SafetyLevel | str,
        *,
        tool_names: Iterable[str] | None = None,
    ) -> list[ToolDefinition]:
        return self.list_tools([level], tool_names=tool_names)

    def get_tool_descriptions(
        self,
        safety_levels: Iterable[SafetyLevel | str] | None = None,
        *,
        tool_names: Iterable[str] | None = None,
        include_schema: bool = False,
    ) -> str:
        lines: list[str] = []
        for tool in self.list_tools(safety_levels=safety_levels, tool_names=tool_names):
            line = f"- `{tool.name}` ({tool.safety_level.value}): {tool.description}"
            if include_schema:
                line += f" | schema={tool.params_schema}"
            lines.append(line)
        return "\n".join(lines)

    async def execute(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        if name not in self._tools:
            return ToolResult(tool=name, success=False, error=f"tool not found: {name}")
        tool = self._tools[name]
        handler = self._handlers[name]
        if name in self._disabled:
            return ToolResult(tool=name, success=False, error=f"tool is disabled: {name}")
        payload = params or {}
        if not isinstance(payload, dict):
            return ToolResult(tool=name, success=False, error="params must be a dict")
        ctx = context or ToolExecutionContext()

        try:
            self._enforce_policy(tool, ctx)
            value = handler(payload, ctx)
            if inspect.isawaitable(value):
                value = await value
            return ToolResult(
                tool=name,
                success=True,
                data=value,
                metadata={"safety_level": tool.safety_level.value},
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(tool=name, success=False, error=str(exc), metadata={"safety_level": tool.safety_level.value})

    def get_langchain_tools(
        self,
        safety_level: SafetyLevel | str = SafetyLevel.READ_ONLY,
        *,
        tool_names: Iterable[str] | None = None,
    ) -> list[Any]:
        """Export read-only tools as LangChain @tool callables."""
        try:
            from langchain_core.tools import tool as langchain_tool
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("langchain-core is required for get_langchain_tools") from exc

        normalized = self._normalize_level(safety_level)
        exported: list[Any] = []
        for tool_def in self.get_tools_by_level(normalized, tool_names=tool_names):
            tool_name = tool_def.name

            async def _wrapped(_tool_name: str = tool_name, **kwargs: Any) -> Any:
                result = await self.execute(
                    _tool_name,
                    kwargs,
                    ToolExecutionContext(),
                )
                if not result.success:
                    raise RuntimeError(result.error or f"{_tool_name} failed")
                return result.data

            _wrapped.__name__ = tool_name.replace(".", "_")
            _wrapped.__doc__ = tool_def.description
            exported.append(langchain_tool(tool_name, description=tool_def.description)(_wrapped))
        return exported

    def _enforce_policy(self, tool: ToolDefinition, context: ToolExecutionContext) -> None:
        if tool.name in self._HARD_BLOCKED_BMC_TOOLS:
            if not bool(context.metadata.get("allow_bmc_network_write", False)):
                raise ToolBlockedError(f"tool {tool.name} is hard-blocked without explicit bmc network approval")

        if tool.needs_approval and not context.write_approved:
            raise ToolApprovalRequiredError(f"tool {tool.name} requires explicit approval")

    @staticmethod
    def _normalize_level(level: SafetyLevel | str) -> SafetyLevel:
        if isinstance(level, SafetyLevel):
            return level
        text = str(level).strip().lower()
        for candidate in SafetyLevel:
            if candidate.value == text:
                return candidate
        raise ToolValidationError(f"unknown safety level: {level!r}")


def build_default_registry() -> ToolRegistry:
    """Build the default Agent-B tool layout."""
    from sre_agent.tools.readonly import gpu, k8s, memory, network, ontology, prometheus
    from sre_agent.tools.write import k8s as write_k8s
    from sre_agent.tools.write import network as write_network
    from sre_agent.tools.write import remediation as write_remediation

    registry = ToolRegistry()

    # readonly/k8s.py
    registry.register(
        ToolDefinition(
            name="k8s.list_pods",
            description="kubectl get pods equivalent.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace"]},
            tags=("k8s", "readonly"),
        ),
        k8s.list_pods,
    )
    registry.register(
        ToolDefinition(
            name="k8s.describe_pod",
            description="kubectl describe pod equivalent (status-focused).",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace", "pod_name"]},
            tags=("k8s", "readonly"),
        ),
        k8s.describe_pod,
    )
    registry.register(
        ToolDefinition(
            name="k8s.resolve_service_pods",
            description="Resolve pod names behind a Kubernetes Service.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace", "service_name"]},
            tags=("k8s", "readonly"),
        ),
        k8s.resolve_service_pods,
    )
    registry.register(
        ToolDefinition(
            name="k8s.resolve_pod_node_ip",
            description="Resolve the hosting node IP for a Pod.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace", "pod_name"]},
            tags=("k8s", "readonly"),
        ),
        k8s.resolve_pod_node_ip,
    )
    registry.register(
        ToolDefinition(
            name="k8s.read_pod_logs",
            description="kubectl logs equivalent via log channel.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace", "pod_name"]},
            tags=("k8s", "logs", "readonly"),
        ),
        k8s.read_pod_logs,
    )
    registry.register(
        ToolDefinition(
            name="k8s.top_pending",
            description="Count pending pods in namespace.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace"]},
            tags=("k8s", "readonly"),
        ),
        k8s.count_pending_pods,
    )
    registry.register(
        ToolDefinition(
            name="k8s.top_oomkilled",
            description="Count OOMKilled pods in namespace.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["namespace"]},
            tags=("k8s", "readonly"),
        ),
        k8s.count_oomkilled_pods,
    )

    # readonly/prometheus.py
    registry.register(
        ToolDefinition(
            name="prometheus.query_instant",
            description="PromQL instant query.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["promql"]},
            tags=("prometheus", "readonly"),
        ),
        prometheus.query_instant,
    )
    registry.register(
        ToolDefinition(
            name="prometheus.query_range",
            description="PromQL range query.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["promql", "start", "end"]},
            tags=("prometheus", "readonly"),
        ),
        prometheus.query_range,
    )

    # readonly/gpu.py
    registry.register(
        ToolDefinition(
            name="gpu.get_metrics",
            description="Collect GPU metrics via nvidia-smi.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("gpu", "readonly"),
        ),
        gpu.get_metrics,
    )
    registry.register(
        ToolDefinition(
            name="gpu.get_processes",
            description="Collect GPU process table via nvidia-smi.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("gpu", "readonly"),
        ),
        gpu.get_processes,
    )

    # readonly/network.py
    registry.register(
        ToolDefinition(
            name="network.get_rdma_stats",
            description="Collect RDMA/link stats from node.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("network", "readonly"),
        ),
        network.get_rdma_stats,
    )
    registry.register(
        ToolDefinition(
            name="network.get_switch_port_counters",
            description="Read switch port counters/status.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["switch", "interface"]},
            tags=("network", "readonly"),
        ),
        network.get_switch_port_counters,
    )

    # readonly/ontology.py
    registry.register(
        ToolDefinition(
            name="ontology.query",
            description="Ontology entity query.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["entity_type"]},
            tags=("ontology", "readonly"),
        ),
        ontology.query_entities,
    )
    registry.register(
        ToolDefinition(
            name="ontology.path",
            description="Ontology path traversal.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["from_id", "to_id"]},
            tags=("ontology", "readonly"),
        ),
        ontology.get_path,
    )
    registry.register(
        ToolDefinition(
            name="ontology.blast_radius",
            description="Ontology blast-radius traversal.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["entity_id"]},
            tags=("ontology", "readonly"),
        ),
        ontology.get_blast_radius,
    )

    # readonly/memory.py
    registry.register(
        ToolDefinition(
            name="memory.search_incidents",
            description="Search incident memory records.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["query"]},
            tags=("memory", "readonly"),
        ),
        memory.search_incidents,
    )
    registry.register(
        ToolDefinition(
            name="memory.search_patterns",
            description="Search learned patterns.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["query"]},
            tags=("memory", "readonly"),
        ),
        memory.search_patterns,
    )
    registry.register(
        ToolDefinition(
            name="memory.get_config_baseline",
            description="Get config baseline by aidc id.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["aidc_id"]},
            tags=("memory", "readonly"),
        ),
        memory.get_config_baseline,
    )

    # write/k8s.py
    registry.register(
        ToolDefinition(
            name="k8s.apply_manifest",
            description="kubectl apply manifest (if backend supports it).",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["manifest"]},
            tags=("k8s", "write"),
            needs_approval=True,
        ),
        write_k8s.apply_manifest,
    )
    registry.register(
        ToolDefinition(
            name="k8s.delete_pod",
            description="kubectl delete pod / selector.",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["namespace"]},
            tags=("k8s", "write"),
            needs_approval=True,
        ),
        write_k8s.delete_pod,
    )
    registry.register(
        ToolDefinition(
            name="k8s.scale_deployment",
            description="kubectl scale deployment.",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["namespace", "name", "replicas"]},
            tags=("k8s", "write"),
            needs_approval=True,
        ),
        write_k8s.scale_deployment,
    )
    registry.register(
        ToolDefinition(
            name="k8s.cordon_node",
            description="kubectl cordon node (if backend supports it).",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["node"]},
            tags=("k8s", "write"),
            needs_approval=True,
        ),
        write_k8s.cordon_node,
    )
    registry.register(
        ToolDefinition(
            name="k8s.drain_node",
            description="kubectl drain node (if backend supports it).",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["node"]},
            tags=("k8s", "write"),
            needs_approval=True,
        ),
        write_k8s.drain_node,
    )

    # write/remediation.py
    registry.register(
        ToolDefinition(
            name="kill_process",
            description="Terminate a rogue process on a node via SSH using pid or process name.",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["node"]},
            tags=("remediation", "write", "ssh"),
            needs_approval=True,
        ),
        write_remediation.kill_process,
    )
    registry.register(
        ToolDefinition(
            name="remediation.execute_plan",
            description="Execute validated remediation plan through remediation engine.",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["plan"]},
            tags=("remediation", "write"),
            needs_approval=True,
        ),
        write_remediation.execute_plan,
    )

    # write/network.py
    registry.register(
        ToolDefinition(
            name="network.switch_port_enable",
            description="Enable switch port.",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["switch", "interface"]},
            tags=("network", "write"),
            needs_approval=True,
        ),
        write_network.switch_port_enable,
    )
    registry.register(
        ToolDefinition(
            name="network.switch_port_disable",
            description="Disable switch port.",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["switch", "interface"]},
            tags=("network", "write"),
            needs_approval=True,
        ),
        write_network.switch_port_disable,
    )
    registry.register(
        ToolDefinition(
            name="network.update_route",
            description="Update switch route configuration.",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["switch", "config_xml"]},
            tags=("network", "write"),
            needs_approval=True,
        ),
        write_network.update_route,
    )
    registry.register(
        ToolDefinition(
            name="network.set_bmc_vlan",
            description="Set BMC VLAN (hard blocked without explicit network approval flag).",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["bmc_host", "vlan_id"]},
            tags=("network", "bmc", "write"),
            needs_approval=True,
        ),
        write_network.set_bmc_vlan,
    )
    registry.register(
        ToolDefinition(
            name="network.set_bmc_mtu",
            description="Set BMC MTU (hard blocked without explicit network approval flag).",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["bmc_host", "mtu"]},
            tags=("network", "bmc", "write"),
            needs_approval=True,
        ),
        write_network.set_bmc_mtu,
    )

    return registry
