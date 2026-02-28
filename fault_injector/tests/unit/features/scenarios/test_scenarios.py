"""
Tests for Fault Injection Scenarios.

Tests cover:
- NetworkJitterScenario (RC-2)
- RoCEMTUMismatchScenario (F-5)

Extensible design allows adding tests for other scenarios.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import tempfile

from fault_injector.scenarios.vllm_latency import (
    NetworkJitterScenario,
)
from fault_injector.scenarios.rdma_anomaly import (
    RoCEMTUMismatchScenario,
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RDMAQoSDowngradeScenario,
)
from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import (
    ChannelResult,
    InjectResult,
    RecoverResult,
    SSHConfig,
    TargetNodeConfig,
    SafetyConfig,
)
from lib.channels.ssh import SSHChannel
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal


# =============================================================================
# Test Fixtures
# =============================================================================

class _MockSwitchChannel:
    def __init__(self):
        self.admin_status = "up"
        self.description = "baseline"
        self.pvid = 1
        self.link_type = "trunk"

    def get_interface_status(self, switch, interface):
        _ = (switch, interface)
        return MagicMock(admin_status=self.admin_status)

    def get_interface_config(self, switch, interface):
        _ = (switch, interface)
        return MagicMock(
            description=self.description,
            pvid=self.pvid,
            link_type=self.link_type,
        )

    def apply_interface_config(self, switch, interface, **kwargs):
        _ = (switch, interface)
        if "description" in kwargs and kwargs["description"] is not None:
            self.description = kwargs["description"]
        if "admin_status" in kwargs and kwargs["admin_status"] is not None:
            self.admin_status = "up" if kwargs["admin_status"] == 1 else "down"
        if "pvid" in kwargs and kwargs["pvid"] is not None:
            self.pvid = kwargs["pvid"]
        if "link_type" in kwargs and kwargs["link_type"] is not None:
            self.link_type = {1: "access", 2: "trunk", 3: "hybrid"}.get(kwargs["link_type"], "trunk")
        return ChannelResult(success=True)

    def shutdown_port(self, switch, interface, fault_id=None):
        _ = (switch, interface, fault_id)
        self.admin_status = "down"
        return ChannelResult(success=True)

    def bringup_port(self, switch, interface, fault_id=None):
        _ = (switch, interface, fault_id)
        self.admin_status = "up"
        return ChannelResult(success=True)

    def verify_admin_state(self, switch, interface, expected):
        _ = (switch, interface)
        return self.admin_status == expected

@pytest.fixture
def temp_journal_path():
    """Temporary path for rollback journal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "rollback.jsonl"


@pytest.fixture
def safety_config():
    """Safety configuration for testing."""
    return SafetyConfig(
        require_confirmation=False,
        auto_recover_timeout=60,
        dry_run=True,
    )


@pytest.fixture
def safety_guard(safety_config):
    """Safety guard instance."""
    return SafetyGuard(safety_config)


@pytest.fixture
def rollback_journal(temp_journal_path):
    """Rollback journal instance."""
    return RollbackJournal(temp_journal_path)


@pytest.fixture
def mock_inventory():
    """Mock inventory with test nodes."""
    return {
        "test-node-1": TargetNodeConfig(
            name="test-node-1",
            ssh=SSHConfig(
                host="192.168.1.100",
                port=22,
                user="testuser",
                key_file="~/.ssh/id_rsa",
                use_sudo=True,
            ),
            interface="eth0",
        )
    }


@pytest.fixture
def mock_ssh_channel(mock_inventory, rollback_journal, safety_guard):
    """Mock SSH channel in dry-run mode."""
    return SSHChannel(
        inventory=mock_inventory,
        dry_run=True,
        wal=rollback_journal,
        guard=safety_guard,
    )


@pytest.fixture
def base_context(mock_ssh_channel, rollback_journal, safety_guard):
    """Base fault context for testing."""
    return FaultContext(
        ssh=mock_ssh_channel,
        rollback=rollback_journal,
        guard=safety_guard,
        target_node="test-node-1",
        params={},
        fault_id="test-fault-001",
        session_id="test-session",
    )

@pytest.fixture
def mock_switch_channel():
    return _MockSwitchChannel()


# =============================================================================
# NetworkJitterScenario Tests (RC-2)
# =============================================================================

class TestNetworkJitterScenario:
    """Tests for NetworkJitterScenario (RC-2)."""
    
    @pytest.fixture
    def scenario(self):
        """Create scenario instance."""
        return NetworkJitterScenario()
    
    @pytest.fixture
    def context(self, base_context):
        """Create context with network jitter parameters."""
        base_context.params = {
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
            "loss_pct": 0,
            "duration": 30,
        }
        base_context.fault_id = "network_jitter_test-node-1_20260225_120000"
        return base_context
    
    # Property Tests
    
    def test_scenario_name(self, scenario):
        """Test scenario name."""
        assert scenario.name == "network_jitter"
    
    def test_scenario_description(self, scenario):
        """Test scenario description."""
        assert "tc netem" in scenario.description.lower()
        assert "network jitter" in scenario.description.lower()

    def test_scenario_layer(self, scenario):
        """Test scenario layer."""
        assert scenario.layer == "os"
    
    def test_monitor_queries(self, scenario):
        """Test monitor queries."""
        queries = scenario.monitor_queries()
        
        assert isinstance(queries, dict)
        assert "inference_p50" in queries
        assert "inference_p95" in queries
        assert "vllm" in queries["inference_p50"]
    
    # Command Building Tests
    
    def test_build_tc_command_basic(self, scenario):
        """Test basic TC command building."""
        cmd = scenario._build_tc_command({
            "interface": "eth0",
            "delay_ms": 50,
        })
        
        assert "tc qdisc replace dev eth0 root netem delay 50ms" in cmd
    
    def test_build_tc_command_with_jitter(self, scenario):
        """Test TC command with jitter."""
        cmd = scenario._build_tc_command({
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
        })
        
        assert "50ms 100ms" in cmd
    
    def test_build_tc_command_with_distribution(self, scenario):
        """Test TC command with distribution."""
        cmd = scenario._build_tc_command({
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
        })
        
        assert "distribution pareto" in cmd
    
    def test_build_tc_command_with_loss(self, scenario):
        """Test TC command with packet loss."""
        cmd = scenario._build_tc_command({
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
            "loss_pct": 5,
        })
        
        assert "loss 5%" in cmd
    
    def test_build_recovery_command(self, scenario):
        """Test recovery command building."""
        cmd = scenario._build_recovery_command("eth0")
        
        assert "tc qdisc del dev eth0 root" in cmd
        assert "sudo" not in cmd
    
    # Inject/Recover/Verify Tests
    
    @pytest.mark.asyncio
    async def test_inject_dry_run(self, scenario, context):
        """Test inject in dry-run mode."""
        result = await scenario.inject(context)
        
        assert result.success is True
        assert result.fault_id == context.fault_id
    
    @pytest.mark.asyncio
    async def test_inject_writes_to_wal(self, scenario, context):
        """Test that inject writes to WAL."""
        await scenario.inject(context)
        
        # Check WAL has entry
        active = context.rollback.get_active_faults()
        fault_ids = [e.fault_id for e in active]
        assert context.fault_id in fault_ids
    
    @pytest.mark.asyncio
    async def test_recover_dry_run(self, scenario, context):
        """Test recover in dry-run mode."""
        # First inject
        await scenario.inject(context)
        
        # Then recover
        result = await scenario.recover(context)
        
        assert result.success is True
        assert result.fault_id == context.fault_id
    
    @pytest.mark.asyncio
    async def test_recover_marks_recovered_in_wal(self, scenario, context):
        """Test that recover marks entry as recovered in WAL."""
        await scenario.inject(context)
        await scenario.recover(context)
        
        active = context.rollback.get_active_faults()
        fault_ids = [e.fault_id for e in active]
        assert context.fault_id not in fault_ids
    
    @pytest.mark.asyncio
    async def test_verify_dry_run(self, scenario, context):
        """Test verify in dry-run mode."""
        await scenario.inject(context)
        await scenario.recover(context)
        
        result = await scenario.verify(context)
        
        # In dry-run mode, verify should succeed
        assert result is True
    
    @pytest.mark.asyncio
    async def test_full_cycle(self, scenario, context):
        """Test full inject -> recover -> verify cycle."""
        # Inject
        inject_result = await scenario.inject(context)
        assert inject_result.success is True
        
        # Recover
        recover_result = await scenario.recover(context)
        assert recover_result.success is True
        
        # Verify
        verified = await scenario.verify(context)
        assert verified is True


# =============================================================================
# RoCEMTUMismatchScenario Tests (F-5)
# =============================================================================

class TestRoCEMTUMismatchScenario:
    """Tests for RoCEMTUMismatchScenario (F-5)."""
    
    @pytest.fixture
    def scenario(self):
        """Create scenario instance."""
        return RoCEMTUMismatchScenario()
    
    @pytest.fixture
    def context(self, base_context):
        """Create context with MTU parameters."""
        base_context.params = {
            "interface": "eth0",
            "mtu": 1500,
            "original_mtu": 9000,
        }
        base_context.fault_id = "roce_mtu_mismatch_test-node-1_20260225_120000"
        return base_context
    
    # Property Tests
    
    def test_scenario_name(self, scenario):
        """Test scenario name."""
        assert scenario.name == "roce_mtu_mismatch"
    
    def test_scenario_description(self, scenario):
        """Test scenario description."""
        assert "MTU" in scenario.description
        assert "RoCE" in scenario.description or "roce" in scenario.description.lower()
    
    def test_scenario_layer(self, scenario):
        """Test scenario layer."""
        assert scenario.layer == "os"
    
    def test_monitor_queries(self, scenario):
        """Test monitor queries."""
        queries = scenario.monitor_queries()
        
        assert isinstance(queries, dict)
        assert "rdma_throughput" in queries
    
    # Inject/Recover/Verify Tests
    
    @pytest.mark.asyncio
    async def test_inject_dry_run(self, scenario, context):
        """Test inject in dry-run mode."""
        result = await scenario.inject(context)
        
        assert result.success is True
        assert result.fault_id == context.fault_id
    
    @pytest.mark.asyncio
    async def test_inject_writes_to_wal(self, scenario, context):
        """Test that inject writes to WAL."""
        await scenario.inject(context)
        
        active = context.rollback.get_active_faults()
        fault_ids = [e.fault_id for e in active]
        assert context.fault_id in fault_ids
    
    @pytest.mark.asyncio
    async def test_recover_dry_run(self, scenario, context):
        """Test recover in dry-run mode."""
        await scenario.inject(context)
        result = await scenario.recover(context)
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_recover_marks_recovered_in_wal(self, scenario, context):
        """Test that recover marks entry as recovered in WAL."""
        await scenario.inject(context)
        await scenario.recover(context)
        
        active = context.rollback.get_active_faults()
        fault_ids = [e.fault_id for e in active]
        assert context.fault_id not in fault_ids
    
    @pytest.mark.asyncio
    async def test_verify_dry_run(self, scenario, context):
        """Test verify in dry-run mode."""
        await scenario.inject(context)
        await scenario.recover(context)
        
        result = await scenario.verify(context)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_full_cycle(self, scenario, context):
        """Test full inject -> recover -> verify cycle."""
        inject_result = await scenario.inject(context)
        assert inject_result.success is True
        
        recover_result = await scenario.recover(context)
        assert recover_result.success is True
        
        verified = await scenario.verify(context)
        assert verified is True
    
    # MTU-Specific Tests
    
    @pytest.mark.asyncio
    async def test_inject_with_different_mtu_values(self, scenario, context):
        """Test inject with different MTU values."""
        test_cases = [
            {"mtu": 1500, "original_mtu": 9000},
            {"mtu": 5000, "original_mtu": 9000},
            {"mtu": 9000, "original_mtu": 1500},
        ]
        
        for params in test_cases:
            context.params = {**context.params, **params}
            result = await scenario.inject(context)
            assert result.success is True
            
            await scenario.recover(context)


class TestRDMASwitchScenarios:
    @pytest.fixture
    def context(self, base_context, mock_switch_channel):
        base_context.switch = mock_switch_channel
        base_context.params = {
            "switch": "sw1",
            "interface": "GE1/0/4",
            "flap_duration": 0,
        }
        base_context.fault_id = "rdma-switch-test"
        return base_context

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scenario_cls",
        [
            PFCDeadlockScenario,
            ECNMisconfigurationScenario,
            RDMALoadImbalanceScenario,
            RDMALinkFlapScenario,
            RDMAQoSDowngradeScenario,
        ],
    )
    async def test_switch_scenario_inject_recover_cycle(self, scenario_cls, context):
        scenario = scenario_cls()
        inject = await scenario.inject(context)
        assert inject.success is True

        active = context.rollback.get_active_faults()
        assert any(entry.fault_id == context.fault_id for entry in active)

        recover = await scenario.recover(context)
        assert recover.success is True

        active_after = context.rollback.get_active_faults()
        assert all(entry.fault_id != context.fault_id for entry in active_after)


# =============================================================================
# Parametrized Tests
# =============================================================================

class TestNetworkJitterParametrized:
    """Parametrized tests for NetworkJitterScenario."""
    
    @pytest.fixture
    def scenario(self):
        return NetworkJitterScenario()
    
    @pytest.fixture
    def context(self, base_context):
        base_context.fault_id = "network_jitter_test_param"
        return base_context
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("delay,jitter,distribution,loss", [
        (50, 0, "normal", 0),
        (50, 100, "pareto", 0),
        (100, 200, "pareto", 5),
        (10, 50, "normal", 1),
    ])
    async def test_various_configurations(
        self, scenario, context, delay, jitter, distribution, loss
    ):
        """Test various network jitter configurations."""
        context.params = {
            "interface": "eth0",
            "delay_ms": delay,
            "jitter_ms": jitter,
            "distribution": distribution,
            "loss_pct": loss,
        }
        
        # Inject
        result = await scenario.inject(context)
        assert result.success is True
        
        # Recover
        result = await scenario.recover(context)
        assert result.success is True


class TestRoCEMTUParametrized:
    """Parametrized tests for RoCEMTUMismatchScenario."""
    
    @pytest.fixture
    def scenario(self):
        return RoCEMTUMismatchScenario()
    
    @pytest.fixture
    def context(self, base_context):
        base_context.fault_id = "roce_mtu_test_param"
        return base_context
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("mtu,original_mtu", [
        (1500, 9000),
        (9000, 1500),
        (5000, 9000),
        (4000, 9000),
    ])
    async def test_various_mtu_configurations(
        self, scenario, context, mtu, original_mtu
    ):
        """Test various MTU configurations."""
        context.params = {
            "interface": "eth0",
            "mtu": mtu,
            "original_mtu": original_mtu,
        }
        
        # Inject
        result = await scenario.inject(context)
        assert result.success is True
        
        # Recover
        result = await scenario.recover(context)
        assert result.success is True


# =============================================================================
# Scenario Registry Tests
# =============================================================================

class TestScenarioRegistry:
    """Tests for scenario registration."""
    
    def test_network_jitter_registered(self):
        """Test that NetworkJitterScenario is registered."""
        from fault_injector.scenarios.registry import get_scenario
        
        scenario = get_scenario("network_jitter")
        assert scenario is not None
        assert scenario.name == "network_jitter"
    
    def test_roce_mtu_registered(self):
        """Test that RoCEMTUMismatchScenario is registered."""
        from fault_injector.scenarios.registry import get_scenario
        
        scenario = get_scenario("roce_mtu_mismatch")
        assert scenario is not None
        assert scenario.name == "roce_mtu_mismatch"
    
    def test_list_scenarios_includes_both(self):
        """Test that list_scenarios includes both scenarios."""
        from fault_injector.scenarios.registry import list_scenarios
        
        scenarios = list_scenarios()
        names = [s["name"] for s in scenarios]
        
        assert "network_jitter" in names
        assert "roce_mtu_mismatch" in names


# =============================================================================
# Extensibility: Template for Adding New Scenario Tests
# =============================================================================

class ScenarioTestTemplate:
    """
    Template class for testing new scenarios.
    
    To add tests for a new scenario:
    1. Copy this class and rename
    2. Implement the fixtures
    3. Add scenario-specific tests
    
    Example:
        class TestMyNewScenario(ScenarioTestTemplate):
            @pytest.fixture
            def scenario(self):
                return MyNewScenario()
            
            @pytest.fixture
            def context(self, base_context):
                base_context.params = {"my_param": "value"}
                return base_context
    """
    
    @pytest.fixture
    def scenario(self) -> BaseScenario:
        """Override: Return the scenario instance to test."""
        raise NotImplementedError
    
    @pytest.fixture
    def context(self) -> FaultContext:
        """Override: Return the fault context for testing."""
        raise NotImplementedError
    
    # Standard tests (inherited)
    
    def test_scenario_name(self, scenario):
        """Test scenario has a name."""
        assert scenario.name is not None
    
    def test_scenario_description(self, scenario):
        """Test scenario has a description."""
        assert scenario.description is not None
    
    def test_scenario_layer(self, scenario):
        """Test scenario has a valid layer."""
        assert scenario.layer in ["hardware", "os", "platform", "service"]
    
    @pytest.mark.asyncio
    async def test_inject_returns_result(self, scenario, context):
        """Test inject returns InjectResult."""
        result = await scenario.inject(context)
        assert isinstance(result, InjectResult)
    
    @pytest.mark.asyncio
    async def test_recover_returns_result(self, scenario, context):
        """Test recover returns RecoverResult."""
        await scenario.inject(context)
        result = await scenario.recover(context)
        assert isinstance(result, RecoverResult)
    
    @pytest.mark.asyncio
    async def test_verify_returns_bool(self, scenario, context):
        """Test verify returns boolean."""
        await scenario.inject(context)
        await scenario.recover(context)
        result = await scenario.verify(context)
        assert isinstance(result, bool)

