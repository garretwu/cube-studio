"""
Switch channel (H3C style CLI over SSH).
"""
from __future__ import annotations

import asyncio
from typing import Any

import asyncssh

from fault_injector.channels.base import BaseChannel
from fault_injector.config.schema import ChannelResult


class SwitchChannel(BaseChannel):
    """Network switch command channel."""

    def __init__(
        self,
        devices: dict[str, dict[str, Any]],
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        command_timeout: int = 30,
        connect_timeout: int = 10,
    ):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.devices = devices
        self.command_timeout = command_timeout
        self.connect_timeout = connect_timeout
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}

    def _device(self, name: str) -> dict[str, Any]:
        if name not in self.devices:
            raise ValueError(f"Unknown switch: {name}")
        return self.devices[name]

    async def _conn(self, name: str) -> asyncssh.SSHClientConnection:
        if name in self._connections and not self._connections[name].is_closed():
            return self._connections[name]
        dev = self._device(name)
        kwargs: dict[str, Any] = {
            "host": dev["host"],
            "port": int(dev.get("port", 22)),
            "username": dev["user"],
            "known_hosts": None,
        }
        if dev.get("key_file"):
            kwargs["client_keys"] = [dev["key_file"]]
        elif dev.get("password"):
            kwargs["password"] = dev["password"]
        conn = await asyncio.wait_for(asyncssh.connect(**kwargs), timeout=self.connect_timeout)
        self._connections[name] = conn
        return conn

    async def ssh_cli_execute(
        self,
        switch: str,
        commands: list[str],
        undo_commands: list[str] | None = None,
        fault_id: str | None = None,
    ) -> ChannelResult:
        return await self.execute(
            "ssh_cli_execute",
            {"switch": switch, "commands": commands},
            recovery_action="ssh_cli_execute" if undo_commands else None,
            recovery_params={"switch": switch, "commands": undo_commands or []} if undo_commands else None,
            fault_id=fault_id,
            target=switch,
        )

    async def shutdown_port(
        self, switch: str, interface: str, fault_id: str | None = None
    ) -> ChannelResult:
        return await self.ssh_cli_execute(
            switch=switch,
            commands=["system-view", f"interface {interface}", "shutdown", "quit"],
            undo_commands=["system-view", f"interface {interface}", "undo shutdown", "quit"],
            fault_id=fault_id,
        )

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action != "ssh_cli_execute":
            return ChannelResult(success=False, error=f"Unknown action: {action}")
        switch = params["switch"]
        commands = params.get("commands", [])
        command_text = "\n".join(commands) + "\n"
        try:
            conn = await self._conn(switch)
            result = await asyncio.wait_for(
                conn.run(command_text),
                timeout=self.command_timeout,
            )
            return ChannelResult(
                success=result.exit_status == 0,
                output=result.stdout or "",
                error=result.stderr or "",
            )
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    async def close(self) -> None:
        for conn in self._connections.values():
            conn.close()
            await conn.wait_closed()
        self._connections.clear()
