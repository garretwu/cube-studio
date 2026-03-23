from __future__ import annotations

from dataclasses import dataclass

import pytest

from lib.channels.base import ChannelResult
from fault_injector.scenarios.base import FaultContext
from fault_injector.scenarios.rdma_anomaly import (
    ECNMisconfigurationScenario,
    PFCDeadlockScenario,
    RDMALinkFlapScenario,
    RDMALoadImbalanceScenario,
    RDMAQoSDowngradeScenario,
    RoCEMTUMismatchScenario,
)
from fault_injector.safety.guard import SafetyViolationError


CLI_SCENARIOS = [
    ECNMisconfigurationScenario,
    RDMAQoSDowngradeScenario,
]

SWITCH_SCENARIOS = [
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RDMAQoSDowngradeScenario,
]


@dataclass
class _DummySSH:
    responses: list[ChannelResult]

    def __post_init__(self):
        self.calls: list[dict[str, object]] = []

    async def run_command(self, node: str, command: str, use_sudo: bool = True):
        self.calls.append({"node": node, "command": command, "use_sudo": use_sudo})
        if self.responses:
            return self.responses.pop(0)
        return ChannelResult(success=True, output="", dry_run=False)


@pytest.fixture
def rdma_switch_context(mock_fault_context, fake_switch_channel):
    mock_fault_context.switch = fake_switch_channel
    mock_fault_context.params = {
        "switch": "sw1",
        "interface": "GE1/0/4",
        "flap_duration": 0,
    }
    mock_fault_context.fault_id = "rdma-switch-fixture"
    return mock_fault_context


def _build_roce_ctx(mock_fault_context, ssh: _DummySSH, *, mtu: int = 1500, original_mtu: int = 9000):
    return FaultContext(
        ssh=ssh,
        rollback=mock_fault_context.rollback,
        guard=mock_fault_context.guard,
        target_node=mock_fault_context.target_node,
        params={"interface": "eth0", "mtu": mtu, "original_mtu": original_mtu},
        fault_id="roce-fixture",
        session_id=mock_fault_context.session_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_happy_cycle(scenario_cls, rdma_switch_context):
    scenario = scenario_cls()
    ctx = rdma_switch_context

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert inject.fault_id == ctx.fault_id
    assert any(entry.fault_id == ctx.fault_id for entry in ctx.rollback.get_active_faults())

    recover = await scenario.recover(ctx)
    assert recover.success is True
    assert all(entry.fault_id != ctx.fault_id for entry in ctx.rollback.get_active_faults())


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_require_switch_channel(scenario_cls, mock_fault_context):
    scenario = scenario_cls()

    ctx = FaultContext(
        ssh=mock_fault_context.ssh,
        rollback=mock_fault_context.rollback,
        guard=mock_fault_context.guard,
        target_node=mock_fault_context.target_node,
        params={"switch": "sw1", "interface": "GE1/0/4"},
        fault_id=mock_fault_context.fault_id,
        session_id=mock_fault_context.session_id,
    )
    ctx.switch = None

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "Switch channel is required" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_require_switch_and_interface_params(scenario_cls, rdma_switch_context):
    scenario = scenario_cls()
    rdma_switch_context.params = {"switch": ""}

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is False
    assert "required" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_guard_block_returns_error(scenario_cls, rdma_switch_context, monkeypatch):
    scenario = scenario_cls()
    monkeypatch.setattr(
        rdma_switch_context.guard,
        "check_command",
        lambda command, channel: (_ for _ in ()).throw(SafetyViolationError("blocked")),
    )

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is False
    assert "blocked" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_fail_when_interface_status_missing(
    scenario_cls, mock_fault_context, fake_switch_channel_factory
):
    scenario = scenario_cls()
    mock_fault_context.switch = fake_switch_channel_factory(missing_status=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4", "flap_duration": 0}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert "Interface not found" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_fail_when_interface_config_missing(
    scenario_cls, mock_fault_context, fake_switch_channel_factory
):
    scenario = scenario_cls()
    mock_fault_context.switch = fake_switch_channel_factory(missing_config=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4", "flap_duration": 0}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert "Unable to read config" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", CLI_SCENARIOS)
async def test_cli_scenarios_allow_empty_baseline_description(scenario_cls, rdma_switch_context):
    scenario = scenario_cls()
    rdma_switch_context.switch.description = ""
    rdma_switch_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is True


@pytest.mark.asyncio
async def test_pfc_deadlock_allows_empty_baseline_description(rdma_switch_context):
    scenario = PFCDeadlockScenario()
    rdma_switch_context.switch.description = ""
    rdma_switch_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", CLI_SCENARIOS)
async def test_cli_scenarios_fail_when_apply_fails(mock_fault_context, fake_switch_channel_factory, scenario_cls):
    scenario = scenario_cls()
    mock_fault_context.switch = fake_switch_channel_factory(apply_fail=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert inject.error == "apply failed"


@pytest.mark.asyncio
async def test_pfc_deadlock_inject_emits_no_drop_dot1p_all(rdma_switch_context):
    scenario = PFCDeadlockScenario()
    rdma_switch_context.switch.description = "baseline-desc"
    rdma_switch_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is True

    state, dot1p = rdma_switch_context.switch.get_pfc_port_profile("sw1", "GE1/0/4")
    assert state == 1
    assert dot1p == [0, 1, 2, 3, 4, 5, 6, 7]


@pytest.mark.asyncio
async def test_pfc_deadlock_recover_emits_undo_no_drop(rdma_switch_context):
    scenario = PFCDeadlockScenario()
    rdma_switch_context.switch.description = "baseline-desc"
    rdma_switch_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is True

    recover = await scenario.recover(rdma_switch_context)
    assert recover.success is True

    state, dot1p = rdma_switch_context.switch.get_pfc_port_profile("sw1", "GE1/0/4")
    assert state == 1
    assert dot1p == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", CLI_SCENARIOS)
async def test_cli_scenarios_fail_post_change_verification_when_show_fails(
    mock_fault_context, fake_switch_channel_factory, scenario_cls, monkeypatch
):
    scenario = scenario_cls()
    switch = fake_switch_channel_factory()
    mock_fault_context.switch = switch
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    original_show = switch.run_cli_execution
    state = {"count": 0}

    def _first_then_fail(sw, cmd):
        state["count"] += 1
        if state["count"] > 1:
            return ChannelResult(success=False, error="show failed")
        return original_show(sw, cmd)

    monkeypatch.setattr(switch, "run_cli_execution", _first_then_fail)

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert "show failed" in (inject.error or "")


@pytest.mark.asyncio
async def test_rdma_load_imbalance_fails_when_shutdown_fails(mock_fault_context, fake_switch_channel_factory):
    scenario = RDMALoadImbalanceScenario()
    mock_fault_context.switch = fake_switch_channel_factory(shutdown_fail=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert inject.error == "shutdown failed"


@pytest.mark.asyncio
async def test_rdma_load_imbalance_fails_when_admin_verification_false(mock_fault_context, fake_switch_channel_factory):
    scenario = RDMALoadImbalanceScenario()
    mock_fault_context.switch = fake_switch_channel_factory(verify_false=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4"}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert "Post-change verification failed" in (inject.error or "")


@pytest.mark.asyncio
async def test_rdma_link_flap_fails_when_bringup_fails(mock_fault_context, fake_switch_channel_factory, monkeypatch):
    scenario = RDMALinkFlapScenario()
    mock_fault_context.switch = fake_switch_channel_factory(bringup_fail=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4", "flap_duration": 0}
    
    async def _no_sleep(_: float):
        return None

    monkeypatch.setattr("fault_injector.scenarios.rdma_anomaly.asyncio.sleep", _no_sleep)

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert inject.error == "bringup failed"


@pytest.mark.asyncio
async def test_rdma_link_flap_fails_when_link_does_not_go_down(mock_fault_context, fake_switch_channel_factory):
    scenario = RDMALinkFlapScenario()
    mock_fault_context.switch = fake_switch_channel_factory(verify_false=True)
    mock_fault_context.params = {"switch": "sw1", "interface": "GE1/0/4", "flap_duration": 0}

    inject = await scenario.inject(mock_fault_context)
    assert inject.success is False
    assert "Link did not go down" in (inject.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_recover_without_baseline_fails(scenario_cls, rdma_switch_context):
    scenario = scenario_cls()

    recover = await scenario.recover(rdma_switch_context)
    assert recover.success is False
    assert "Baseline not found" in (recover.error or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_cls", SWITCH_SCENARIOS)
async def test_switch_scenarios_recover_baseline_verification_failure(
    scenario_cls, rdma_switch_context, monkeypatch
):
    scenario = scenario_cls()

    inject = await scenario.inject(rdma_switch_context)
    assert inject.success is True

    monkeypatch.setattr(rdma_switch_context.switch, "get_interface_config", lambda switch, interface: None)
    recover = await scenario.recover(rdma_switch_context)
    assert recover.success is False
    assert "Baseline verification failed" in (recover.error or "")


def test_switch_scenarios_monitor_queries_contract():
    for scenario_cls in SWITCH_SCENARIOS:
        scenario = scenario_cls()
        queries = scenario.monitor_queries()
        assert isinstance(queries, dict)
        assert len(queries) > 0


def test_roce_mtu_monitor_queries_contract():
    scenario = RoCEMTUMismatchScenario()
    queries = scenario.monitor_queries()
    assert isinstance(queries, dict)
    assert "rdma_throughput" in queries
    assert "rdma_retrans" in queries
    assert "mtu_errors" in queries


@pytest.mark.asyncio
async def test_roce_mtu_happy_path_uses_detected_baseline(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="9000\n"),
            ChannelResult(success=True),
            ChannelResult(success=True, output="1500\n"),
        ]
    )
    ctx = _build_roce_ctx(mock_fault_context, ssh, mtu=1500, original_mtu=8000)

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert ctx.params["original_mtu"] == 9000
    assert any(entry.fault_id == ctx.fault_id for entry in ctx.rollback.get_active_faults())

    ssh.responses = [ChannelResult(success=True)]
    recover = await scenario.recover(ctx)
    assert recover.success is True
    assert all(entry.fault_id != ctx.fault_id for entry in ctx.rollback.get_active_faults())


@pytest.mark.asyncio
async def test_roce_mtu_uses_fallback_original_when_baseline_read_fails(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=False, error="read failed"),
            ChannelResult(success=True),
            ChannelResult(success=True, output="1500\n"),
        ]
    )
    ctx = _build_roce_ctx(mock_fault_context, ssh, mtu=1500, original_mtu=9100)

    inject = await scenario.inject(ctx)
    assert inject.success is True
    assert ctx.params["original_mtu"] == 9100

    entry = ctx.rollback.get_entry(ctx.fault_id)
    assert entry is not None
    assert entry.recover_params["mtu"] == 9100


@pytest.mark.asyncio
async def test_roce_mtu_guard_block_returns_error(mock_fault_context, monkeypatch):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(responses=[])
    ctx = _build_roce_ctx(mock_fault_context, ssh)

    monkeypatch.setattr(
        ctx.guard,
        "check_command",
        lambda command, channel: (_ for _ in ()).throw(SafetyViolationError("blocked")),
    )

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "blocked" in (inject.error or "")


@pytest.mark.asyncio
async def test_roce_mtu_fails_when_set_command_fails(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="9000\n"),
            ChannelResult(success=False, error="set mtu failed"),
        ]
    )
    ctx = _build_roce_ctx(mock_fault_context, ssh)

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert inject.error == "set mtu failed"


@pytest.mark.asyncio
async def test_roce_mtu_fails_when_post_change_verification_mismatch(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(
        responses=[
            ChannelResult(success=True, output="9000\n"),
            ChannelResult(success=True),
            ChannelResult(success=True, output="1400\n"),
        ]
    )
    ctx = _build_roce_ctx(mock_fault_context, ssh, mtu=1500)

    inject = await scenario.inject(ctx)
    assert inject.success is False
    assert "MTU verification failed" in (inject.error or "")


@pytest.mark.asyncio
async def test_roce_mtu_verify_returns_true_for_dry_run(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(responses=[ChannelResult(success=True, dry_run=True)])
    ctx = _build_roce_ctx(mock_fault_context, ssh)

    result = await scenario.verify(ctx)
    assert result is True


@pytest.mark.asyncio
async def test_roce_mtu_verify_returns_false_on_mismatch(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(responses=[ChannelResult(success=True, output="1400\n")])
    ctx = _build_roce_ctx(mock_fault_context, ssh, original_mtu=9000)

    result = await scenario.verify(ctx)
    assert result is False


@pytest.mark.asyncio
async def test_roce_mtu_recover_marks_failed_on_error(mock_fault_context):
    scenario = RoCEMTUMismatchScenario()
    ssh = _DummySSH(responses=[ChannelResult(success=False, error="recover failed", dry_run=False)])
    ctx = _build_roce_ctx(mock_fault_context, ssh)

    ctx.rollback.record(
        fault_id=ctx.fault_id,
        channel="ssh",
        target=ctx.target_node,
        inject_action="mtu_change",
        inject_params={"interface": "eth0", "mtu": 1500},
        recover_action="mtu_restore",
        recover_params={"interface": "eth0", "mtu": 9000},
    )

    recover = await scenario.recover(ctx)
    assert recover.success is False
    assert recover.error == "recover failed"

    entry = ctx.rollback.get_entry(ctx.fault_id)
    assert entry is not None
    assert entry.status == "failed"
