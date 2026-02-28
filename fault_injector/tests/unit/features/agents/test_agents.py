from __future__ import annotations

import uuid

import pytest

from fault_injector.agents.hardware import HardwareFaultAgent
from fault_injector.agents.os_fault import OSFaultAgent
from fault_injector.agents.platform import PlatformFaultAgent
from fault_injector.agents.service import ServiceFaultAgent
from fault_injector.config.schema import SSHConfig, SafetyConfig, TargetNodeConfig
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.scenarios.base import FaultContext
from lib.channels.ssh import SSHChannel


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


@pytest.mark.asyncio
async def test_os_agent_full_cycle(context_factory):
    agent = OSFaultAgent()
    ctx = context_factory({"duration": 0, "interface": "eth0", "delay_ms": 10}, "f-os")

    inject = await agent.inject("network_jitter", ctx)
    assert inject.success is True
    assert agent.status()["active_faults"] == 1

    recover = await agent.recover("network_jitter", ctx)
    assert recover.success is True

    verify = await agent.verify("network_jitter", ctx)
    assert verify.success is True


@pytest.mark.asyncio
async def test_hardware_agent_full_cycle(context_factory):
    agent = HardwareFaultAgent()
    ctx = context_factory({"duration": 0, "gpu_id": 0}, "f-hw")

    inject = await agent.inject("gpu_contention", ctx)
    assert inject.success is True
    assert agent.status()["active_faults"] == 1

    recover = await agent.recover("gpu_contention", ctx)
    assert recover.success is True


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
