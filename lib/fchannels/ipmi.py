"""
IPMI channel implemented with python library (pyghmi).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from lib.fchannels.base import BaseChannel
from fault_injector.config.schema import ChannelResult

try:
    from pyghmi.ipmi import command as pyghmi_command
except Exception:  # pragma: no cover - optional dependency in runtime env
    pyghmi_command = None


class IPMIChannel(BaseChannel):
    """IPMI operations over LAN/LANPlus using pyghmi."""

    def __init__(self, dry_run: bool = False, wal: Any = None, guard: Any = None, timeout: int = 30):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.timeout = timeout
        self._clients: dict[str, Any] = {}

    def _client_key(self, params: dict[str, Any]) -> str:
        return (
            f"{params['host']}|{params.get('port', 623)}|{params.get('interface', 'lanplus')}"
            f"|{params.get('username', '')}|{params.get('password', '')}"
        )

    async def _get_client(self, params: dict[str, Any]) -> tuple[bool, Any, str]:
        if pyghmi_command is None:
            return False, None, "pyghmi is not installed. Install with: pip install pyghmi"

        key = self._client_key(params)
        existing = self._clients.get(key)
        if existing is not None:
            return True, existing, ""

        host = params["host"]
        username = params.get("username")
        password = params.get("password")
        if not username or not password:
            return False, None, "Missing IPMI credentials (username/password)"

        port = int(params.get("port", 623))
        timeout = int(params.get("timeout", self.timeout))

        try:
            client = await asyncio.to_thread(
                pyghmi_command.Command,
                bmc=host,
                userid=username,
                password=password,
                port=port,
            )
            self._clients[key] = client
            return True, client, ""
        except Exception as exc:
            return False, None, str(exc)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, (bytes, bytearray)):
            return list(value)
        if isinstance(value, dict):
            return {str(k): IPMIChannel._json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [IPMIChannel._json_safe(v) for v in value]
        if isinstance(value, tuple):
            return [IPMIChannel._json_safe(v) for v in value]
        return value

    @staticmethod
    def _dumps(data: Any) -> str:
        return json.dumps(IPMIChannel._json_safe(data), ensure_ascii=False)

    async def connect(
        self,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 623,
        interface: str = "lanplus",
        timeout: int = 30,
    ) -> ChannelResult:
        return await self.execute(
            "connect",
            {
                "host": host,
                "username": username,
                "password": password,
                "port": port,
                "interface": interface,
                "timeout": timeout,
            },
        )

    async def get_mc_info(
        self,
        *,
        host: str,
        username: str,
        password: str,
        port: int = 623,
        interface: str = "lanplus",
        timeout: int = 30,
    ) -> ChannelResult:
        return await self.execute(
            "get_mc_info",
            {
                "host": host,
                "username": username,
                "password": password,
                "port": port,
                "interface": interface,
                "timeout": timeout,
            },
        )

    async def get_sensor_data(
        self,
        *,
        host: str,
        username: str,
        password: str,
        sensor_type: str | None = None,
        port: int = 623,
        interface: str = "lanplus",
        timeout: int = 30,
    ) -> ChannelResult:
        return await self.execute(
            "get_sensor_data",
            {
                "host": host,
                "username": username,
                "password": password,
                "sensor_type": sensor_type,
                "port": port,
                "interface": interface,
                "timeout": timeout,
            },
        )

    async def raw_command(
        self,
        *,
        host: str,
        username: str,
        password: str,
        netfn: int,
        command: int,
        data: list[int] | None = None,
        port: int = 623,
        interface: str = "lanplus",
        timeout: int = 30,
    ) -> ChannelResult:
        return await self.execute(
            "raw_command",
            {
                "host": host,
                "username": username,
                "password": password,
                "netfn": int(netfn),
                "command": int(command),
                "data": list(data or []),
                "port": port,
                "interface": interface,
                "timeout": timeout,
            },
        )

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "connect":
            return await self._connect_impl(params)
        if action == "get_mc_info":
            return await self._get_mc_info_impl(params)
        if action == "get_sensor_data":
            return await self._get_sensor_data_impl(params)
        if action == "raw_command":
            return await self._raw_command_impl(params)
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    async def _connect_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, client, error = await self._get_client(params)
        if not ok:
            return ChannelResult(success=False, error=f"IPMI connect failed: {error}")
        try:
            result = await asyncio.to_thread(client.raw_command, netfn=0x06, command=0x01, data=[])
            return ChannelResult(success=True, output=self._dumps({"device_id": result}))
        except Exception as exc:
            return ChannelResult(success=False, error=f"IPMI probe failed: {exc}")

    async def _get_mc_info_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, client, error = await self._get_client(params)
        if not ok:
            return ChannelResult(success=False, error=f"IPMI get_mc_info failed: {error}")
        try:
            payload = await asyncio.to_thread(client.raw_command, netfn=0x06, command=0x01, data=[])
            data = payload.get("data", []) if isinstance(payload, dict) else []
            bytes_data = [int(x) for x in data] if isinstance(data, list) else []
            mc_info = {
                "raw": payload,
                "device_id": bytes_data[0] if len(bytes_data) > 0 else None,
                "device_revision": bytes_data[1] if len(bytes_data) > 1 else None,
                "firmware_major": bytes_data[2] if len(bytes_data) > 2 else None,
                "firmware_minor": bytes_data[3] if len(bytes_data) > 3 else None,
                "ipmi_version": bytes_data[4] if len(bytes_data) > 4 else None,
            }
            return ChannelResult(success=True, output=self._dumps(mc_info))
        except Exception as exc:
            return ChannelResult(success=False, error=f"IPMI get_mc_info failed: {exc}")

    async def _get_sensor_data_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, client, error = await self._get_client(params)
        if not ok:
            return ChannelResult(success=False, error=f"IPMI get_sensor_data failed: {error}")
        sensor_type = str(params.get("sensor_type") or "").strip().lower()
        try:
            raw_sensors = await asyncio.to_thread(lambda: list(client.get_sensor_data()))
            sensors: list[dict[str, Any]] = []
            for item in raw_sensors:
                if not isinstance(item, dict):
                    continue
                sensor_name = str(item.get("name") or "")
                item_type = str(item.get("type") or "")
                if sensor_type:
                    if sensor_type not in sensor_name.lower() and sensor_type not in item_type.lower():
                        continue
                sensors.append(
                    {
                        "name": sensor_name,
                        "type": item_type,
                        "value": item.get("value"),
                        "units": item.get("units"),
                        "states": item.get("states"),
                        "health": item.get("health"),
                        "unavailable": item.get("unavailable"),
                    }
                )
            return ChannelResult(success=True, output=self._dumps({"count": len(sensors), "sensors": sensors}))
        except Exception as exc:
            return ChannelResult(success=False, error=f"IPMI get_sensor_data failed: {exc}")

    async def _raw_command_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, client, error = await self._get_client(params)
        if not ok:
            return ChannelResult(success=False, error=f"IPMI raw_command failed: {error}")
        try:
            result = await asyncio.to_thread(
                client.raw_command,
                netfn=int(params["netfn"]),
                command=int(params["command"]),
                data=[int(v) for v in params.get("data", [])],
            )
            return ChannelResult(success=True, output=self._dumps(result))
        except Exception as exc:
            return ChannelResult(success=False, error=f"IPMI raw_command failed: {exc}")

    async def close(self) -> None:
        self._clients.clear()
