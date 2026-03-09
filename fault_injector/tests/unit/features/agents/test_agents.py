from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from fault_injector.agents.hardware import HardwareFaultAgent
from fault_injector.agents.monitor import MonitorAgent
from fault_injector.agents.os_fault import OSFaultAgent
from fault_injector.agents.platform import PlatformFaultAgent
from fault_injector.agents.service import ServiceFaultAgent
from fault_injector.config.schema import InjectResult, RecoverResult, SSHConfig, SafetyConfig, TargetNodeConfig
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.registry import SCENARIO_REGISTRY
from lib.channels.ssh import SSHChannel


@dataclass
class _InjectBehaviorScenario:
    layer: str = "os"
    inject_ok: bool = True
    recover_ok: bool = True
    verify_ok: bool = True
    raise_on: str = ""

    async def inject(self, ctx: FaultContext) -> InjectResult:
        _ = ctx
        if self.raise_on == "inject":
            raise RuntimeError("inject boom")
        if self.inject_ok:
            return InjectResult(success=True, fault_id=ctx.fault_id)
        return InjectResult(success=False, fault_id=ctx.fault_id, error="inject failed")

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        _ = ctx
        if self.raise_on == "recover":
            raise RuntimeError("recover boom")
        if self.recover_ok:
            return RecoverResult(success=True, fault_id=ctx.fault_id)
        return RecoverResult(success=False, fault_id=ctx.fault_id, error="recover failed")

    async def verify(self, ctx: FaultContext) -> bool:
        _ = ctx
        if self.raise_on == "verify":
            raise RuntimeError("verify boom")
        return self.verify_ok


@pytest.fixture
def context_factory(tmp_path):
    inventory = {
        "node-1": TargetNodeConfig(
            name="node-1",
            ssh=SSHConfig(host="127.0.0.1", user="root"),
            interface="eth0",
        )
    }
    rollback = RollbackJournal(tmp_path / "rollback.jsonl")
    guard = SafetyGuard(SafetyConfig(require_confirmation=False, dry_run=True))
    ssh = SSHChannel(inventory=inventory, dry_run=True, wal=rollback, guard=guard)

    def _build(params: dict, fault_id: str | None = None) -> FaultContext:
        return FaultContext(
            ssh=ssh,
            rollback=rollback,
            guard=guard,
            target_node="node-1",
            params=params,
            fault_id=fault_id or f"f-{uuid.uuid4().hex[:6]}",
            session_id="s-1",
        )

    return _build


@pytest.fixture
def register_scenario(monkeypatch):
    created: list[str] = []

    def _register(name: str, scenario):
        monkeypatch.setitem(SCENARIO_REGISTRY, name, scenario)
        created.append(name)

    yield _register

    for name in created:
        SCENARIO_REGISTRY.pop(name, None)


@pytest.mark.asyncio
async def test_os_agent_full_cycle_has_status_and_verify_metadata(context_factory):
    agent = OSFaultAgent()
    ctx = context_factory({"duration": 0, "interface": "eth0", "delay_ms": 10}, "f-os")

    inject = await agent.inject("network_jitter", ctx)
    assert inject.success is True
    assert inject.agent_name == "os_fault_agent"
    assert inject.operation == "inject"
    assert inject.scenario_name == "network_jitter"
    assert inject.duration_ms >= 0
    assert agent.status()["active_faults"] == 1

    recover = await agent.recover("network_jitter", ctx)
    assert recover.success is True
    assert recover.operation == "recover"
    assert recover.duration_ms >= 0
    assert agent.status()["active_faults"] == 0

    verify = await agent.verify("network_jitter", ctx)
    assert verify.success is True
    assert verify.operation == "verify"
    assert verify.metadata["verified"] is True


@pytest.mark.asyncio
async def test_hardware_agent_full_cycle(context_factory):
    agent = HardwareFaultAgent()
    ctx = context_factory({"duration": 0, "gpu_id": 0}, "f-hw")

    inject = await agent.inject("gpu_contention", ctx)
    assert inject.success is True
    assert agent.status()["active_faults"] == 1

    recover = await agent.recover("gpu_contention", ctx)
    assert recover.success is True
    assert agent.status()["active_faults"] == 0


@pytest.mark.asyncio
async def test_platform_agent_full_cycle(context_factory):
    agent = PlatformFaultAgent()
    ctx = context_factory({"port": 3306, "delay_ms": 100}, "f-plat")

    inject = await agent.inject("platform_cascade", ctx)
    assert inject.success is True

    recover = await agent.recover("platform_cascade", ctx)
    assert recover.success is True


@pytest.mark.asyncio
async def test_service_agent_reports_unknown_scenario(context_factory):
    agent = ServiceFaultAgent()
    ctx = context_factory({"service_name": "svc-a", "namespace": "service"}, "f-svc")

    inject = await agent.inject("unknown_service_scenario", ctx)
    assert inject.success is False
    assert "Scenario not found" in inject.error
    assert inject.duration_ms >= 0


@pytest.mark.asyncio
async def test_agent_layer_mismatch_returns_failed_result(context_factory, register_scenario):
    register_scenario("fake_mismatch", lambda: _InjectBehaviorScenario(layer="service"))
    agent = OSFaultAgent()
    ctx = context_factory({}, "f-layer")

    result = await agent.inject("fake_mismatch", ctx)
    assert result.success is False
    assert "layer mismatch" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["inject", "recover", "verify"])
async def test_agent_maps_scenario_exceptions_to_failed_results(context_factory, register_scenario, op):
    register_scenario("fake_exception", lambda: _InjectBehaviorScenario(layer="os", raise_on=op))
    agent = OSFaultAgent()
    ctx = context_factory({}, "f-exc")

    if op == "recover":
        result = await agent.recover("fake_exception", ctx)
    elif op == "verify":
        result = await agent.verify("fake_exception", ctx)
    else:
        result = await agent.inject("fake_exception", ctx)

    assert result.success is False
    assert f"{op} boom" in result.error
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_recover_uses_scenario_resolution_when_fault_not_active(context_factory, register_scenario):
    register_scenario("fake_recover", lambda: _InjectBehaviorScenario(layer="os"))
    agent = OSFaultAgent()
    ctx = context_factory({}, "f-recover")

    result = await agent.recover("fake_recover", ctx)
    assert result.success is True
    assert result.operation == "recover"
    assert ctx.fault_id not in agent.status()["active_fault_ids"]


@pytest.mark.asyncio
async def test_verify_metadata_false_is_returned_when_scenario_verify_false(context_factory, register_scenario):
    register_scenario("fake_verify_false", lambda: _InjectBehaviorScenario(layer="os", verify_ok=False))
    agent = OSFaultAgent()
    ctx = context_factory({}, "f-verify")

    result = await agent.verify("fake_verify_false", ctx)
    assert result.success is False
    assert result.metadata["verified"] is False


def test_monitor_agent_status_readiness():
    ready = MonitorAgent(channels={"prometheus": object()})
    not_ready = MonitorAgent()

    assert ready.status()["ready"] is True
    assert not_ready.status()["ready"] is False


@pytest.mark.asyncio
async def test_monitor_agent_lifecycle_noops(context_factory):
    agent = MonitorAgent()
    ctx = context_factory({}, "f-mon")

    inject = await agent.inject("any", ctx)
    recover = await agent.recover("any", ctx)
    verify = await agent.verify("any", ctx)

    assert inject.success is True and inject.metadata["noop"] is True
    assert recover.success is True and recover.metadata["noop"] is True
    assert verify.success is True and verify.metadata["noop"] is True


@pytest.mark.asyncio
async def test_monitor_agent_collection_behaviors():
    class _Prom:
        async def collect_baseline(self, queries: dict[str, str], duration: int, interval: int = 15):
            _ = (duration, interval)
            return {k: [1.0] for k in queries}

        async def query_instant(self, promql: str):
            if "fail" in promql:
                raise RuntimeError("boom")
            return 2.5

    agent = MonitorAgent(channels={"prometheus": _Prom()})

    baseline = await agent.collect_baseline({"a": "metric_a"}, duration=10, interval=1)
    observed = await agent.observe({"b": "metric_b"}, duration=10, interval=1)
    point = await agent.point_in_time({"ok": "metric_ok", "bad": "metric_fail"})

    assert baseline == {"a": [1.0]}
    assert observed == {"b": [1.0]}
    assert point["ok"] == 2.5
    assert point["bad"] == 0.0
    assert "collected_at" in point


@pytest.mark.asyncio
async def test_monitor_agent_returns_empty_results_without_prometheus():
    agent = MonitorAgent()

    baseline = await agent.collect_baseline({"a": "metric_a"}, duration=1)
    observed = await agent.observe({"b": "metric_b"}, duration=1)
    point = await agent.point_in_time({"c": "metric_c"})

    assert baseline == {}
    assert observed == {}
    assert point == {}
