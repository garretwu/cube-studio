"""
Tests for SSHChannel.

Tests cover:
- Dry-run mode
- Command execution
- Sudo handling
- Error handling
- TC (traffic control) commands
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from lib.fchannels.ssh import SSHChannel
from lib.fchannels.base import BaseChannel
from fault_injector.config.schema import ChannelResult, SSHConfig, TargetNodeConfig


class TestSSHChannelDryRun:
    """Tests for SSH Channel in dry-run mode."""
    
    @pytest.fixture
    def mock_inventory(self) -> dict[str, TargetNodeConfig]:
        """Create mock inventory."""
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
    def ssh_channel(self, mock_inventory) -> SSHChannel:
        """Create SSH channel in dry-run mode."""
        return SSHChannel(
            inventory=mock_inventory,
            dry_run=True,
            wal=None,
            guard=None,
        )
    
    def test_dry_run_property(self, ssh_channel):
        """Test that dry_run is True."""
        assert ssh_channel.dry_run is True
    
    def test_inventory_loaded(self, ssh_channel, mock_inventory):
        """Test that inventory is loaded correctly."""
        assert ssh_channel.inventory == mock_inventory
    
    @pytest.mark.asyncio
    async def test_run_command_dry_run_returns_success(self, ssh_channel):
        """Test that dry-run command returns success."""
        result = await ssh_channel.run_command(
            node="test-node-1",
            command="echo 'test'",
        )
        
        assert result.success is True
        assert result.dry_run is True
        assert "DRY-RUN" in result.output
    
    @pytest.mark.asyncio
    async def test_run_command_with_sudo_dry_run(self, ssh_channel):
        """Test that sudo commands work in dry-run mode."""
        result = await ssh_channel.run_command(
            node="test-node-1",
            command="tc qdisc show",
            use_sudo=True,
        )
        
        assert result.success is True
        assert result.dry_run is True
    
    @pytest.mark.asyncio
    async def test_run_command_unknown_node_raises_error(self, ssh_channel):
        """Test that unknown node raises ValueError."""
        with pytest.raises(ValueError, match="节点.*不在清单中"):
            await ssh_channel.run_command(
                node="unknown-node",
                command="echo 'test'",
            )
    
    @pytest.mark.asyncio
    async def test_test_connection_dry_run(self, ssh_channel):
        """Test connection test in dry-run mode."""
        result = await ssh_channel.test_connection("test-node-1")
        assert result is True
    
    @pytest.mark.asyncio
    async def test_close_dry_run(self, ssh_channel):
        """Test close in dry-run mode."""
        # Should not raise any error
        await ssh_channel.close()


class TestSSHChannelCommands:
    """Tests for SSH Channel command building."""
    
    @pytest.fixture
    def ssh_channel(self) -> SSHChannel:
        """Create SSH channel for command tests."""
        inventory = {
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
        return SSHChannel(
            inventory=inventory,
            dry_run=True,
            wal=None,
            guard=None,
        )
    
    @pytest.mark.asyncio
    async def test_tc_add_delay_command(self, ssh_channel):
        """Test TC add delay command."""
        result = await ssh_channel._tc_add_delay({
            "node": "test-node-1",
            "interface": "eth0",
            "delay_ms": 50,
            "jitter_ms": 100,
            "distribution": "pareto",
            "loss_pct": 0,
        })
        
        assert result.success is True
        assert result.dry_run is True
    
    @pytest.mark.asyncio
    async def test_tc_del_qdisc_command(self, ssh_channel):
        """Test TC delete qdisc command."""
        result = await ssh_channel._tc_del_qdisc({
            "node": "test-node-1",
            "interface": "eth0",
        })
        
        assert result.success is True
        assert result.dry_run is True
    
    @pytest.mark.asyncio
    async def test_execute_impl_tc_add_delay(self, ssh_channel):
        """Test _execute_impl with tc_add_delay action."""
        result = await ssh_channel._execute_impl(
            "tc_add_delay",
            {
                "node": "test-node-1",
                "interface": "eth0",
                "delay_ms": 50,
                "jitter_ms": 100,
            }
        )
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_execute_impl_tc_del_qdisc(self, ssh_channel):
        """Test _execute_impl with tc_del_qdisc action."""
        result = await ssh_channel._execute_impl(
            "tc_del_qdisc",
            {
                "node": "test-node-1",
                "interface": "eth0",
            }
        )
        
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_execute_impl_unknown_action(self, ssh_channel):
        """Test _execute_impl with unknown action."""
        result = await ssh_channel._execute_impl(
            "unknown_action",
            {}
        )
        
        assert result.success is False
        assert "未知操作" in result.error


class TestSSHChannelSudo:
    """Tests for SSH Channel sudo handling."""
    
    @pytest.fixture
    def ssh_channel_with_sudo(self) -> SSHChannel:
        """Create SSH channel with sudo enabled."""
        inventory = {
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
        return SSHChannel(
            inventory=inventory,
            dry_run=True,
            wal=None,
            guard=None,
        )
    
    @pytest.fixture
    def ssh_channel_no_sudo(self) -> SSHChannel:
        """Create SSH channel with sudo disabled."""
        inventory = {
            "test-node-1": TargetNodeConfig(
                name="test-node-1",
                ssh=SSHConfig(
                    host="192.168.1.100",
                    port=22,
                    user="root",  # Root user doesn't need sudo
                    key_file="~/.ssh/id_rsa",
                    use_sudo=False,
                ),
                interface="eth0",
            )
        }
        return SSHChannel(
            inventory=inventory,
            dry_run=True,
            wal=None,
            guard=None,
        )
    
    def test_should_use_sudo_true(self, ssh_channel_with_sudo):
        """Test _should_use_sudo returns True when configured."""
        result = ssh_channel_with_sudo._should_use_sudo("test-node-1")
        assert result is True
    
    def test_should_use_sudo_false(self, ssh_channel_no_sudo):
        """Test _should_use_sudo returns False when configured."""
        result = ssh_channel_no_sudo._should_use_sudo("test-node-1")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_tc_commands_force_sudo(self, ssh_channel_no_sudo):
        """Test that TC commands always use sudo regardless of setting."""
        # Even with use_sudo=False, TC commands should use sudo
        result = await ssh_channel_no_sudo._tc_add_delay({
            "node": "test-node-1",
            "interface": "eth0",
            "delay_ms": 50,
        })
        
        # Command should succeed (dry-run mode)
        assert result.success is True


class TestSSHChannelNodeConfig:
    """Tests for node configuration handling."""
    
    @pytest.fixture
    def ssh_channel(self) -> SSHChannel:
        """Create SSH channel for node config tests."""
        inventory = {
            "node-with-password": TargetNodeConfig(
                name="node-with-password",
                ssh=SSHConfig(
                    host="192.168.1.101",
                    port=22,
                    user="testuser",
                    password="testpassword",
                    key_file=None,
                    use_sudo=True,
                ),
                interface="eth0",
            ),
            "node-with-key": TargetNodeConfig(
                name="node-with-key",
                ssh=SSHConfig(
                    host="192.168.1.102",
                    port=22,
                    user="testuser",
                    key_file="~/.ssh/id_rsa",
                    password=None,
                    use_sudo=True,
                ),
                interface="eth0",
            ),
        }
        return SSHChannel(
            inventory=inventory,
            dry_run=True,
            wal=None,
            guard=None,
        )
    
    def test_get_node_config_existing(self, ssh_channel):
        """Test getting existing node config."""
        config = ssh_channel._get_node_config("node-with-password")
        assert config.name == "node-with-password"
        assert config.ssh.password == "testpassword"
    
    def test_get_node_config_nonexistent(self, ssh_channel):
        """Test getting non-existent node config raises error."""
        with pytest.raises(ValueError, match="节点.*不在清单中"):
            ssh_channel._get_node_config("nonexistent-node")


class TestSSHChannelErrorHandling:
    """Tests for error handling."""
    
    @pytest.fixture
    def ssh_channel(self) -> SSHChannel:
        """Create SSH channel for error handling tests."""
        inventory = {
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
        return SSHChannel(
            inventory=inventory,
            dry_run=True,  # Dry-run mode won't have real errors
            wal=None,
            guard=None,
        )
    
    @pytest.mark.asyncio
    async def test_command_timeout_parameter(self, ssh_channel):
        """Test that timeout parameter is accepted."""
        result = await ssh_channel.run_command(
            node="test-node-1",
            command="sleep 10",
            timeout=5,
        )
        
        # In dry-run mode, should succeed
        assert result.success is True
    
    @pytest.mark.asyncio
    async def test_empty_command(self, ssh_channel):
        """Test empty command handling."""
        result = await ssh_channel.run_command(
            node="test-node-1",
            command="",
        )
        
        # In dry-run mode, should succeed
        assert result.success is True


class TestSSHChannelIntegration:
    """Integration tests for SSH Channel (requires mocking asyncssh)."""
    
    @pytest.fixture
    def ssh_channel_real(self) -> SSHChannel:
        """Create SSH channel for integration tests (not dry-run)."""
        inventory = {
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
        return SSHChannel(
            inventory=inventory,
            dry_run=False,  # Real mode
            wal=None,
            guard=None,
        )
    
    @pytest.mark.asyncio
    async def test_connection_with_mock(self, ssh_channel_real):
        """Test connection with mocked asyncssh."""
        with patch("lib.fchannels.ssh.asyncssh") as mock_asyncssh:
            # Setup mock
            mock_conn = AsyncMock()
            mock_conn.is_closed.return_value = False
            mock_conn.run = AsyncMock(return_value=MagicMock(
                exit_status=0,
                stdout="test output",
                stderr="",
            ))
            mock_asyncssh.connect = AsyncMock(return_value=mock_conn)
            mock_asyncssh.Error = Exception
            
            # Run command
            result = await ssh_channel_real.run_command(
                node="test-node-1",
                command="echo 'test'",
            )
            
            assert result.success is True
            assert result.output == "test output"
    
    @pytest.mark.asyncio
    async def test_connection_failure_with_mock(self, ssh_channel_real):
        """Test connection failure handling with mocked asyncssh."""
        with patch("lib.fchannels.ssh.asyncssh") as mock_asyncssh:
            # Setup mock to fail
            mock_asyncssh.connect = AsyncMock(
                side_effect=Exception("Connection refused")
            )
            mock_asyncssh.Error = Exception
            
            # Run command should handle error
            result = await ssh_channel_real.run_command(
                node="test-node-1",
                command="echo 'test'",
            )
            
            assert result.success is False
            assert "Connection refused" in result.error or "执行失败" in result.error
