"""
SSHChannel — SSH 命令执行 Channel

使用 asyncssh 实现异步 SSH 连接和命令执行。
支持 sudo 模式执行需要 root 权限的命令。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncssh

from lib.channels.base import BaseChannel
from fault_injector.config.schema import (
    ChannelResult,
    SSHConfig,
    TargetNodeConfig,
)
from fault_injector.safety.guard import SafetyViolationError

logger = logging.getLogger(__name__)


class SSHChannel(BaseChannel):
    """
    SSH 命令执行 Channel。
    
    功能：
    - 异步 SSH 连接池管理
    - 命令执行超时控制
    - 自动注册恢复命令到 WAL
    - 安全命令检查
    - 支持 sudo 模式执行命令（默认启用）
    """
    
    def __init__(
        self,
        inventory: dict[str, TargetNodeConfig],
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        command_timeout: int = 60,
        connect_timeout: int = 10,
    ):
        """
        初始化 SSH Channel。
        
        Args:
            inventory: 节点清单 {node_name: TargetNodeConfig}
            dry_run: 干运行模式
            wal: 回滚日志
            guard: 安全守卫
            command_timeout: 命令执行超时（秒）
            connect_timeout: 连接超时（秒）
        """
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.inventory = inventory
        self.command_timeout = command_timeout
        self.connect_timeout = connect_timeout
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}
    
    def _get_node_config(self, node: str) -> TargetNodeConfig:
        """获取节点配置"""
        if node not in self.inventory:
            raise ValueError(f"节点 '{node}' 不在清单中")
        return self.inventory[node]
    
    def _should_use_sudo(self, node: str) -> bool:
        """检查节点是否需要使用 sudo（默认 True）"""
        node_config = self._get_node_config(node)
        # 如果配置中没有 use_sudo 字段，默认返回 True
        return getattr(node_config.ssh, 'use_sudo', True)
    
    async def _get_connection(
        self, node: str
    ) -> asyncssh.SSHClientConnection:
        """
        获取或创建到节点的 SSH 连接。
        
        Args:
            node: 节点名称
            
        Returns:
            asyncssh.SSHClientConnection
            
        Raises:
            ValueError: 节点不存在
            asyncssh.Error: 连接失败
        """
        # 检查现有连接
        if node in self._connections:
            conn = self._connections[node]
            if not conn.is_closed():
                return conn
        
        # 获取节点配置
        node_config = self._get_node_config(node)
        ssh_config = node_config.ssh
        
        # 构建连接参数
        connect_kwargs = {
            "host": ssh_config.host,
            "port": ssh_config.port,
            "username": ssh_config.user,
            "known_hosts": None,  # 禁用主机密钥检查（内网环境）
            "config": [],  # Avoid local ~/.ssh/config encoding/parse issues on Windows.
        }
        
        # 认证方式
        if ssh_config.key_file:
            connect_kwargs["client_keys"] = [ssh_config.key_file]
        elif ssh_config.password:
            connect_kwargs["password"] = ssh_config.password
        
        # 创建连接
        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(**connect_kwargs),
                timeout=self.connect_timeout,
            )
            self._connections[node] = conn
            logger.info(f"SSH 连接成功: {node} ({ssh_config.host})")
            return conn
        except asyncio.TimeoutError:
            raise asyncssh.Error(
                f"SSH 连接超时: {node} ({ssh_config.host})"
            )
    
    async def run_command(
        self,
        node: str,
        command: str,
        timeout: int | None = None,
        use_sudo: bool = True,  # 默认使用 sudo
    ) -> ChannelResult:
        """
        在目标节点执行命令。
        
        Args:
            node: 节点名称
            command: 要执行的命令
            timeout: 超时时间（秒），默认使用 command_timeout
            use_sudo: 是否使用 sudo，默认 True
            
        Returns:
            ChannelResult: 执行结果
        """
        timeout = timeout or self.command_timeout
        
        # 先验证节点是否存在（即使在 dry_run 模式下也要验证）
        self._get_node_config(node)
        
        # 如果需要 sudo，包装命令
        actual_command = f"sudo {command}" if use_sudo else command
        
        # dry_run 模式
        if self.dry_run:
            logger.info(f"[DRY-RUN] SSH {node}: {actual_command}")
            return ChannelResult(
                success=True,
                output="[DRY-RUN] 命令未实际执行",
                dry_run=True,
            )
        
        try:
            conn = await self._get_connection(node)
            
            # 执行命令
            result = await asyncio.wait_for(
                conn.run(actual_command, encoding="utf-8", errors="replace"),
                timeout=timeout,
            )
            
            return ChannelResult(
                success=result.exit_status == 0,
                output=result.stdout or "",
                error=result.stderr or "",
                dry_run=False,
            )
            
        except asyncio.TimeoutError:
            return ChannelResult(
                success=False,
                error=f"命令执行超时 ({timeout}s): {actual_command}",
                dry_run=False,
            )
        except asyncssh.Error as e:
            return ChannelResult(
                success=False,
                error=f"SSH 错误: {e}",
                dry_run=False,
            )
        except Exception as e:
            return ChannelResult(
                success=False,
                error=f"执行失败: {e}",
                dry_run=False,
            )
    
    async def _execute_impl(
        self, action: str, params: dict[str, Any]
    ) -> ChannelResult:
        """
        执行操作实现。
        
        Args:
            action: 操作类型
            params: 操作参数
            
        Returns:
            ChannelResult
        """
        if action == "run_command":
            return await self.run_command(
                node=params["node"],
                command=params["command"],
                timeout=params.get("timeout"),
                use_sudo=params.get("use_sudo", True),
            )
        elif action == "tc_add_delay":
            return await self._tc_add_delay(params)
        elif action == "tc_del_qdisc":
            return await self._tc_del_qdisc(params)
        else:
            return ChannelResult(
                success=False,
                error=f"未知操作: {action}",
                dry_run=False,
            )
    
    async def _tc_add_delay(self, params: dict[str, Any]) -> ChannelResult:
        """
        使用 tc netem 添加网络延迟。
        强制使用 sudo 执行。
        
        Args:
            params: {
                "node": 节点名,
                "interface": 接口名,
                "delay_ms": 延迟毫秒,
                "jitter_ms": 抖动毫秒,
                "distribution": 分布类型,
                "loss_pct": 丢包率
            }
        """
        node = params["node"]
        interface = params["interface"]
        delay_ms = params["delay_ms"]
        jitter_ms = params.get("jitter_ms", 0)
        distribution = params.get("distribution", "pareto")
        loss_pct = params.get("loss_pct", 0)
        
        # 构建命令
        cmd = f"tc qdisc add dev {interface} root netem delay {delay_ms}ms"
        if jitter_ms > 0:
            cmd += f" {jitter_ms}ms"
        if distribution != "normal":
            cmd += f" distribution {distribution}"
        if loss_pct > 0:
            cmd += f" loss {loss_pct}%"
        
        # 强制使用 sudo
        return await self.run_command(node, cmd, use_sudo=True)
    
    async def _tc_del_qdisc(self, params: dict[str, Any]) -> ChannelResult:
        """
        删除 tc qdisc（恢复网络）。
        强制使用 sudo 执行。
        
        Args:
            params: {"node": 节点名, "interface": 接口名}
        """
        node = params["node"]
        interface = params["interface"]
        cmd = f"tc qdisc del dev {interface} root"
        
        # 强制使用 sudo
        return await self.run_command(node, cmd, use_sudo=True)
    
    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        """
        SSH 安全检查。
        
        Args:
            action: 操作类型
            params: 操作参数
            
        Raises:
            SafetyViolationError
        """
        if self.guard:
            if action == "run_command":
                self.guard.check_command(params.get("command", ""), "ssh")
    
    async def close(self) -> None:
        """关闭所有连接"""
        for node, conn in self._connections.items():
            try:
                conn.close()
                await conn.wait_closed()
                logger.debug(f"SSH 连接关闭: {node}")
            except Exception as e:
                logger.warning(f"关闭 SSH 连接失败 ({node}): {e}")
        self._connections.clear()
    
    async def test_connection(self, node: str) -> bool:
        """
        测试到节点的连接。
        
        Args:
            node: 节点名称
            
        Returns:
            bool: 连接是否成功
        """
        try:
            # 测试连接时使用简单的 echo 命令，不需要 sudo
            result = await self.run_command(node, "echo 'OK'", use_sudo=False)
            # dry_run 模式下返回 True
            if result.dry_run:
                return True
            return result.success and "OK" in result.output
        except Exception:
            return False
