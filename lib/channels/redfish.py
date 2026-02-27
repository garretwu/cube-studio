"""
Scalable Redfish channel for BMC operations and discovery.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from lib.channels.base import BaseChannel
from fault_injector.config.schema import ChannelResult
from fault_injector.safety.guard import SafetyViolationError

logger = logging.getLogger(__name__)


class RedfishChannel(BaseChannel):
    """Redfish REST API channel."""

    SERVICE_ROOT_PATH = "/redfish/v1/"
    SESSION_PATH = "/redfish/v1/SessionService/Sessions"

    FORBIDDEN_PATH_KEYWORDS = (
        "restorefactory",
        "factoryreset",
        "ethernetinterfaces",
    )
    FORBIDDEN_RESET_TYPES = {"forceoff", "forcerestart"}

    def __init__(
        self,
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        timeout: int = 30,
        max_connections: int = 20,
    ):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.timeout = timeout
        self.max_connections = max_connections
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._tokens: dict[str, str] = {}
        self._session_locations: dict[str, str] = {}

    async def _get_client(self, bmc_host: str, verify_tls: bool = True) -> httpx.AsyncClient:
        key = f"{bmc_host}|{verify_tls}"
        client = self._clients.get(key)
        if client is None:
            limits = httpx.Limits(max_connections=self.max_connections, max_keepalive_connections=10)
            client = httpx.AsyncClient(
                base_url=f"https://{bmc_host}",
                timeout=self.timeout,
                verify=verify_tls,
                limits=limits,
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

    async def logout(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.execute(
            "logout",
            {
                "bmc_host": bmc_host,
                "verify_tls": verify_tls,
            },
        )

    def set_token(self, bmc_host: str, token: str) -> None:
        """Set a pre-existing Redfish session token."""
        self._tokens[bmc_host] = token

    async def get_service_root(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.execute(
            "get_service_root",
            {"bmc_host": bmc_host, "verify_tls": verify_tls},
        )

    async def get_bmc_info(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.execute(
            "get_bmc_info",
            {"bmc_host": bmc_host, "verify_tls": verify_tls},
        )

    async def discover_capabilities(
        self,
        bmc_host: str,
        verify_tls: bool = True,
        include_members: bool = True,
        member_limit: int = 10,
    ) -> ChannelResult:
        return await self.execute(
            "discover_capabilities",
            {
                "bmc_host": bmc_host,
                "verify_tls": verify_tls,
                "include_members": include_members,
                "member_limit": member_limit,
            },
        )

    async def request(
        self,
        bmc_host: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        verify_tls: bool = True,
        requires_auth: bool = True,
    ) -> ChannelResult:
        return await self.execute(
            "request",
            {
                "bmc_host": bmc_host,
                "method": method.upper(),
                "path": path,
                "payload": payload or {},
                "query": query or {},
                "verify_tls": verify_tls,
                "requires_auth": requires_auth,
            },
        )

    async def get_thermal(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.request(
            bmc_host=bmc_host,
            method="GET",
            path="/redfish/v1/Chassis/Self/Thermal",
            verify_tls=verify_tls,
            requires_auth=True,
        )

    async def get_power(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.request(
            bmc_host=bmc_host,
            method="GET",
            path="/redfish/v1/Chassis/Self/Power",
            verify_tls=verify_tls,
            requires_auth=True,
        )

    async def get_sensors(self, bmc_host: str, verify_tls: bool = True) -> ChannelResult:
        return await self.request(
            bmc_host=bmc_host,
            method="GET",
            path="/redfish/v1/Chassis/Self/Sensors",
            verify_tls=verify_tls,
            requires_auth=True,
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
        recover_params = {
            "bmc_host": bmc_host,
            "fan_index": fan_index,
            "mode": "Auto",
            "verify_tls": verify_tls,
        }
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

    async def set_fan_speed(
        self,
        bmc_host: str,
        fan_pwm: int,
        mode: str = "Manual",
        verify_tls: bool = True,
        fault_id: str | None = None,
    ) -> ChannelResult:
        """
        Backward-compatible helper used by scenario code.
        """
        return await self.set_fan_control(
            bmc_host=bmc_host,
            fan_index=0,
            mode=mode,
            pwm=fan_pwm,
            verify_tls=verify_tls,
            fault_id=fault_id,
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
        handlers = {
            "authenticate": self._authenticate_impl,
            "logout": self._logout_impl,
            "get_service_root": self._get_service_root_impl,
            "get_bmc_info": self._get_bmc_info_impl,
            "discover_capabilities": self._discover_capabilities_impl,
            "request": self._request_impl,
            "set_fan_control": self._set_fan_control_impl,
            "reset_system": self._reset_system_impl,
        }
        handler = handlers.get(action)
        if handler is None:
            return ChannelResult(success=False, error=f"Unknown action: {action}")
        return await handler(params)

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        path = str(params.get("path", "")).lower()
        for keyword in self.FORBIDDEN_PATH_KEYWORDS:
            if keyword in path:
                raise SafetyViolationError(f"Forbidden Redfish endpoint: {path}")

        if action == "reset_system":
            reset_type = str(params.get("reset_type", "")).lower()
            if reset_type in self.FORBIDDEN_RESET_TYPES:
                raise SafetyViolationError(f"Forbidden reset type: {reset_type}")

    async def _authenticate_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        try:
            resp = await client.post(
                self.SESSION_PATH,
                json={"UserName": params["username"], "Password": params["password"]},
            )
            resp.raise_for_status()
            token = resp.headers.get("X-Auth-Token", "")
            location = resp.headers.get("Location", "")
            if not token:
                return ChannelResult(success=False, error="Redfish auth token missing")
            self._tokens[bmc_host] = token
            if location:
                self._session_locations[bmc_host] = location
            return ChannelResult(success=True, output=token)
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish auth failed: {exc}")

    async def _logout_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        location = self._session_locations.get(bmc_host)
        if not location:
            self._tokens.pop(bmc_host, None)
            return ChannelResult(success=True, output="No active Redfish session location")

        client = await self._get_client(bmc_host, params.get("verify_tls", True))
        headers = self._authorized_headers(bmc_host)
        try:
            resp = await client.delete(location, headers=headers)
            if resp.status_code not in (200, 202, 204):
                resp.raise_for_status()
            self._tokens.pop(bmc_host, None)
            self._session_locations.pop(bmc_host, None)
            return ChannelResult(success=True, output="Redfish session closed")
        except httpx.HTTPError as exc:
            return ChannelResult(success=False, error=f"Redfish logout failed: {exc}")

    def _authorized_headers(self, bmc_host: str) -> dict[str, str]:
        token = self._tokens.get(bmc_host)
        return {"X-Auth-Token": token} if token else {}

    async def _fetch_json(
        self,
        *,
        bmc_host: str,
        method: str,
        path: str,
        verify_tls: bool,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        requires_auth: bool = True,
    ) -> tuple[bool, dict[str, Any], str]:
        if requires_auth and bmc_host not in self._tokens:
            return False, {}, "Missing Redfish auth token. Call authenticate() first."

        client = await self._get_client(bmc_host, verify_tls)
        headers = self._authorized_headers(bmc_host) if requires_auth else {}
        try:
            resp = await client.request(
                method=method.upper(),
                url=path,
                headers=headers,
                json=payload if payload else None,
                params=query if query else None,
            )
            resp.raise_for_status()
            if not resp.text:
                return True, {}, ""
            try:
                return True, resp.json(), ""
            except ValueError:
                return False, {}, f"Expected JSON response from {path}"
        except httpx.HTTPError as exc:
            return False, {}, str(exc)

    @staticmethod
    def _extract_actions(payload: dict[str, Any]) -> list[str]:
        actions = payload.get("Actions", {})
        if not isinstance(actions, dict):
            return []
        return sorted([k for k in actions.keys() if isinstance(k, str)])

    @staticmethod
    def _extract_allowable_reset_types(payload: dict[str, Any]) -> list[str]:
        actions = payload.get("Actions", {})
        if not isinstance(actions, dict):
            return []
        reset = actions.get("#ComputerSystem.Reset")
        if not isinstance(reset, dict):
            return []
        vals = reset.get("ResetType@Redfish.AllowableValues", [])
        if not isinstance(vals, list):
            return []
        return [str(v) for v in vals]

    @staticmethod
    def _dumps(data: dict[str, Any]) -> str:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)

    async def _get_service_root_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, data, error = await self._fetch_json(
            bmc_host=params["bmc_host"],
            method="GET",
            path=self.SERVICE_ROOT_PATH,
            verify_tls=params.get("verify_tls", True),
            requires_auth=True,
        )
        if not ok:
            return ChannelResult(success=False, error=f"Redfish service root query failed: {error}")
        return ChannelResult(success=True, output=self._dumps(data))

    async def _get_bmc_info_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        verify_tls = params.get("verify_tls", True)

        ok, root, error = await self._fetch_json(
            bmc_host=bmc_host,
            method="GET",
            path=self.SERVICE_ROOT_PATH,
            verify_tls=verify_tls,
            requires_auth=True,
        )
        if not ok:
            return ChannelResult(success=False, error=f"Failed to query service root: {error}")

        info: dict[str, Any] = {
            "service_root": {
                "redfish_version": root.get("RedfishVersion"),
                "uuid": root.get("UUID"),
                "vendor": root.get("Vendor"),
                "product": root.get("Product"),
                "protocol_features": root.get("ProtocolFeaturesSupported", {}),
            }
        }

        async def load_first_member(resource_key: str, out_key: str) -> None:
            resource = root.get(resource_key, {})
            if not isinstance(resource, dict):
                return
            collection_path = resource.get("@odata.id")
            if not collection_path:
                return

            ok_collection, collection, _ = await self._fetch_json(
                bmc_host=bmc_host,
                method="GET",
                path=collection_path,
                verify_tls=verify_tls,
                requires_auth=True,
            )
            if not ok_collection:
                return

            members = collection.get("Members", [])
            if not isinstance(members, list) or not members:
                return
            first = members[0]
            if not isinstance(first, dict):
                return
            member_path = first.get("@odata.id")
            if not member_path:
                return

            ok_member, member, _ = await self._fetch_json(
                bmc_host=bmc_host,
                method="GET",
                path=member_path,
                verify_tls=verify_tls,
                requires_auth=True,
            )
            if not ok_member:
                return

            info[out_key] = {
                "path": member_path,
                "id": member.get("Id"),
                "name": member.get("Name"),
                "model": member.get("Model"),
                "manufacturer": member.get("Manufacturer"),
                "firmware_version": member.get("FirmwareVersion"),
                "power_state": member.get("PowerState"),
                "status": member.get("Status", {}),
                "actions": self._extract_actions(member),
            }
            if out_key == "system":
                info[out_key]["allowable_reset_types"] = self._extract_allowable_reset_types(member)

        await load_first_member("Managers", "manager")
        await load_first_member("Systems", "system")
        await load_first_member("Chassis", "chassis")
        return ChannelResult(success=True, output=self._dumps(info))

    async def _discover_capabilities_impl(self, params: dict[str, Any]) -> ChannelResult:
        bmc_host = params["bmc_host"]
        verify_tls = params.get("verify_tls", True)
        include_members = bool(params.get("include_members", True))
        member_limit = int(params.get("member_limit", 10))

        ok, root, error = await self._fetch_json(
            bmc_host=bmc_host,
            method="GET",
            path=self.SERVICE_ROOT_PATH,
            verify_tls=verify_tls,
            requires_auth=True,
        )
        if not ok:
            return ChannelResult(success=False, error=f"Failed to query service root: {error}")

        capabilities: dict[str, Any] = {
            "redfish_version": root.get("RedfishVersion"),
            "vendor": root.get("Vendor"),
            "product": root.get("Product"),
            "services": {},
        }

        for key, value in root.items():
            if not isinstance(value, dict):
                continue
            service_path = value.get("@odata.id")
            if not service_path:
                continue

            ok_service, service_doc, _ = await self._fetch_json(
                bmc_host=bmc_host,
                method="GET",
                path=service_path,
                verify_tls=verify_tls,
                requires_auth=True,
            )
            if not ok_service:
                continue

            entry: dict[str, Any] = {
                "path": service_path,
                "actions": self._extract_actions(service_doc),
            }

            members = service_doc.get("Members", [])
            if isinstance(members, list):
                entry["members_count"] = len(members)
                if include_members:
                    member_entries: list[dict[str, Any]] = []
                    for member in members[:member_limit]:
                        if not isinstance(member, dict):
                            continue
                        member_path = member.get("@odata.id")
                        if not member_path:
                            continue
                        ok_member, member_doc, _ = await self._fetch_json(
                            bmc_host=bmc_host,
                            method="GET",
                            path=member_path,
                            verify_tls=verify_tls,
                            requires_auth=True,
                        )
                        if not ok_member:
                            continue
                        member_entry = {
                            "path": member_path,
                            "type": member_doc.get("@odata.type"),
                            "name": member_doc.get("Name"),
                            "actions": self._extract_actions(member_doc),
                        }
                        allowable = self._extract_allowable_reset_types(member_doc)
                        if allowable:
                            member_entry["allowable_reset_types"] = allowable
                        member_entries.append(member_entry)
                    entry["members"] = member_entries

            capabilities["services"][key] = entry

        return ChannelResult(success=True, output=self._dumps(capabilities))

    async def _request_impl(self, params: dict[str, Any]) -> ChannelResult:
        ok, data, error = await self._fetch_json(
            bmc_host=params["bmc_host"],
            method=params["method"],
            path=params["path"],
            verify_tls=params.get("verify_tls", True),
            payload=params.get("payload"),
            query=params.get("query"),
            requires_auth=params.get("requires_auth", True),
        )
        if not ok:
            return ChannelResult(success=False, error=f"Redfish request failed: {error}")
        return ChannelResult(success=True, output=self._dumps(data))

    async def _set_fan_control_impl(self, params: dict[str, Any]) -> ChannelResult:
        payload: dict[str, Any] = {"FanControlMode": params["mode"], "FanIndex": params["fan_index"]}
        if params.get("pwm") is not None:
            payload["FanPWM"] = int(params["pwm"])

        return await self._request_impl(
            {
                "bmc_host": params["bmc_host"],
                "method": "PATCH",
                "path": "/redfish/v1/Chassis/Self/Thermal/ThermalManagement",
                "payload": payload,
                "verify_tls": params.get("verify_tls", True),
                "requires_auth": True,
            }
        )

    async def _reset_system_impl(self, params: dict[str, Any]) -> ChannelResult:
        return await self._request_impl(
            {
                "bmc_host": params["bmc_host"],
                "method": "POST",
                "path": "/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset",
                "payload": {"ResetType": params.get("reset_type", "GracefulRestart")},
                "verify_tls": params.get("verify_tls", True),
                "requires_auth": True,
            }
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._tokens.clear()
        self._session_locations.clear()
