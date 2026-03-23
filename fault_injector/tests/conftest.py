"""
pytest fixtures for Fault Injector mock tests.

This module provides reusable fixtures for testing channels and scenarios
without requiring actual SSH connections or hardware.

Extensibility:
- Use register_channel_fixture() to add new channel fixtures
- Use register_scenario_fixture() to add new scenario fixtures
- Fixtures can be combined and overridden in test classes
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Callable, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fault_injector.config.schema import (
    ChannelResult,
    SSHConfig,
    TargetNodeConfig,
    SafetyConfig,
)
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from lib.channels.ssh import SSHChannel
from fault_injector.scenarios.base import FaultContext


# =============================================================================
# Event Loop Fixture
# =============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# =============================================================================
# Configuration Fixtures
# =============================================================================

@pytest.fixture
def mock_ssh_config() -> SSHConfig:
    """Mock SSH configuration for testing."""
    return SSHConfig(
        host="192.168.1.100",
        port=22,
        user="testuser",
        key_file="~/.ssh/id_rsa",
        password=None,
        use_sudo=True,
    )


@pytest.fixture
def mock_target_node(mock_ssh_config: SSHConfig) -> TargetNodeConfig:
    """Mock target node configuration for testing."""
    return TargetNodeConfig(
        name="test-node-1",
        ssh=mock_ssh_config,
        interface="eth0",
        roles=["test"],
        redfish=None,
    )


@pytest.fixture
def mock_inventory(mock_target_node: TargetNodeConfig) -> dict[str, TargetNodeConfig]:
    """Mock inventory with test nodes."""
    return {mock_target_node.name: mock_target_node}


# =============================================================================
# Temporary Directory and File Fixtures
# =============================================================================

@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_journal_path(temp_dir: Path) -> Path:
    """Temporary path for rollback journal."""
    return temp_dir / "rollback.jsonl"


# =============================================================================
# Safety and Rollback Fixtures
# =============================================================================

@pytest.fixture
def safety_config() -> SafetyConfig:
    """Safety configuration for testing."""
    return SafetyConfig(
        require_confirmation=False,
        auto_recover_timeout=60,
        dry_run=True,
        max_concurrent_faults=3,
        excluded_nodes=[],
    )


@pytest.fixture
def safety_guard(safety_config: SafetyConfig) -> SafetyGuard:
    """Safety guard instance for testing."""
    return SafetyGuard(safety_config)


@pytest.fixture
def rollback_journal(temp_journal_path: Path) -> RollbackJournal:
    """Rollback journal instance for testing."""
    return RollbackJournal(temp_journal_path)


# =============================================================================
# Mock Channel Fixtures
# =============================================================================

@pytest.fixture
def mock_ssh_channel(
    mock_inventory: dict[str, TargetNodeConfig],
    rollback_journal: RollbackJournal,
    safety_guard: SafetyGuard,
) -> SSHChannel:
    """
    Mock SSH channel for testing in dry-run mode.
    
    This channel will not make actual SSH connections.
    """
    return SSHChannel(
        inventory=mock_inventory,
        dry_run=True,
        wal=rollback_journal,
        guard=safety_guard,
    )


@pytest.fixture
def mock_ssh_channel_with_mocks(
    mock_inventory: dict[str, TargetNodeConfig],
    rollback_journal: RollbackJournal,
    safety_guard: SafetyGuard,
) -> tuple[SSHChannel, AsyncMock]:
    """
    SSH channel with mockable connection for testing actual command execution.
    
    Returns:
        tuple: (SSHChannel instance, mock connection)
    """
    with patch("lib.channels.ssh.asyncssh") as mock_asyncssh:
        # Setup mock connection
        mock_conn = AsyncMock()
        mock_conn.is_closed.return_value = False
        mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
        mock_asyncssh.Error = Exception
        
        channel = SSHChannel(
            inventory=mock_inventory,
            dry_run=False,  # Not dry-run, but with mocked connection
            wal=rollback_journal,
            guard=safety_guard,
        )
        
        yield channel, mock_conn


class FakeSwitchChannel:
    """Configurable fake switch channel for RDMA scenario tests."""

    def __init__(
        self,
        *,
        missing_status: bool = False,
        missing_config: bool = False,
        apply_fail: bool = False,
        shutdown_fail: bool = False,
        bringup_fail: bool = False,
        verify_false: bool = False,
    ):
        self.missing_status = missing_status
        self.missing_config = missing_config
        self.apply_fail = apply_fail
        self.shutdown_fail = shutdown_fail
        self.bringup_fail = bringup_fail
        self.verify_false = verify_false

        self.admin_status = "up"
        self.description = "baseline"
        self.pvid = 1
        self.link_type = "trunk"
        self.cli_commands: list[list[str]] = []
        self.pfc_state: int = 1
        self.pfc_dot1p: list[int] = []
        self.wred_queue_lines: dict[int, set[str]] = {}
        self.dscp_dot1p_map: dict[int, int] = {26: 3}

    def get_interface_status(self, switch: str, interface: str):
        _ = (switch, interface)
        if self.missing_status:
            return None
        return MagicMock(
            admin_status=self.admin_status,
            name="TwoHundredGigE1/0/4",
            abbreviated_name="200GE1/0/4",
        )

    def get_interface_config(self, switch: str, interface: str):
        _ = (switch, interface)
        if self.missing_config:
            return None
        return MagicMock(
            description=self.description,
            pvid=self.pvid,
            link_type=self.link_type,
        )

    def apply_interface_config(self, switch: str, interface: str, **kwargs: Any) -> ChannelResult:
        _ = (switch, interface)
        if self.apply_fail:
            return ChannelResult(success=False, error="apply failed")

        if "description" in kwargs and kwargs["description"] is not None:
            self.description = kwargs["description"]
        if "admin_status" in kwargs and kwargs["admin_status"] is not None:
            self.admin_status = "up" if kwargs["admin_status"] == 1 else "down"
        if "pvid" in kwargs and kwargs["pvid"] is not None:
            self.pvid = kwargs["pvid"]
        if "link_type" in kwargs and kwargs["link_type"] is not None:
            self.link_type = {1: "access", 2: "trunk", 3: "hybrid"}.get(kwargs["link_type"], "trunk")

        return ChannelResult(success=True)

    def apply_cli_commands(self, switch: str, commands: list[str]) -> ChannelResult:
        _ = switch
        if self.apply_fail:
            return ChannelResult(success=False, error="apply failed")
        self.cli_commands.append(list(commands))
        joined = " ".join(commands)
        if "priority-flow-control no-drop dot1p" in joined and "undo priority-flow-control no-drop dot1p" not in joined:
            tail = joined.split("priority-flow-control no-drop dot1p", 1)[1].strip()
            values: list[int] = []
            for part in tail.replace(",", " ").split():
                if "-" in part:
                    start, end = part.split("-", 1)
                    if start.isdigit() and end.isdigit():
                        values.extend(range(int(start), int(end) + 1))
                elif part.isdigit():
                    values.append(int(part))
            self.pfc_dot1p = sorted(set(v for v in values if 0 <= v <= 7))
        if "undo priority-flow-control no-drop dot1p" in joined:
            self.pfc_dot1p = []

        for cmd in commands:
            text = str(cmd).strip()
            if text.startswith("qos wred queue "):
                parts = text.split()
                if len(parts) >= 4 and parts[3].isdigit():
                    qid = int(parts[3])
                    self.wred_queue_lines.setdefault(qid, set()).add(text)
            if text.startswith("undo qos wred queue "):
                origin = text.replace("undo ", "", 1)
                parts = origin.split()
                if len(parts) >= 4 and parts[3].isdigit():
                    qid = int(parts[3])
                    if origin.startswith(f"qos wred queue {qid} drop-level ") and len(parts) >= 6:
                        level = parts[5]
                        keep = {
                            line
                            for line in self.wred_queue_lines.setdefault(qid, set())
                            if not line.startswith(f"qos wred queue {qid} drop-level {level} ")
                        }
                        self.wred_queue_lines[qid] = keep
                    else:
                        self.wred_queue_lines.setdefault(qid, set()).discard(origin)
            if text.startswith("import ") and " export " in text:
                parts = text.split()
                if len(parts) == 4 and parts[0] == "import" and parts[2] == "export":
                    if parts[1].isdigit() and parts[3].isdigit():
                        self.dscp_dot1p_map[int(parts[1])] = int(parts[3])
        return ChannelResult(success=True)

    def run_cli_execution(self, switch: str, command: str) -> ChannelResult:
        _ = (switch, command)
        cmd = str(command).strip()
        if cmd.startswith("display qos map-table dscp-dot1p"):
            mapping = sorted(self.dscp_dot1p_map.items())
            body = "\n".join(f"{dscp:>4}    :    {dot1p}" for dscp, dot1p in mapping)
            output = (
                "<switch>display qos map-table dscp-dot1p\n"
                "MAP-TABLE NAME: dscp-dot1p   TYPE: pre-define\n\n"
                "IMPORT  :  EXPORT\n\n"
                f"{body}\n"
            )
            return ChannelResult(success=True, output=output)
        if cmd.startswith("display current-configuration interface "):
            q_lines: list[str] = []
            for _, lines in sorted(self.wred_queue_lines.items()):
                q_lines.extend(sorted(lines))
            q_text = "\n".join(q_lines)
            output = (
                "<switch>display current-configuration interface TwoHundredGigE1/0/4\n"
                "#\n"
                "interface TwoHundredGigE1/0/4\n"
                f"{q_text}\n"
                "#\n"
                "return\n"
            )
            return ChannelResult(success=True, output=output)
        dot = " ".join([f"{self.pfc_dot1p[0]}-{self.pfc_dot1p[-1]}"]) if self.pfc_dot1p == list(range(8)) else (
            ",".join(str(v) for v in self.pfc_dot1p) if self.pfc_dot1p else ""
        )
        output = (
            "Interface                        AdminMode  OperMode  Dot1pList   Prio  Recv       Send       Inpps      Outpps\n"
            "---------------------------------------------------------------------------------------------------\n"
            f"200GE1/0/4                       Enabled    Enabled   {dot}         5     18         0          0          0\n"
        )
        return ChannelResult(success=True, output=output)

    def get_pfc_port_profile(self, switch: str, interface: str):
        _ = (switch, interface)
        return self.pfc_state, list(self.pfc_dot1p)

    def apply_pfc_no_drop_dot1p(
        self,
        switch: str,
        interface: str,
        dot1p_priorities: list[int],
        *,
        state: int = 1,
        clear_existing: bool = False,
    ) -> ChannelResult:
        _ = (switch, interface, clear_existing)
        if self.apply_fail:
            return ChannelResult(success=False, error="apply failed")
        self.pfc_state = state
        self.pfc_dot1p = sorted(set(int(p) for p in dot1p_priorities))
        return ChannelResult(success=True)

    def shutdown_port(self, switch: str, interface: str, fault_id: str | None = None) -> ChannelResult:
        _ = (switch, interface, fault_id)
        if self.shutdown_fail:
            return ChannelResult(success=False, error="shutdown failed")
        self.admin_status = "down"
        return ChannelResult(success=True)

    def bringup_port(self, switch: str, interface: str, fault_id: str | None = None) -> ChannelResult:
        _ = (switch, interface, fault_id)
        if self.bringup_fail:
            return ChannelResult(success=False, error="bringup failed")
        self.admin_status = "up"
        return ChannelResult(success=True)

    def verify_admin_state(self, switch: str, interface: str, expected: str) -> bool:
        _ = (switch, interface)
        if self.verify_false:
            return False
        return self.admin_status == expected


@pytest.fixture
def fake_switch_channel_factory():
    """Factory fixture to build switch channel doubles with failure toggles."""

    def _build(**kwargs: Any) -> FakeSwitchChannel:
        return FakeSwitchChannel(**kwargs)

    return _build


@pytest.fixture
def fake_switch_channel() -> FakeSwitchChannel:
    return FakeSwitchChannel()


# =============================================================================
# Fault Context Fixtures
# =============================================================================

@pytest.fixture
def mock_fault_context(
    mock_ssh_channel: SSHChannel,
    rollback_journal: RollbackJournal,
    safety_guard: SafetyGuard,
) -> FaultContext:
    """Mock fault context for testing scenarios."""
    return FaultContext(
        ssh=mock_ssh_channel,
        rollback=rollback_journal,
        guard=safety_guard,
        target_node="test-node-1",
        params={
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
            "loss_pct": 0,
        },
        fault_id="test-fault-001",
        session_id="test-session",
    )


@pytest.fixture
def network_jitter_context(mock_fault_context: FaultContext) -> FaultContext:
    """Context for NetworkJitterScenario testing."""
    mock_fault_context.params = {
        "interface": "eth0",
        "delay_ms": 50,
        "jitter_ms": 100,
        "distribution": "pareto",
        "loss_pct": 0,
        "duration": 30,
    }
    mock_fault_context.fault_id = "network_jitter_test-node-1_20260225_120000"
    return mock_fault_context


@pytest.fixture
def roce_mtu_context(mock_fault_context: FaultContext) -> FaultContext:
    """Context for RoCEMTUMismatchScenario testing."""
    mock_fault_context.params = {
        "interface": "eth0",
        "mtu": 1500,
        "original_mtu": 9000,
    }
    mock_fault_context.fault_id = "roce_mtu_mismatch_test-node-1_20260225_120000"
    return mock_fault_context


# =============================================================================
# Extensibility Helpers
# =============================================================================

def register_channel_fixture(
    name: str,
    channel_class: type,
    config_factory: Callable[[], Any],
) -> Callable:
    """
    Factory function to register new channel fixtures.
    
    Usage:
        @register_channel_fixture("my_channel", MyChannel, lambda: MyConfig())
        def my_channel_fixture():
            pass
    
    Args:
        name: Fixture name
        channel_class: Channel class to instantiate
        config_factory: Factory function for channel configuration
        
    Returns:
        Fixture decorator
    """
    @pytest.fixture(name=name)
    def fixture(rollback_journal: RollbackJournal, safety_guard: SafetyGuard):
        config = config_factory()
        return channel_class(
            dry_run=True,
            wal=rollback_journal,
            guard=safety_guard,
            **config.__dict__ if hasattr(config, '__dict__') else config,
        )
    return fixture


def register_scenario_fixture(
    name: str,
    scenario_class: type,
    params_factory: Callable[[], dict],
) -> Callable:
    """
    Factory function to register new scenario fixtures.
    
    Usage:
        @register_scenario_fixture("my_scenario", MyScenario, lambda: {"param": "value"})
        def my_scenario_fixture():
            pass
    
    Args:
        name: Fixture name
        scenario_class: Scenario class to instantiate
        params_factory: Factory function for scenario parameters
        
    Returns:
        Fixture decorator
    """
    @pytest.fixture(name=name)
    def fixture(mock_fault_context: FaultContext):
        scenario = scenario_class()
        mock_fault_context.params = params_factory()
        mock_fault_context.fault_id = f"{scenario.name}_test_{name}"
        return scenario, mock_fault_context
    return fixture


# =============================================================================
# Test Data Fixtures
# =============================================================================

@pytest.fixture
def sample_tc_show_output() -> str:
    """Sample output from 'tc qdisc show' command."""
    return "qdisc mq 0: root\n"


@pytest.fixture
def sample_tc_netem_output() -> str:
    """Sample output from 'tc qdisc show' with netem."""
    return "qdisc netem 1: root refcnt 2 limit 1000 delay 50.0ms 100ms\n"


@pytest.fixture
def sample_mtu_output() -> str:
    """Sample output from MTU check."""
    return "9000\n"


# =============================================================================
# Parametrized Fixtures
# =============================================================================

@pytest.fixture(params=[
    ("normal", {"delay_ms": 50, "jitter_ms": 0}),
    ("pareto", {"delay_ms": 50, "jitter_ms": 100}),
    ("loss", {"delay_ms": 50, "jitter_ms": 100, "loss_pct": 5}),
])
def network_jitter_variants(request) -> tuple[str, dict]:
    """Parametrized network jitter configurations."""
    return request.param


@pytest.fixture(params=[
    (1500, 9000),   # Standard to Jumbo
    (9000, 1500),   # Jumbo to Standard
    (5000, 9000),   # Custom to Jumbo
])
def mtu_variants(request) -> tuple[int, int]:
    """Parametrized MTU configurations."""
    return request.param
