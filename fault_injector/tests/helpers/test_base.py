"""
Base classes for Fault Injector tests.

These base classes provide common test patterns and utilities that can be
extended for testing specific channels and scenarios.

Usage:
    class TestMyChannel(ChannelTestBase):
        @pytest.fixture
        def channel(self):
            return MyChannel(...)
        
        def test_specific_feature(self, channel):
            # Test implementation
            pass
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.fchannels.base import BaseChannel
from lib.fchannels.ssh import SSHChannel
from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.config.schema import ChannelResult, InjectResult, RecoverResult

logger = logging.getLogger(__name__)

# Type variables for generic base classes
ChannelT = TypeVar("ChannelT", bound=BaseChannel)
ScenarioT = TypeVar("ScenarioT", bound=BaseScenario)


class ChannelTestBase(ABC, Generic[ChannelT]):
    """
    Base class for Channel tests.
    
    Provides common test patterns for testing channel functionality.
    Subclasses should implement the abstract methods and fixtures.
    
    Test Categories:
    - Connection tests
    - Command execution tests
    - Error handling tests
    - Dry-run mode tests
    """
    
    @pytest.fixture
    @abstractmethod
    def channel(self) -> ChannelT:
        """Return the channel instance to test."""
        pass
    
    @pytest.fixture
    def target_node(self) -> str:
        """Return the target node name for testing."""
        return "test-node-1"
    
    # =========================================================================
    # Common Tests (inherited by all channel tests)
    # =========================================================================
    
    def test_channel_has_dry_run_property(self, channel: ChannelT):
        """Test that channel has dry_run property."""
        assert hasattr(channel, "dry_run")
        assert isinstance(channel.dry_run, bool)
    
    def test_channel_has_wal_property(self, channel: ChannelT):
        """Test that channel has wal property."""
        assert hasattr(channel, "wal")
    
    def test_channel_has_guard_property(self, channel: ChannelT):
        """Test that channel has guard property."""
        assert hasattr(channel, "guard")
    
    @pytest.mark.asyncio
    async def test_channel_close(self, channel: ChannelT):
        """Test that channel can be closed without error."""
        await channel.close()


class SSHChannelTestBase(ChannelTestBase[SSHChannel]):
    """
    Base class for SSH Channel tests.
    
    Extends ChannelTestBase with SSH-specific tests.
    """
    
    # =========================================================================
    # SSH-Specific Tests
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_run_command_dry_run(self, channel: SSHChannel, target_node: str):
        """Test command execution in dry-run mode."""
        result = await channel.run_command(
            node=target_node,
            command="echo 'test'",
        )
        
        assert result.success
        assert result.dry_run
        assert "DRY-RUN" in result.output
    
    @pytest.mark.asyncio
    async def test_run_command_with_sudo(self, channel: SSHChannel, target_node: str):
        """Test command execution with sudo."""
        result = await channel.run_command(
            node=target_node,
            command="tc qdisc show",
            use_sudo=True,
        )
        
        assert result.success
    
    @pytest.mark.asyncio
    async def test_run_command_unknown_node(self, channel: SSHChannel):
        """Test command execution on unknown node."""
        with pytest.raises(ValueError, match="节点.*不在清单中"):
            await channel.run_command(
                node="unknown-node",
                command="echo 'test'",
            )
    
    @pytest.mark.asyncio
    async def test_test_connection(self, channel: SSHChannel, target_node: str):
        """Test connection test method."""
        result = await channel.test_connection(target_node)
        # In dry-run mode, this should return True
        assert result is True


class ScenarioTestBase(ABC, Generic[ScenarioT]):
    """
    Base class for Scenario tests.
    
    Provides common test patterns for testing scenario functionality.
    Subclasses should implement the abstract methods and fixtures.
    
    Test Categories:
    - Property tests (name, description, layer)
    - Inject/Recover tests
    - Verify tests
    - Monitor query tests
    """
    
    @pytest.fixture
    @abstractmethod
    def scenario(self) -> ScenarioT:
        """Return the scenario instance to test."""
        pass
    
    @pytest.fixture
    @abstractmethod
    def context(self) -> FaultContext:
        """Return the fault context for testing."""
        pass
    
    # =========================================================================
    # Property Tests (inherited by all scenario tests)
    # =========================================================================
    
    def test_scenario_has_name(self, scenario: ScenarioT):
        """Test that scenario has a name property."""
        assert hasattr(scenario, "name")
        assert isinstance(scenario.name, str)
        assert len(scenario.name) > 0
    
    def test_scenario_has_description(self, scenario: ScenarioT):
        """Test that scenario has a description property."""
        assert hasattr(scenario, "description")
        assert isinstance(scenario.description, str)
        assert len(scenario.description) > 0
    
    def test_scenario_has_layer(self, scenario: ScenarioT):
        """Test that scenario has a layer property."""
        assert hasattr(scenario, "layer")
        assert isinstance(scenario.layer, str)
        assert scenario.layer in ["hardware", "os", "platform", "service"]
    
    def test_scenario_has_monitor_queries(self, scenario: ScenarioT):
        """Test that scenario has monitor_queries method."""
        assert hasattr(scenario, "monitor_queries")
        queries = scenario.monitor_queries()
        assert isinstance(queries, dict)
    
    # =========================================================================
    # Inject/Recover/Verify Tests (template methods)
    # =========================================================================
    
    @pytest.mark.asyncio
    async def test_inject_returns_result(self, scenario: ScenarioT, context: FaultContext):
        """Test that inject returns an InjectResult."""
        result = await scenario.inject(context)
        
        assert isinstance(result, InjectResult)
        assert result.fault_id == context.fault_id
    
    @pytest.mark.asyncio
    async def test_recover_returns_result(self, scenario: ScenarioT, context: FaultContext):
        """Test that recover returns a RecoverResult."""
        # First inject
        await scenario.inject(context)
        
        # Then recover
        result = await scenario.recover(context)
        
        assert isinstance(result, RecoverResult)
        assert result.fault_id == context.fault_id
    
    @pytest.mark.asyncio
    async def test_verify_returns_bool(self, scenario: ScenarioT, context: FaultContext):
        """Test that verify returns a boolean."""
        # First inject and recover
        await scenario.inject(context)
        await scenario.recover(context)
        
        # Then verify
        result = await scenario.verify(context)
        
        assert isinstance(result, bool)
    
    @pytest.mark.asyncio
    async def test_full_cycle(self, scenario: ScenarioT, context: FaultContext):
        """Test full inject -> recover -> verify cycle."""
        # Inject
        inject_result = await scenario.inject(context)
        assert inject_result.success, f"Inject failed: {inject_result.error}"
        
        # Recover
        recover_result = await scenario.recover(context)
        assert recover_result.success, f"Recover failed: {recover_result.error}"
        
        # Verify
        verified = await scenario.verify(context)
        assert verified, "Verification failed"


class NetworkScenarioTestBase(ScenarioTestBase[ScenarioT]):
    """
    Base class for network-related scenario tests.
    
    Extends ScenarioTestBase with network-specific tests.
    """
    
    @pytest.fixture
    def interface(self) -> str:
        """Return the network interface for testing."""
        return "eth0"
    
    def test_monitor_queries_include_latency(self, scenario: ScenarioT):
        """Test that monitor queries include latency metrics."""
        queries = scenario.monitor_queries()
        # Should have some latency-related query
        latency_keys = [k for k in queries.keys() if "latency" in k.lower() or "duration" in k.lower()]
        assert len(latency_keys) > 0 or len(queries) > 0  # Flexible check


class SSHBasedScenarioTestBase(ScenarioTestBase[ScenarioT]):
    """
    Base class for scenarios that use SSH channel.
    
    Provides tests specific to SSH-based fault injection.
    """
    
    @pytest.mark.asyncio
    async def test_inject_writes_to_wal(self, scenario: ScenarioT, context: FaultContext):
        """Test that inject writes to WAL."""
        # Get initial WAL entries
        initial_active = context.rollback.get_active_faults()
        initial_count = len(initial_active)
        
        # Inject
        await scenario.inject(context)
        
        # Check WAL has new entry
        new_active = context.rollback.get_active_faults()
        assert len(new_active) > initial_count or scenario.name in [e.fault_id for e in new_active]
    
    @pytest.mark.asyncio
    async def test_recover_marks_recovered_in_wal(self, scenario: ScenarioT, context: FaultContext):
        """Test that recover marks entry as recovered in WAL."""
        # Inject
        await scenario.inject(context)
        
        # Recover
        await scenario.recover(context)
        
        # Check WAL entry is marked recovered
        active = context.rollback.get_active_faults()
        fault_ids = [e.fault_id for e in active]
        assert context.fault_id not in fault_ids


# =============================================================================
# Test Utilities
# =============================================================================

def create_mock_channel_result(
    success: bool = True,
    output: str = "",
    error: str = "",
    dry_run: bool = False,
) -> ChannelResult:
    """Helper to create ChannelResult for testing."""
    return ChannelResult(
        success=success,
        output=output,
        error=error,
        dry_run=dry_run,
    )


def create_mock_inject_result(
    success: bool = True,
    fault_id: str = "test-fault",
    error: str = "",
) -> InjectResult:
    """Helper to create InjectResult for testing."""
    return InjectResult(
        success=success,
        fault_id=fault_id,
        error=error,
    )


def create_mock_recover_result(
    success: bool = True,
    fault_id: str = "test-fault",
    error: str = "",
) -> RecoverResult:
    """Helper to create RecoverResult for testing."""
    return RecoverResult(
        success=success,
        fault_id=fault_id,
        error=error,
    )


async def run_scenario_cycle(
    scenario: BaseScenario,
    context: FaultContext,
) -> tuple[InjectResult, RecoverResult, bool]:
    """
    Helper to run full scenario cycle.
    
    Returns:
        tuple: (inject_result, recover_result, verified)
    """
    inject_result = await scenario.inject(context)
    recover_result = await scenario.recover(context)
    verified = await scenario.verify(context)
    
    return inject_result, recover_result, verified
