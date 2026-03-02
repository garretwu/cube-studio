from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from fault_injector.config.schema import ChannelResult
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,
    OSResourcePressureScenario,
    PlatformCascadeScenario,
    StorageIOInterferenceScenario,
    ThermalThrottlingScenario,
)
from fault_injector.safety.guard import SafetyViolationError


@dataclass
class _DummySSH:
    responses: list[ChannelResult]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def run_command(self, node: str, command: str, use_sudo: bool = True):
        self.calls.append({"node": node, "command": command, "use_sudo": use_sudo})
        if self.responses:
            return self.responses.pop(0)
        return ChannelResult(success=True, output="", dry_run=False)


def _build_context(mock_fault_context, ssh: _DummySSH, params: dict[str, object], fault_id: str) -> FaultContext:
    return FaultContext(
        ssh=ssh,
        rollback=mock_fault_context.rollback,
        guard=mock_fault_context.guard,
        target_node=mock_fault_context.target_node,
        params=params,
        fault_id=fault_id,
        session_id=mock_fault_context.session_id,
    )


@pytest.mark.asyncio
async def test_guard_block_returns_error_for_vllm_inject(mock_fault_context, monkeypatch):
    scenario = PlatformCascadeScenario()
    ssh = _DummySSH(responses=[])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"interface": "eth0", "port": 3306, "delay_ms": 100},
        "guard-block-platform",
    )

    monkeypatch.setattr(
        ctx.guard,
        "check_command",
        lambda command, channel: (_ for _ in ()).throw(SafetyViolationError("blocked")),
    )
    result = await scenario.inject(ctx)
    assert result.success is False
    assert "blocked" in (result.error or "")


@pytest.mark.asyncio
async def test_platform_cascade_inject_command_has_no_embedded_sudo(mock_fault_context):
    scenario = PlatformCascadeScenario()
    ssh = _DummySSH(responses=[ChannelResult(success=True)])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"interface": "eth0", "port": 3306, "delay_ms": 100},
        "platform-no-sudo",
    )
    result = await scenario.inject(ctx)
    assert result.success is True
    assert ssh.calls
    assert "sudo " not in str(ssh.calls[0]["command"])


@pytest.mark.asyncio
async def test_thermal_inject_propagates_original_power_and_uses_non_sudo_payload(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="275\n"),
            ChannelResult(success=True),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 150},
        "thermal-original-power",
    )

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert int(ctx.params["original_power"]) == 275
    assert "sudo " not in str(ssh.calls[1]["command"])

    recover = await scenario.recover(ctx)
    assert recover.success is True
    assert "sudo " not in str(ssh.calls[2]["command"])
    assert "275" in str(ssh.calls[2]["command"])


@pytest.mark.asyncio
async def test_thermal_recover_failure_marks_wal_failed(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="280\n"),
            ChannelResult(success=True),
            ChannelResult(success=False, error="recover failed", dry_run=False),
        ]
    )
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 140},
        "thermal-recover-failed",
    )

    inject = await scenario.inject(ctx)
    assert inject.success is True

    recover = await scenario.recover(ctx)
    assert recover.success is False
    assert recover.error == "recover failed"
    entry = ctx.rollback.get_entry(ctx.fault_id)
    assert entry is not None
    assert entry.status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_cls,params,fault_id,expected_marker",
    [
        (GPUContentionScenario, {"gpu_id": 0, "duration": 1}, "gpu-pid-recover", "fi_gpu_burn_gpu-pid-recover"),
        (
            StorageIOInterferenceScenario,
            {"filename": "/tmp/fio-test", "duration": 1},
            "fio-pid-recover",
            "fi_fio_fio-pid-recover",
        ),
        (
            OSResourcePressureScenario,
            {"duration": 1, "cpu_workers": 1, "vm_bytes_percent": 10},
            "stress-pid-recover",
            "fi_stress_ng_stress-pid-recover",
        ),
    ],
)
async def test_pid_scoped_recovery_commands(
    mock_fault_context,
    scenario_cls,
    params,
    fault_id,
    expected_marker,
):
    scenario = scenario_cls()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True),
            ChannelResult(success=True, output="killed\n"),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(mock_fault_context, ssh, params, fault_id)

    inject = await scenario.inject(ctx)
    assert inject.success is True
    recover = await scenario.recover(ctx)
    assert recover.success is True

    recover_commands = [str(call["command"]) for call in ssh.calls[1:]]
    assert any("fault_injector_" in command and ".pid" in command for command in recover_commands)
    assert any(expected_marker in command for command in recover_commands)

    joined = " ".join(recover_commands)
    assert "pkill -f 'fio'" not in joined
    assert "pkill -f stress" not in joined


@pytest.mark.asyncio
async def test_guard_checked_on_inject_and_recover(mock_fault_context, monkeypatch):
    scenario = OSResourcePressureScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True),
            ChannelResult(success=True, output="pid_missing\n"),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(mock_fault_context, ssh, {"duration": 1, "cpu_workers": 1}, "guard-check-os")
    guard_calls: list[str] = []

    def _record_guard(command: str, channel: str):
        assert channel == "ssh"
        guard_calls.append(command)

    monkeypatch.setattr(ctx.guard, "check_command", _record_guard)

    inject = await scenario.inject(ctx)
    assert inject.success is True
    recover = await scenario.recover(ctx)
    assert recover.success is True
    assert len(guard_calls) >= 2
