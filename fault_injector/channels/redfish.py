"""
Redfish channel for BMC operations.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from fault_injector.channels.base import BaseChannel
from fault_injector.config.schema import ChannelResult
from fault_injector.safety.guard import SafetyViolationError

logger = logging.getLogger(__name__)


class RedfishChannel(BaseChannel):
    """Redfish REST API channel."""

    FORBIDDEN_PATH_KEYWORDS = (
        "restorefactory",
        "factoryreset",
        "ethernetinterfaces",
    )

    def __init__(
        self,
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        timeout: int = 30,
    ):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.timeout = timeout
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._tokens: dict[str, str] = {}

    async def _get_client(self, bmc_host: str, verify_tls: bool = True) -> httpx.AsyncClient:
        key = f"{bmc_host}|{verify_tls}"
        client = self._clients.get(key)
        if client is None:
            client = httpx.AsyncClient(
                base_url=f"https://{bmc_host}",
                timeout=self.timeout,
                verify=verify_tls,
            )
            self._clients[key] = client
        return client

    async def authenticate(
        self,
        bmc_host: str,
        username: str,
        password: str,
        verify_tls: bool = True,
    ) -> ChannelResult:
        return await self.execute(
            "authenticate",
            {
                "bmc_host": bmc_host,
                "username": username,
                "password": password,
                "verify_tls": verify_tls,
            },
        )

    async def get_thermal(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.execute(
            "get_thermal",
            {"bmc_host": bmc_host, "verify_tls": verify_tls},
        )

    async def set_fan_control(
        self,
        bmc_host: str,
        fan_index: int,
        mode: str,
        pwm: int | None = None,
        verify_tls: bool = True,
        fault_id: str | None = None,
    ) -> ChannelResult:
        recover_params = {"bmc_host": bmc_host, "fan_index": fan_index, "mode": "Auto", "verify_tls": verify_tls}
        return await self.execute(
            "set_fan_control",
            {
                "bmc_host": bmc_host,
                "fan_index": fan_index,
                "mode": mode,
                "pwm": pwm,
                "verify_tls": verify_tls,
            },
            recovery_action="set_fan_control",
            recovery_params=recover_params,
            fault_id=fault_id,
            target=bmc_host,
        )

    async def reset_system(
        self,
        bmc_host: str,
        reset_type: str = "GracefulRestart",
        verify_tls: bool = True,
        fault_id: str | None = None,
    ) -> ChannelResult:
        return await self.execute(
            "reset_system",
            {"bmc_host": bmc_host, "reset_type": reset_type, "verify_tls": verify_tls},
            fault_id=fault_id,
            target=bmc_host,
        )

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "authenticate":
            return await self._authenticate_impl(params)
        if action == "get_thermal":
            return await self._get_thermal_impl(params)
        if action == "set_fan_control":
            return await self._set_fan_control_impl(params)
        if action == "reset_system":
            return await self._reset_system_impl(params)
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        path = str(params.get("path", "")).lower()
        for keyword in self.FORBIDDEN_PATH_KEYWORDS:
            if keyword in path:
                raise SafetyViolationError(f"Forbidden Redfish endpoint: {path}")
        if action == "reset_system":
            reset_type = str(params.get("reset_type", "")).lower()
            if reset_type in {"forcerestart", "forceoff"}:
                raise SafetyViolationError(f"Forbidden reset type: {reset_type}")

    async def _authenticate_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        try:
            resp = await client.post(
                "/redfish/v1/SessionService/Sessions",
                json={"UserName": params["username"], "Password": params["password"]},
            )
            resp.raise_for_status()
            token = resp.headers.get("X-Auth-Token", "")
            if not token:
                return ChannelResult(success=False, error="Redfish auth token missing")
            self._tokens[bmc_host] = token
            return ChannelResult(success=True, output=token)
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish auth failed: {exc}")

    async def _authorized_headers(self, bmc_host: str) -> dict[str, str]:
        token = self._tokens.get(bmc_host)
        return {"X-Auth-Token": token} if token else {}

    async def _get_thermal_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        try:
            resp = await client.get(
                "/redfish/v1/Chassis/Self/Thermal",
                headers=await self._authorized_headers(bmc_host),
            )
            resp.raise_for_status()
            return ChannelResult(success=True, output=resp.text)
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish thermal query failed: {exc}")

    async def _set_fan_control_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        mode = params["mode"]
        payload: dict[str, Any] = {"FanControlMode": mode}
        if params.get("pwm") is not None:
            payload["FanPWM"] = int(params["pwm"])
        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        try:
            resp = await client.patch(
                "/redfish/v1/Chassis/Self/Thermal/ThermalManagement",
                headers=await self._authorized_headers(bmc_host),
                json=payload,
            )
            resp.raise_for_status()
            return ChannelResult(success=True, output=resp.text or "ok")
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish set fan failed: {exc}")

    async def _reset_system_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        try:
            resp = await client.post(
                "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
                headers=await self._authorized_headers(bmc_host),
                json={"ResetType": params.get("reset_type", "GracefulRestart")},
            )
            resp.raise_for_status()
            return ChannelResult(success=True, output=resp.text or "ok")
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish reset failed: {exc}")

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._tokens.clear()
