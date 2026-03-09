from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from fault_injector.config.schema import ChannelResult, RedfishConfig
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,
    OSResourcePressureScenario,
    PlatformCascadeScenario,
    StorageIOInterferenceScenario,
    ThermalThrottlingScenario,
    _python_c_command,
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


@dataclass
class _DummyRedfish:
    auth_success: bool = True
    thermal_success: bool = True
    power_success: bool = True
    sensors_success: bool = True
    web_fan_status_success: bool = True
    fan_set_success: bool = True
    fan_set_error: str = "fan set failed"
    calls: list[str] = field(default_factory=list)
    token_set: bool = False

    async def authenticate(self, bmc_host: str, username: str, password: str, verify_tls: bool = True):
        _ = (bmc_host, username, password, verify_tls)
        self.calls.append("authenticate")
        if self.auth_success:
            return ChannelResult(success=True, output="token")
        return ChannelResult(success=False, error="auth failed")

    async def get_thermal(self, bmc_host: str, verify_tls: bool = True):
        _ = (bmc_host, verify_tls)
        self.calls.append("get_thermal")
        if self.thermal_success:
            return ChannelResult(success=True, output='{"Fans":[{"Name":"Fan1"}],"Temperatures":[{"Name":"GPU0"}]}')
        return ChannelResult(success=False, error="thermal failed")

    async def get_power(self, bmc_host: str, verify_tls: bool = True):
        _ = (bmc_host, verify_tls)
        self.calls.append("get_power")
        if self.power_success:
            return ChannelResult(success=True, output='{"PowerControl":[{"Name":"System Power"}]}')
        return ChannelResult(success=False, error="power failed")

    async def get_sensors(self, bmc_host: str, verify_tls: bool = True):
        _ = (bmc_host, verify_tls)
        self.calls.append("get_sensors")
        if self.sensors_success:
            return ChannelResult(success=True, output='{"Members":[{"@odata.id":"/redfish/v1/Chassis/Self/Sensors/1"}]}')
        return ChannelResult(success=False, error="sensors failed")

    async def set_fan_control(
        self,
        bmc_host: str,
        fan_index: int,
        mode: str,
        pwm: int | None = None,
        verify_tls: bool = True,
        fault_id: str | None = None,
    ):
        _ = (bmc_host, fan_index, mode, pwm, verify_tls, fault_id)
        self.calls.append("set_fan_control")
        if self.fan_set_success:
            return ChannelResult(success=True, output="ok")
        return ChannelResult(success=False, error=self.fan_set_error)

    async def get_web_fan_status(self, bmc_host: str, verify_tls: bool = True):
        _ = (bmc_host, verify_tls)
        self.calls.append("get_web_fan_status")
        if self.web_fan_status_success:
            return ChannelResult(success=True, output='{"fans":[{"id":0,"mode":"Manual","pwm":30}]}')
        return ChannelResult(success=False, error="web fan status failed")

    async def logout(self, bmc_host: str, verify_tls: bool = True):
        _ = (bmc_host, verify_tls)
        self.calls.append("logout")
        return ChannelResult(success=True, output="logout ok")

    def set_token(self, bmc_host: str, token: str):
        _ = (bmc_host, token)
        self.token_set = True
        self.calls.append("set_token")

    def set_web_credentials(self, bmc_host: str, username: str, password: str):
        _ = (bmc_host, username, password)
        self.calls.append("set_web_credentials")


def _build_context(
    mock_fault_context,
    ssh: _DummySSH,
    params: dict[str, object],
    fault_id: str,
    *,
    redfish=None,
    ipmi=None,
    target_redfish=None,
    target_ipmi=None,
) -> FaultContext:
    return FaultContext(
        ssh=ssh,
        rollback=mock_fault_context.rollback,
        guard=mock_fault_context.guard,
        target_node=mock_fault_context.target_node,
        params=params,
        fault_id=fault_id,
        redfish=redfish,
        ipmi=ipmi,
        target_redfish=target_redfish,
        target_ipmi=target_ipmi,
        session_id=mock_fault_context.session_id,
    )


def _test_redfish_config() -> RedfishConfig:
    return RedfishConfig(
        bmc_host="10.11.8.13",
        username="admin",
        password="Admin@9000",
        verify_tls=False,
        timeout=30,
    )


def test_python_c_command_uses_selected_interpreter():
    command = _python_c_command("print('ok')\nprint('next')\n", interpreter="/usr/bin/python3")
    assert command.startswith('/usr/bin/python3 -c "')
    assert "print('ok')" in command


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
    redfish = _DummyRedfish()
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
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert int(ctx.params["original_power"]) == 275
    assert ctx.params["bmc_precheck"]["fan_before"]["fan_count"] == 1
    assert ctx.params["bmc_precheck"]["fan_after_inject"]["fan_count"] == 1
    assert "sudo " not in str(ssh.calls[1]["command"])

    recover = await scenario.recover(ctx)
    assert recover.success is True
    assert "sudo " not in str(ssh.calls[2]["command"])
    assert "275" in str(ssh.calls[2]["command"])
    assert "set_fan_control" in redfish.calls


@pytest.mark.asyncio
async def test_thermal_recover_failure_marks_wal_failed(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish()
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
        redfish=redfish,
        target_redfish=_test_redfish_config(),
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


@pytest.mark.asyncio
async def test_gpu_inject_uses_output_as_error_when_stderr_is_empty(mock_fault_context):
    scenario = GPUContentionScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=False, error="not found"),
            ChannelResult(success=True, output="Python 3.11.6"),
            ChannelResult(success=False, output="Traceback boom", error=""),
        ]
    )
    ctx = _build_context(mock_fault_context, ssh, {"gpu_id": 0, "duration": 1}, "gpu-error-fallback")

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Traceback boom" in (inject.error or "")
    assert str(ssh.calls[0]["command"]).startswith("/usr/bin/python3 -V")
    assert str(ssh.calls[1]["command"]).startswith("python3 -V")


@pytest.mark.asyncio
async def test_thermal_inject_fails_fast_when_target_redfish_config_missing(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish()
    ssh = _DummySSH(responses=[])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170},
        "thermal-missing-redfish-config",
        redfish=redfish,
        target_redfish=None,
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Missing target Redfish config" in (inject.error or "")
    assert redfish.calls == []


@pytest.mark.asyncio
async def test_thermal_inject_runs_redfish_precheck_before_ssh_power_limit(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="299\n"),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170},
        "thermal-bmc-precheck",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert redfish.calls[:7] == [
        "set_web_credentials",
        "authenticate",
        "get_thermal",
        "get_power",
        "get_sensors",
        "get_web_fan_status",
        "set_fan_control",
    ]
    assert redfish.calls[7] == "get_web_fan_status"
    assert redfish.calls[8] == "get_thermal"
    assert redfish.calls[9] == "logout"
    assert ctx.params["bmc_precheck"]["bmc_host"] == "10.11.8.13"
    assert ctx.params["bmc_precheck"]["fan_inject"]["mode"] == "Manual"
    assert str(ssh.calls[0]["command"]).startswith("nvidia-smi -i 0 --query-gpu=power.limit")
    assert str(ssh.calls[1]["command"]).startswith("nvidia-smi -i 0 -pl 170")


@pytest.mark.asyncio
async def test_thermal_inject_returns_clear_error_when_redfish_fan_inject_fails(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish(fan_set_success=False, fan_set_error="500 internal error")
    ssh = _DummySSH(responses=[])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170},
        "thermal-fan-inject-failed",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Redfish fan inject failed" in (inject.error or "")


@pytest.mark.asyncio
async def test_thermal_inject_does_not_fail_on_unsupported_fan_endpoint(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish(fan_set_success=False, fan_set_error="HTTP status error '404 Not Found'")
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="299\n"),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170},
        "thermal-fan-endpoint-unsupported",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Redfish fan inject failed" in (inject.error or "")


@pytest.mark.asyncio
async def test_thermal_inject_returns_clear_error_when_redfish_auth_fails(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish(auth_success=False)
    ssh = _DummySSH(responses=[])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170},
        "thermal-auth-failed",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Redfish auth failed" in (inject.error or "")
    assert redfish.calls == ["set_web_credentials", "authenticate"]


@pytest.mark.asyncio
async def test_thermal_inject_rejects_ipmi_backend(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish()
    ssh = _DummySSH(responses=[])
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {"gpu_id": 0, "power_limit": 170, "fan_control_backend": "ipmi", "ipmi_profile": "supermicro_raw"},
        "thermal-ipmi-missing-config",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
        target_ipmi=None,
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Only 'redfish' is supported" in (inject.error or "")


@pytest.mark.asyncio
async def test_thermal_inject_auto_mode_uses_redfish_only(mock_fault_context):
    scenario = ThermalThrottlingScenario()
    redfish = _DummyRedfish(fan_set_success=False, fan_set_error="HTTP status error '404 Not Found'")
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="299\n"),
            ChannelResult(success=True),
        ]
    )
    ctx = _build_context(
        mock_fault_context,
        ssh,
        {
            "gpu_id": 0,
            "power_limit": 170,
            "fan_control_backend": "auto",
        },
        "thermal-auto-ipmi-missing-tool",
        redfish=redfish,
        target_redfish=_test_redfish_config(),
    )
    inject = await scenario.inject(ctx)

    assert inject.success is False
    assert "Redfish fan inject failed" in (inject.error or "")
