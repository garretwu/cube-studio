from __future__ import annotations

import inspect
import re
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
    command_template: str | None = None
    command_template_alt: str | None = None

    def build_command(self, params: dict[str, Any]) -> str:
        """Render a human-readable command string from *params*.

        If *command_template* is set, it is formatted via ``str.format_map``
        with *params* (missing keys render as ``<key>``).  Falls back to
        ``<tool_name> <params_json>``.
        """
        import json

        explicit = params.get("command")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        template = self._resolve_template(params)
        if template:
            safe_params = {k: v for k, v in params.items() if v is not None}
            try:
                return template.format_map(_SafeFormatDict(safe_params))
            except Exception:  # noqa: BLE001
                pass

        compact = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
        return f"{self.name} {compact}"

    def _resolve_template(self, params: dict[str, Any]) -> str | None:
        """Pick the best template based on *params* content."""
        if not self.command_template:
            return None
        if not self.command_template_alt:
            return self.command_template
        # For tools with alt template (e.g. k8s.delete_pod), pick based on params.
        # Primary template uses keys from itself; alt template uses different keys.
        # Heuristic: if alt template's unique keys are present in params, prefer alt.
        try:
            primary_keys = set(_extract_format_keys(self.command_template))
            alt_keys = set(_extract_format_keys(self.command_template_alt))
            alt_only = alt_keys - primary_keys
            if alt_only and any(k in params and params[k] for k in alt_only):
                return self.command_template_alt
        except Exception:  # noqa: BLE001
            pass
        return self.command_template


def _extract_format_keys(template: str) -> list[str]:
    """Extract ``{key}`` placeholders from a format string."""
    import re
    return re.findall(r"\{(\w+)\}", template)


class _SafeFormatDict(dict):
    """``str.format_map`` helper that keeps unresolved placeholders intact."""

    def __missing__(self, key: str) -> str:
        return f"<{key}>"


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

    @staticmethod
    def _classify_error(message: str) -> tuple[str | None, str | None]:
        text = message.strip()
        if not text:
            return None, None

        tagged = re.search(r"\[(CHANNEL_[A-Z_]+)\]\s*channel '([^']+)'", text)
        if tagged:
            return tagged.group(1), tagged.group(2)

        missing = re.search(r"required channel is missing:\s*([a-zA-Z0-9_/-]+)", text)
        if missing:
            return "CHANNEL_MISSING", missing.group(1)

        lowered = text.casefold()
        if "dependency is not configured" in lowered or "is not configured" in lowered:
            return "CHANNEL_UNHEALTHY", None
        return None, None

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
            metadata = {"safety_level": tool.safety_level.value}
            error_text = str(exc)
            error_code, channel_name = self._classify_error(error_text)
            if error_code is not None:
                metadata["error_code"] = error_code
            if channel_name:
                metadata["channel"] = channel_name
                health = ctx.metadata.get("channel_health", {}).get(channel_name)
                if isinstance(health, str) and health:
                    metadata["channel_state"] = health
            return ToolResult(tool=name, success=False, error=error_text, metadata=metadata)

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
    from sre_agent.tools.readonly import bmc, gpu, k8s, knowledge, logs, memory, network, ontology, platform, prometheus
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
            name="network.get_tc_qdisc",
            description="Inspect tc qdisc/netem rules on node interface.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("network", "readonly"),
        ),
        network.get_tc_qdisc,
    )
    registry.register(
        ToolDefinition(
            name="network.get_nic_link_state",
            description="Inspect NIC link and interface state on node.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("network", "readonly"),
        ),
        network.get_nic_link_state,
    )
    registry.register(
        ToolDefinition(
            name="network.get_nic_counters",
            description="Inspect NIC error/drop counters on node.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("network", "readonly"),
        ),
        network.get_nic_counters,
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

    # readonly/logs.py
    registry.register(
        ToolDefinition(
            name="logs.query",
            description="Query log backend with Loki-style query.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["query"]},
            tags=("logs", "readonly"),
        ),
        logs.query,
    )
    registry.register(
        ToolDefinition(
            name="logs.read_pod",
            description="Read pod logs from log backend.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["pod", "namespace"]},
            tags=("logs", "readonly"),
        ),
        logs.read_pod,
    )
    registry.register(
        ToolDefinition(
            name="logs.read_system",
            description="Read system log file from node log stream.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["node"]},
            tags=("logs", "readonly"),
        ),
        logs.read_system,
    )

    # readonly/bmc.py
    registry.register(
        ToolDefinition(
            name="bmc.get_info",
            description="Read BMC summary information via Redfish.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["bmc_host"]},
            tags=("bmc", "readonly"),
        ),
        bmc.get_info,
    )
    registry.register(
        ToolDefinition(
            name="bmc.get_thermal",
            description="Read BMC thermal telemetry via Redfish.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["bmc_host"]},
            tags=("bmc", "readonly"),
        ),
        bmc.get_thermal,
    )
    registry.register(
        ToolDefinition(
            name="bmc.get_power",
            description="Read BMC power telemetry via Redfish.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["bmc_host"]},
            tags=("bmc", "readonly"),
        ),
        bmc.get_power,
    )

    # readonly/platform.py
    registry.register(
        ToolDefinition(
            name="platform.list_inference_services",
            description="List Cube Studio inference services.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object"},
            tags=("platform", "readonly"),
        ),
        platform.list_inference_services,
    )
    registry.register(
        ToolDefinition(
            name="platform.get_service_status",
            description="Get Cube Studio inference service status by service name.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["service_name"]},
            tags=("platform", "readonly"),
        ),
        platform.get_service_status,
    )

    # readonly/knowledge.py
    registry.register(
        ToolDefinition(
            name="knowledge.search",
            description="Search knowledge base documents.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["query"]},
            tags=("knowledge", "readonly"),
        ),
        knowledge.search,
    )
    registry.register(
        ToolDefinition(
            name="knowledge.search_runbook",
            description="Search runbook snippets by symptom.",
            safety_level=SafetyLevel.READ_ONLY,
            params_schema={"type": "object", "required": ["symptom"]},
            tags=("knowledge", "readonly"),
        ),
        knowledge.search_runbook,
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
            command_template="kubectl apply -n {namespace} -f - <<EOF\n{manifest}\nEOF",
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
            command_template="kubectl delete pod {pod_name} -n {namespace}",
            command_template_alt="kubectl delete pod -l {label_selector} -n {namespace}",
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
            command_template="kubectl scale deployment {name} -n {namespace} --replicas={replicas}",
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
            command_template="kubectl cordon {node}",
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
            command_template="kubectl drain {node} --ignore-daemonsets --delete-emptydir-data",
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
            command_template="ssh {node} sudo kill {pid}",
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
            command_template="switchport enable --switch {switch} --interface {interface}",
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
            command_template="switchport disable --switch {switch} --interface {interface}",
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
            command_template="route update --switch {switch} --config <config_xml>",
        ),
        write_network.update_route,
    )
    registry.register(
        ToolDefinition(
            name="network.clear_tc_qdisc",
            description="Delete tc qdisc/netem rules from a node interface via SSH.",
            safety_level=SafetyLevel.HIGH,
            params_schema={"type": "object", "required": ["node", "iface"]},
            tags=("network", "write", "ssh"),
            needs_approval=True,
            command_template="ssh {node} sudo tc qdisc del dev {iface} root",
        ),
        write_network.clear_tc_qdisc,
    )
    registry.register(
        ToolDefinition(
            name="network.set_bmc_vlan",
            description="Set BMC VLAN (hard blocked without explicit network approval flag).",
            safety_level=SafetyLevel.CRITICAL,
            params_schema={"type": "object", "required": ["bmc_host", "vlan_id"]},
            tags=("network", "bmc", "write"),
            needs_approval=True,
            command_template="redfish set-vlan --bmc {bmc_host} --vlan {vlan_id}",
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
            command_template="redfish set-mtu --bmc {bmc_host} --mtu {mtu}",
        ),
        write_network.set_bmc_mtu,
    )

    return registry
