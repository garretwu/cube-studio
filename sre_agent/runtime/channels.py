"""Tool runtime channel bootstrap and health tracking."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml

from fault_injector.config.schema import TargetNodeConfig
from lib.channels.cube_studio import CubeStudioChannel
from lib.channels.kubernetes import K8sChannel
from lib.channels.knowledge import KnowledgeBaseChannel
from lib.channels.log import LogChannel
from lib.channels.ontology import OntologyChannel
from lib.channels.prometheus import PrometheusChannel
from lib.channels.redfish import RedfishChannel
from lib.channels.ssh import SSHChannel
from lib.channels.switch import SwitchChannel
from sre_agent.config import SREAgentConfig
from sre_agent.tools.registry import ToolExecutionContext

ChannelHealth = Literal["ready", "degraded", "unavailable", "disabled"]
ToolRuntimeMode = Literal["strict", "degraded"]


@dataclass
class ChannelRuntimeStatus:
    name: str
    health: ChannelHealth
    required_by_tools: list[str] = field(default_factory=list)
    enabled: bool = True
    mode: str = "unknown"
    last_error: str | None = None
    last_checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "health": self.health,
            "required_by_tools": sorted(set(self.required_by_tools)),
            "enabled": self.enabled,
            "mode": self.mode,
            "last_error": self.last_error,
            "last_checked_at": self.last_checked_at.isoformat(),
        }


@dataclass
class ToolChannelBootstrapResult:
    runtime_mode: ToolRuntimeMode
    channels: dict[str, Any]
    statuses: dict[str, ChannelRuntimeStatus]

    def to_health_map(self) -> dict[str, str]:
        return {name: status.health for name, status in self.statuses.items()}


class UnavailableToolChannel:
    """Placeholder channel used when runtime dependencies are missing."""

    def __init__(self, *, name: str, reason: str, error_code: str = "CHANNEL_UNHEALTHY") -> None:
        self.name = name
        self.reason = reason
        self.error_code = error_code

    def _raise(self) -> None:
        raise RuntimeError(f"[{self.error_code}] channel '{self.name}' is unavailable: {self.reason}")

    async def execute(self, action: str, params: dict[str, Any] | None = None) -> Any:
        _ = (action, params)
        self._raise()

    async def connect(self) -> bool:
        self._raise()
        return False

    async def disconnect(self) -> bool:
        self._raise()
        return False

    async def close(self) -> None:
        return None

    async def query_instant(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def query_range(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def run_command(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def list_pods(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_pod_status(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def count_pending_pods(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def count_oomkilled_pods(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def read_pod_logs(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def read_system_log(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def query_logs(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def query_logs_range_only(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def search_runbook(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def search_runbooks(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def search_incidents(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def search_patterns(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_config_baseline(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def execute_plan(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def list_inference_services(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_service_status(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_bmc_info(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_thermal(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    async def get_power(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()

    def get_interface_status(self, *args: Any, **kwargs: Any) -> Any:
        _ = (args, kwargs)
        self._raise()


class MemoryToolChannelAdapter:
    """Adapter exposing tool-friendly memory methods over MemoryStore-like objects."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def search_incidents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if hasattr(self._store, "search_incidents"):
            return await _maybe_await(self._store.search_incidents(query, limit=limit))
        if hasattr(self._store, "search_similar"):
            rows = await _maybe_await(self._store.search_similar(query, top_k=limit))
            return [_to_mapping(item) for item in rows]
        if hasattr(self._store, "list_recent"):
            rows = await _maybe_await(self._store.list_recent(last=limit))
            return _filter_records(rows, query=query)
        raise RuntimeError("memory store does not provide incident search")

    async def search_patterns(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if hasattr(self._store, "search_patterns"):
            return await _maybe_await(self._store.search_patterns(query, limit=limit))
        if hasattr(self._store, "get_known_patterns"):
            rows = await _maybe_await(self._store.get_known_patterns())
            mappings = [_to_mapping(item) for item in rows]
            needle = query.casefold()
            if not needle:
                return mappings[:limit]
            filtered = [item for item in mappings if needle in json.dumps(item, ensure_ascii=False).casefold()]
            return filtered[:limit]
        raise RuntimeError("memory store does not provide pattern search")

    async def get_config_baseline(self, aidc_id: str) -> dict[str, Any] | None:
        if hasattr(self._store, "get_config_baseline"):
            value = await _maybe_await(self._store.get_config_baseline(aidc_id))
            return _to_mapping(value) if value is not None else None
        if hasattr(self._store, "get_baseline"):
            value = await _maybe_await(self._store.get_baseline(aidc_id))
            return _to_mapping(value) if value is not None else None
        raise RuntimeError("memory store does not provide baseline lookup")


class RemediationToolChannelAdapter:
    """Adapter exposing execute_plan interface expected by tools.write.remediation."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def execute_plan(self, plan: Any) -> dict[str, Any]:
        return await _maybe_await(self._engine.execute_plan(plan))


class _LokiHttpBackend:
    """Minimal async Loki backend for LogChannel."""

    def __init__(self, *, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def query(self, query: str, limit: int = 200, **kwargs: Any) -> dict[str, Any]:
        params = {"query": query, "limit": int(limit)}
        if "start" in kwargs and kwargs["start"] is not None:
            params["start"] = str(_to_loki_ts(kwargs["start"]))
        if "end" in kwargs and kwargs["end"] is not None:
            params["end"] = str(_to_loki_ts(kwargs["end"]))
        if "step" in kwargs and kwargs["step"] is not None:
            params["step"] = str(kwargs["step"])
        response = await self._client.get("/loki/api/v1/query", params=params)
        response.raise_for_status()
        return response.json()

    async def query_range(self, query: str, start: Any, end: Any, step: str = "30s", limit: int = 200, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        params = {
            "query": query,
            "start": str(_to_loki_ts(start)),
            "end": str(_to_loki_ts(end)),
            "step": str(step),
            "limit": int(limit),
        }
        response = await self._client.get("/loki/api/v1/query_range", params=params)
        response.raise_for_status()
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()


_DEFAULT_CHANNEL_TOOL_MAP: dict[str, list[str]] = {
    "alert": [],
    "ontology": ["ontology.query", "ontology.get_path", "ontology.get_blast_radius"],
    "ssh": [
        "gpu.get_metrics",
        "gpu.get_processes",
        "process.find",
        "ssh.run_command",
        "network.get_rdma_stats",
        "network.get_tc_qdisc",
        "network.get_nic_link_state",
        "network.get_nic_counters",
    ],
    "k8s": [
        "k8s.list_pods",
        "k8s.describe_pod",
        "k8s.read_pod_logs",
        "k8s.top_pending",
        "k8s.top_oomkilled",
        "k8s.apply_manifest",
        "k8s.delete_pod",
        "k8s.scale_deployment",
        "k8s.cordon_node",
        "k8s.drain_node",
    ],
    "prometheus": ["prometheus.query_instant", "prometheus.query_range"],
    "switch": [
        "network.get_switch_port_counters",
        "network.set_switch_port_up",
        "network.set_switch_port_down",
        "network.update_switch_config",
    ],
    "redfish": ["bmc.get_info", "bmc.get_fan_status", "bmc.get_thermal", "bmc.get_power", "network.set_bmc_vlan", "network.set_bmc_mtu"],
    "log": ["logs.query", "logs.read_pod", "logs.read_system", "k8s.read_pod_logs"],
    "knowledge": ["knowledge.search", "knowledge.search_runbook"],
    "memory": ["memory.search_incidents", "memory.search_patterns", "memory.get_config_baseline"],
    "cube_studio": ["platform.list_inference_services", "platform.get_service_status"],
    "remediation": ["remediation.execute_plan"],
}


def bootstrap_tool_channels(
    *,
    cfg: SREAgentConfig,
    context: ToolExecutionContext,
    ontology: Any,
    memory_store: Any,
    knowledge_store: Any,
) -> ToolChannelBootstrapResult:
    runtime_mode = _resolve_runtime_mode(cfg)
    statuses: dict[str, ChannelRuntimeStatus] = {}
    channels = context.channels
    now = datetime.now(UTC)

    if "ontology" not in channels:
        channels["ontology"] = OntologyChannel(ontology=ontology)
    statuses["ontology"] = ChannelRuntimeStatus(
        name="ontology",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP.get("ontology", []),
        enabled=True,
        mode="injected",
        last_checked_at=now,
    )

    _ensure_memory_channel(cfg=cfg, channels=channels, statuses=statuses, memory_store=memory_store)
    _ensure_knowledge_channel(cfg=cfg, channels=channels, statuses=statuses, knowledge_store=knowledge_store)
    _ensure_k8s_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_prometheus_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_log_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_ssh_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_switch_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_redfish_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_cube_studio_channel(cfg=cfg, channels=channels, statuses=statuses)
    _ensure_alert_channel(channels=channels, statuses=statuses)

    context.metadata["channel_health"] = {name: status.health for name, status in statuses.items()}
    context.metadata["channel_status"] = {name: status.as_json() for name, status in statuses.items()}
    context.metadata["tool_runtime_mode"] = runtime_mode
    return ToolChannelBootstrapResult(runtime_mode=runtime_mode, channels=channels, statuses=statuses)


def register_remediation_channel(
    *,
    context: ToolExecutionContext,
    statuses: dict[str, ChannelRuntimeStatus],
    remediation_engine: Any,
) -> None:
    now = datetime.now(UTC)
    context.channels["remediation"] = RemediationToolChannelAdapter(remediation_engine)
    statuses["remediation"] = ChannelRuntimeStatus(
        name="remediation",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP.get("remediation", []),
        enabled=True,
        mode="adapter",
        last_checked_at=now,
    )
    context.metadata["channel_health"] = {name: status.health for name, status in statuses.items()}
    context.metadata["channel_status"] = {name: status.as_json() for name, status in statuses.items()}


def core_channels_ready(statuses: dict[str, ChannelRuntimeStatus], required: list[str]) -> bool:
    for name in required:
        status = statuses.get(name)
        if status is None:
            return False
        if status.health != "ready":
            return False
    return True


def _resolve_runtime_mode(cfg: SREAgentConfig) -> ToolRuntimeMode:
    mode = str(getattr(cfg.tool_runtime, "mode", "degraded")).strip().lower()
    return "strict" if mode == "strict" else "degraded"


def _channel_enabled(cfg: SREAgentConfig, channel_name: str) -> bool:
    channels_cfg = getattr(cfg.tool_runtime, "channels", {}) or {}
    item = channels_cfg.get(channel_name) if isinstance(channels_cfg, dict) else None
    if item is None:
        return True
    enabled = getattr(item, "enabled", True)
    return bool(enabled)


def _required_core_channels(cfg: SREAgentConfig) -> list[str]:
    values = list(getattr(cfg.tool_runtime, "core_required_channels", []) or [])
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return cleaned or ["ssh", "k8s", "prometheus"]


def runtime_requirements_not_met(cfg: SREAgentConfig, statuses: dict[str, ChannelRuntimeStatus]) -> list[str]:
    required = _required_core_channels(cfg)
    unmet: list[str] = []
    for name in required:
        status = statuses.get(name)
        if status is None or status.health != "ready":
            unmet.append(name)
    return unmet


def _ensure_alert_channel(*, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if "alert" in channels:
        statuses["alert"] = ChannelRuntimeStatus(
            name="alert",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP.get("alert", []),
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    statuses["alert"] = ChannelRuntimeStatus(
        name="alert",
        health="degraded",
        required_by_tools=[],
        enabled=False,
        mode="optional",
        last_error="alert channel not injected; alert poller may fallback to alertmanager_url",
        last_checked_at=now,
    )


def _ensure_memory_channel(
    *,
    cfg: SREAgentConfig,
    channels: dict[str, Any],
    statuses: dict[str, ChannelRuntimeStatus],
    memory_store: Any,
) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "memory"):
        channels["memory"] = UnavailableToolChannel(name="memory", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["memory"] = ChannelRuntimeStatus(
            name="memory",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["memory"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.memory.enabled=false",
            last_checked_at=now,
        )
        return
    if "memory" in channels:
        statuses["memory"] = ChannelRuntimeStatus(
            name="memory",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["memory"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    channels["memory"] = MemoryToolChannelAdapter(memory_store)
    statuses["memory"] = ChannelRuntimeStatus(
        name="memory",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["memory"],
        enabled=True,
        mode="adapter",
        last_checked_at=now,
    )


def _ensure_knowledge_channel(
    *,
    cfg: SREAgentConfig,
    channels: dict[str, Any],
    statuses: dict[str, ChannelRuntimeStatus],
    knowledge_store: Any,
) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "knowledge"):
        channels["knowledge"] = UnavailableToolChannel(
            name="knowledge",
            reason="disabled by config",
            error_code="CHANNEL_DISABLED",
        )
        statuses["knowledge"] = ChannelRuntimeStatus(
            name="knowledge",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["knowledge"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.knowledge.enabled=false",
            last_checked_at=now,
        )
        return
    if "knowledge" in channels:
        statuses["knowledge"] = ChannelRuntimeStatus(
            name="knowledge",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["knowledge"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    channels["knowledge"] = KnowledgeBaseChannel(store=knowledge_store)
    if knowledge_store is None:
        statuses["knowledge"] = ChannelRuntimeStatus(
            name="knowledge",
            health="unavailable",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["knowledge"],
            enabled=True,
            mode="channel",
            last_error="knowledge store dependency is not configured",
            last_checked_at=now,
        )
    else:
        statuses["knowledge"] = ChannelRuntimeStatus(
            name="knowledge",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["knowledge"],
            enabled=True,
            mode="channel",
            last_checked_at=now,
        )


def _ensure_k8s_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "k8s"):
        channels["k8s"] = UnavailableToolChannel(name="k8s", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["k8s"] = ChannelRuntimeStatus(
            name="k8s",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["k8s"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.k8s.enabled=false",
            last_checked_at=now,
        )
        return
    if "k8s" in channels:
        statuses["k8s"] = ChannelRuntimeStatus(
            name="k8s",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["k8s"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    kubeconfig = os.getenv("SRE_KUBECONFIG", "").strip()
    if not kubeconfig:
        kubeconfig = "~/.kube/config"
    channels["k8s"] = K8sChannel(client=None, kubeconfig=kubeconfig)
    exists = Path(kubeconfig).expanduser().exists()
    statuses["k8s"] = ChannelRuntimeStatus(
        name="k8s",
        health="ready" if exists else "degraded",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["k8s"],
        enabled=True,
        mode="channel",
        last_error=None if exists else f"kubeconfig not found: {kubeconfig}",
        last_checked_at=now,
    )


def _ensure_prometheus_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "prometheus"):
        channels["prometheus"] = UnavailableToolChannel(
            name="prometheus",
            reason="disabled by config",
            error_code="CHANNEL_DISABLED",
        )
        statuses["prometheus"] = ChannelRuntimeStatus(
            name="prometheus",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["prometheus"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.prometheus.enabled=false",
            last_checked_at=now,
        )
        return
    if "prometheus" in channels or "metrics" in channels:
        statuses["prometheus"] = ChannelRuntimeStatus(
            name="prometheus",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["prometheus"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    base_url = os.getenv("SRE_PROMETHEUS_URL", "").strip() or str(cfg.global_.prometheus_url or "").strip()
    if not base_url:
        channels["prometheus"] = UnavailableToolChannel(
            name="prometheus",
            reason="prometheus_url is not configured",
            error_code="CHANNEL_DISABLED",
        )
        statuses["prometheus"] = ChannelRuntimeStatus(
            name="prometheus",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["prometheus"],
            enabled=False,
            mode="placeholder",
            last_error="prometheus_url is not configured",
            last_checked_at=now,
        )
        return
    channels["prometheus"] = PrometheusChannel(base_url=base_url, timeout=15)
    statuses["prometheus"] = ChannelRuntimeStatus(
        name="prometheus",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["prometheus"],
        enabled=True,
        mode="channel",
        last_checked_at=now,
    )


def _ensure_log_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "log"):
        channels["log"] = UnavailableToolChannel(name="log", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["log"] = ChannelRuntimeStatus(
            name="log",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["log"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.log.enabled=false",
            last_checked_at=now,
        )
        return
    if "log" in channels:
        statuses["log"] = ChannelRuntimeStatus(
            name="log",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["log"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    loki_url = os.getenv("SRE_LOKI_URL", "").strip() or str(cfg.global_.loki_url or "").strip()
    if not loki_url:
        channels["log"] = UnavailableToolChannel(
            name="log",
            reason="loki_url is not configured (SRE_LOKI_URL or global.loki_url)",
            error_code="CHANNEL_DISABLED",
        )
        statuses["log"] = ChannelRuntimeStatus(
            name="log",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["log"],
            enabled=False,
            mode="placeholder",
            last_error="loki_url is not configured (SRE_LOKI_URL or global.loki_url)",
            last_checked_at=now,
        )
        return
    loki_backend = _LokiHttpBackend(base_url=loki_url)
    channels["log"] = LogChannel(loki=loki_backend)
    statuses["log"] = ChannelRuntimeStatus(
        name="log",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["log"],
        enabled=True,
        mode="channel",
        last_checked_at=now,
    )


def _ensure_ssh_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "ssh"):
        channels["ssh"] = UnavailableToolChannel(name="ssh", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["ssh"] = ChannelRuntimeStatus(
            name="ssh",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["ssh"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.ssh.enabled=false",
            last_checked_at=now,
        )
        return
    if "ssh" in channels:
        statuses["ssh"] = ChannelRuntimeStatus(
            name="ssh",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["ssh"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    inventory_nodes = _load_ssh_inventory(cfg)
    if not inventory_nodes:
        channels["ssh"] = UnavailableToolChannel(name="ssh", reason="no SSH inventory discovered", error_code="CHANNEL_DISABLED")
        statuses["ssh"] = ChannelRuntimeStatus(
            name="ssh",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["ssh"],
            enabled=False,
            mode="placeholder",
            last_error="no SSH inventory discovered from live inventory or SRE_SSH_INVENTORY_PATH",
            last_checked_at=now,
        )
        return
    channels["ssh"] = SSHChannel(inventory=inventory_nodes, dry_run=False)
    statuses["ssh"] = ChannelRuntimeStatus(
        name="ssh",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["ssh"],
        enabled=True,
        mode="channel",
        last_checked_at=now,
    )


def _ensure_switch_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "switch"):
        channels["switch"] = UnavailableToolChannel(name="switch", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["switch"] = ChannelRuntimeStatus(
            name="switch",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["switch"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.switch.enabled=false",
            last_checked_at=now,
        )
        return
    if "switch" in channels:
        statuses["switch"] = ChannelRuntimeStatus(
            name="switch",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["switch"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    devices = _load_switch_devices(cfg)
    if not devices:
        channels["switch"] = UnavailableToolChannel(name="switch", reason="no switch devices configured", error_code="CHANNEL_DISABLED")
        statuses["switch"] = ChannelRuntimeStatus(
            name="switch",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["switch"],
            enabled=False,
            mode="placeholder",
            last_error="no switch devices configured",
            last_checked_at=now,
        )
        return
    channels["switch"] = SwitchChannel(devices=devices, dry_run=False, timeout=30)
    statuses["switch"] = ChannelRuntimeStatus(
        name="switch",
        health="ready",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["switch"],
        enabled=True,
        mode="channel",
        last_checked_at=now,
    )


def _ensure_redfish_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "redfish"):
        channels["redfish"] = UnavailableToolChannel(name="redfish", reason="disabled by config", error_code="CHANNEL_DISABLED")
        statuses["redfish"] = ChannelRuntimeStatus(
            name="redfish",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["redfish"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.redfish.enabled=false",
            last_checked_at=now,
        )
        return
    if "redfish" in channels:
        statuses["redfish"] = ChannelRuntimeStatus(
            name="redfish",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["redfish"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    channels["redfish"] = RedfishChannel(timeout=30)
    statuses["redfish"] = ChannelRuntimeStatus(
        name="redfish",
        health="degraded",
        required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["redfish"],
        enabled=True,
        mode="channel",
        last_error="redfish credentials/session token are not preloaded; authenticate before use",
        last_checked_at=now,
    )


def _ensure_cube_studio_channel(*, cfg: SREAgentConfig, channels: dict[str, Any], statuses: dict[str, ChannelRuntimeStatus]) -> None:
    now = datetime.now(UTC)
    if not _channel_enabled(cfg, "cube_studio"):
        channels["cube_studio"] = UnavailableToolChannel(
            name="cube_studio",
            reason="disabled by config",
            error_code="CHANNEL_DISABLED",
        )
        statuses["cube_studio"] = ChannelRuntimeStatus(
            name="cube_studio",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["cube_studio"],
            enabled=False,
            mode="placeholder",
            last_error="disabled by tool_runtime.channels.cube_studio.enabled=false",
            last_checked_at=now,
        )
        return
    if "cube_studio" in channels or "platform" in channels:
        statuses["cube_studio"] = ChannelRuntimeStatus(
            name="cube_studio",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["cube_studio"],
            enabled=True,
            mode="injected",
            last_checked_at=now,
        )
        return
    base_url = str(cfg.global_.cube_studio_url or "").strip() or os.getenv("SRE_CUBE_STUDIO_URL", "").strip()
    if not base_url:
        channels["cube_studio"] = UnavailableToolChannel(
            name="cube_studio",
            reason="cube_studio_url is not configured",
            error_code="CHANNEL_DISABLED",
        )
        statuses["cube_studio"] = ChannelRuntimeStatus(
            name="cube_studio",
            health="disabled",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["cube_studio"],
            enabled=False,
            mode="placeholder",
            last_error="cube_studio_url is not configured",
            last_checked_at=now,
        )
        return
    auth_method = os.getenv("SRE_CUBE_STUDIO_AUTH_METHOD", "jwt").strip() or "jwt"
    username = os.getenv("SRE_CUBE_STUDIO_USERNAME", "admin").strip() or "admin"
    jwt_secret = os.getenv("JWT_SECRET", "").strip() or os.getenv("SRE_CUBE_STUDIO_JWT_SECRET", "").strip() or None
    try:
        channels["cube_studio"] = CubeStudioChannel(
            base_url=base_url,
            auth_method=auth_method,
            username=username,
            jwt_secret=jwt_secret,
        )
        statuses["cube_studio"] = ChannelRuntimeStatus(
            name="cube_studio",
            health="ready",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["cube_studio"],
            enabled=True,
            mode="channel",
            last_checked_at=now,
        )
    except Exception as exc:  # noqa: BLE001
        channels["cube_studio"] = UnavailableToolChannel(name="cube_studio", reason=str(exc), error_code="CHANNEL_UNHEALTHY")
        statuses["cube_studio"] = ChannelRuntimeStatus(
            name="cube_studio",
            health="unavailable",
            required_by_tools=_DEFAULT_CHANNEL_TOOL_MAP["cube_studio"],
            enabled=True,
            mode="placeholder",
            last_error=str(exc),
            last_checked_at=now,
        )


def _load_inventory_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(payload, dict):
            return payload
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _load_ssh_inventory(cfg: SREAgentConfig) -> dict[str, TargetNodeConfig]:
    candidates: list[Path] = []
    env_path = os.getenv("SRE_SSH_INVENTORY_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))
    discovery_path = str(cfg.ontology.discovery.live_inventory_path or "").strip()
    if discovery_path:
        candidates.append(Path(discovery_path))

    for path in candidates:
        payload = _load_inventory_payload(path)
        workers = payload.get("inventory", {}).get("workers", [])
        if not isinstance(workers, list):
            continue
        result: dict[str, TargetNodeConfig] = {}
        for worker in workers:
            if not isinstance(worker, dict):
                continue
            name = str(worker.get("name", "")).strip()
            ssh = worker.get("ssh")
            if not name or not isinstance(ssh, dict):
                continue
            try:
                result[name] = TargetNodeConfig.model_validate(
                    {
                        "name": name,
                        "ssh": ssh,
                        "redfish": worker.get("redfish"),
                        "interface": worker.get("interface", "eth0"),
                        "roles": worker.get("roles", []),
                    }
                )
            except Exception:  # noqa: BLE001
                continue
        if result:
            return result
    return {}


def _load_switch_devices(cfg: SREAgentConfig) -> dict[str, dict[str, Any]]:
    env_json = os.getenv("SRE_SWITCH_DEVICES_JSON", "").strip()
    if env_json:
        try:
            payload = json.loads(env_json)
            if isinstance(payload, dict):
                return payload
        except Exception:  # noqa: BLE001
            pass

    path = Path(str(cfg.ontology.discovery.live_inventory_path or "").strip())
    payload = _load_inventory_payload(path)
    switches = payload.get("switches")
    if isinstance(switches, dict):
        return switches
    return {}


def _filter_records(rows: Any, *, query: str) -> list[dict[str, Any]]:
    mappings = [_to_mapping(item) for item in rows] if isinstance(rows, list) else []
    needle = query.casefold()
    if not needle:
        return mappings
    return [item for item in mappings if needle in json.dumps(item, ensure_ascii=False).casefold()]


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        mapped = model_dump(mode="json")
        return mapped if isinstance(mapped, dict) else {"value": mapped}
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def _to_loki_ts(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return int(value)
        return int(float(value) * 1_000_000_000)
    return int(datetime.now(UTC).timestamp() * 1_000_000_000)


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value
