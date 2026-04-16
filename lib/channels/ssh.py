"""
SSH command execution channel based on asyncssh.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncssh

from fault_injector.config.schema import SSHConfig, TargetNodeConfig
from lib.channels.base import BaseChannel, ChannelResult, SafetyViolationError

logger = logging.getLogger(__name__)


class SSHChannel(BaseChannel):
    """Execute remote commands through SSH with optional sudo."""

    def __init__(
        self,
        inventory: dict[str, TargetNodeConfig],
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        command_timeout: int = 60,
        connect_timeout: int = 10,
    ):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.inventory = inventory
        self.command_timeout = command_timeout
        self.connect_timeout = connect_timeout
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}
        self._ip_to_node: dict[str, str] = {}
        for node_name, node_config in inventory.items():
            ssh_host = getattr(node_config.ssh, "host", None) if hasattr(node_config, "ssh") else None
            if ssh_host:
                self._ip_to_node[str(ssh_host)] = node_name
            # Also add k8s_node_name mapping for resolving K8s node names like "wj-lab-cpt-04"
            k8s_node_name = getattr(node_config, "k8s_node_name", None)
            if k8s_node_name:
                self._ip_to_node[str(k8s_node_name)] = node_name

    def _resolve_node_name(self, node: str) -> str:
        from sre_agent.runtime.node_mapping import normalize_node_identifier

        normalized_node, _ = normalize_node_identifier(
            node,
            inventory_names=set(self.inventory.keys()),
            host_to_name=self._ip_to_node,
        )
        if normalized_node:
            if normalized_node != node:
                logger.debug("Resolved node %r to inventory node %r", node, normalized_node)
            return normalized_node
        return node

    def _get_node_config(self, node: str) -> TargetNodeConfig:
        resolved_node = self._resolve_node_name(node)
        if resolved_node not in self.inventory:
            raise ValueError(f"节点 '{node}' 不在清单中")
        return self.inventory[resolved_node]

    def _should_use_sudo(self, node: str) -> bool:
        node_config = self._get_node_config(node)
        return getattr(node_config.ssh, "use_sudo", True)

    async def _get_connection(self, node: str) -> asyncssh.SSHClientConnection:
        resolved_node = self._resolve_node_name(node)
        if resolved_node in self._connections:
            conn = self._connections[resolved_node]
            if not conn.is_closed():
                return conn

        node_config = self._get_node_config(resolved_node)
        ssh_config: SSHConfig = node_config.ssh
        connect_kwargs: dict[str, Any] = {
            "host": ssh_config.host,
            "port": ssh_config.port,
            "username": ssh_config.user,
            "known_hosts": None,
            "config": [],
        }
        if ssh_config.key_file:
            connect_kwargs["client_keys"] = [ssh_config.key_file]
        elif ssh_config.password:
            connect_kwargs["password"] = ssh_config.password

        try:
            conn = await asyncio.wait_for(asyncssh.connect(**connect_kwargs), timeout=self.connect_timeout)
            self._connections[resolved_node] = conn
            logger.info("SSH connected: %s (%s)", resolved_node, ssh_config.host)
            return conn
        except asyncio.TimeoutError as exc:
            raise asyncssh.Error(f"SSH connection timed out: {resolved_node} ({ssh_config.host})") from exc

    async def run_command(
        self,
        node: str,
        command: str,
        timeout: int | None = None,
        use_sudo: bool = True,
    ) -> ChannelResult:
        timeout = timeout or self.command_timeout
        self._get_node_config(node)
        actual_command = f"sudo {command}" if use_sudo else command

        if self.dry_run:
            logger.info("[DRY-RUN] SSH %s: %s", node, actual_command)
            return ChannelResult(success=True, output="[DRY-RUN] command not executed", dry_run=True)

        try:
            conn = await self._get_connection(node)
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
            return ChannelResult(success=False, error=f"command timed out ({timeout}s): {actual_command}", dry_run=False)
        except asyncssh.Error as exc:
            return ChannelResult(success=False, error=f"SSH error: {exc}", dry_run=False)
        except Exception as exc:  # noqa: BLE001
            return ChannelResult(success=False, error=f"execution failed: {exc}", dry_run=False)

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "run_command":
            return await self.run_command(
                node=params["node"],
                command=params["command"],
                timeout=params.get("timeout"),
                use_sudo=params.get("use_sudo", True),
            )
        if action == "tc_add_delay":
            return await self._tc_add_delay(params)
        if action == "tc_del_qdisc":
            return await self._tc_del_qdisc(params)
        return ChannelResult(success=False, error=f"unknown action: {action}", dry_run=False)

    async def _tc_add_delay(self, params: dict[str, Any]) -> ChannelResult:
        node = params["node"]
        interface = params["interface"]
        delay_ms = params["delay_ms"]
        jitter_ms = params.get("jitter_ms", 0)
        distribution = params.get("distribution", "pareto")
        loss_pct = params.get("loss_pct", 0)

        cmd = f"tc qdisc add dev {interface} root netem delay {delay_ms}ms"
        if jitter_ms > 0:
            cmd += f" {jitter_ms}ms"
        if distribution != "normal":
            cmd += f" distribution {distribution}"
        if loss_pct > 0:
            cmd += f" loss {loss_pct}%"
        return await self.run_command(node, cmd, use_sudo=True)

    async def _tc_del_qdisc(self, params: dict[str, Any]) -> ChannelResult:
        node = params["node"]
        interface = params["interface"]
        cmd = f"tc qdisc del dev {interface} root"
        return await self.run_command(node, cmd, use_sudo=True)

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        if self.guard and action == "run_command":
            self.guard.check_command(params.get("command", ""), "ssh")

    async def close(self) -> None:
        for node, conn in self._connections.items():
            try:
                conn.close()
                await conn.wait_closed()
                logger.debug("SSH connection closed: %s", node)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to close SSH connection (%s): %s", node, exc)
        self._connections.clear()

    async def test_connection(self, node: str) -> bool:
        try:
            result = await self.run_command(node, "echo 'OK'", use_sudo=False)
            if result.dry_run:
                return True
            return result.success and "OK" in result.output
        except Exception:  # noqa: BLE001
            return False
