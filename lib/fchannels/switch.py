"""Switch channel implemented with H3C NETCONF primitives.

This module follows fault_injector/docs/H3C_NETCONF_GUIDE.md:
- Separate data/config namespaces
- Resolve interface names to IfIndex first
- Read with get()/get_config(), write with edit_config()
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from ncclient import manager
from ncclient.operations import RPCError

from fault_injector.config.schema import ChannelResult
from lib.fchannels.base import BaseChannel

logger = logging.getLogger(__name__)

NS_IFMGR_DATA = "http://www.h3c.com/netconf/data:1.0-Ifmgr"
NS_IFMGR_CONFIG = "http://www.h3c.com/netconf/config:1.0-Ifmgr"

ADMIN_STATUS_MAP = {"1": "up", "2": "down"}
OPER_STATUS_MAP = {"1": "up", "2": "down"}
DUPLEX_MAP = {"1": "full", "2": "half", "3": "auto"}
LINK_TYPE_MAP = {"1": "access", "2": "trunk", "3": "hybrid"}
LINK_TYPE_TO_INT = {"access": 1, "trunk": 2, "hybrid": 3}


@dataclass
class InterfaceStatus:
    if_index: int
    name: str = ""
    abbreviated_name: str = ""
    admin_status: str = "unknown"
    oper_status: str = "unknown"
    description: str = ""
    actual_speed: int = 0
    actual_duplex: str = "unknown"
    mac: str = ""
    pvid: int = 0
    link_type: str = "unknown"


@dataclass
class InterfaceConfig:
    if_index: int
    admin_status: str = "unknown"
    description: str = ""
    pvid: int = 0
    link_type: str = "unknown"


@dataclass
class H3CDevice:
    host: str
    username: str
    password: str
    port: int = 830
    hostkey_verify: bool = False
    timeout: int = 30


class H3CNetconfClient:
    def __init__(self, device: H3CDevice):
        self.device = device
        self._manager: Optional[manager.Manager] = None

    def connect(self) -> None:
        if self._manager is not None:
            return
        self._manager = manager.connect(
            host=self.device.host,
            port=self.device.port,
            username=self.device.username,
            password=self.device.password,
            hostkey_verify=self.device.hostkey_verify,
            device_params={"name": "h3c"},
            timeout=self.device.timeout,
        )

    def disconnect(self) -> None:
        if self._manager is None:
            return
        try:
            self._manager.close_session()
        finally:
            self._manager = None

    def get(self, filter_xml: str) -> str:
        if self._manager is None:
            raise RuntimeError("NETCONF is not connected")
        return str(self._manager.get(("subtree", filter_xml)))

    def get_config(self, filter_xml: str, source: str = "running") -> str:
        if self._manager is None:
            raise RuntimeError("NETCONF is not connected")
        return str(self._manager.get_config(source=source, filter=("subtree", filter_xml)))

    def edit_config(self, config_xml: str, target: str = "running") -> bool:
        if self._manager is None:
            raise RuntimeError("NETCONF is not connected")
        try:
            result = self._manager.edit_config(target=target, config=config_xml)
            return "<ok" in str(result).lower()
        except RPCError as exc:
            logger.error("NETCONF edit-config failed: %s", exc)
            return False


class SwitchChannel(BaseChannel):
    """Switch channel backed by H3C NETCONF."""

    def __init__(
        self,
        devices: dict[str, dict[str, Any]],
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        timeout: int = 30,
    ):
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.devices = devices
        self.timeout = timeout
        self._clients: dict[str, H3CNetconfClient] = {}

    def _get_device_config(self, name: str) -> H3CDevice:
        if name not in self.devices:
            raise ValueError(f"Unknown switch: {name}")
        dev = self.devices[name]
        return H3CDevice(
            host=dev["host"],
            port=dev.get("port", 830),
            username=dev.get("username", dev.get("user", "")),
            password=dev.get("password", ""),
            timeout=self.timeout,
        )

    def _get_client(self, name: str) -> H3CNetconfClient:
        if name not in self._clients:
            client = H3CNetconfClient(self._get_device_config(name))
            client.connect()
            self._clients[name] = client
        return self._clients[name]

    @staticmethod
    def _strip(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def _resolve_if_index(self, switch: str, interface: str | int) -> Optional[int]:
        if isinstance(interface, int):
            return interface
        if isinstance(interface, str) and interface.isdigit():
            return int(interface)
        if self.dry_run:
            return 4
        for iface in self.get_all_interfaces(switch):
            if iface.abbreviated_name == interface or iface.name == interface:
                return iface.if_index
        return None

    def get_all_interfaces(self, switch: str) -> list[InterfaceStatus]:
        if self.dry_run:
            return []
        filter_xml = f"""
        <Ifmgr xmlns=\"{NS_IFMGR_DATA}\">
          <Interfaces/>
        </Ifmgr>"""
        try:
            raw = self._get_client(switch).get(filter_xml)
            return self._parse_interfaces(raw)
        except Exception as exc:
            logger.error("Failed to get interfaces: %s", exc)
            return []

    def get_interface_by_index(self, switch: str, if_index: int) -> Optional[InterfaceStatus]:
        if self.dry_run:
            return InterfaceStatus(
                if_index=if_index,
                name=f"Interface{if_index}",
                abbreviated_name=f"GE1/0/{if_index}",
                admin_status="up",
                oper_status="up",
            )

        filter_xml = f"""
        <Ifmgr xmlns=\"{NS_IFMGR_DATA}\">
          <Interfaces>
            <Interface>
              <IfIndex>{if_index}</IfIndex>
            </Interface>
          </Interfaces>
        </Ifmgr>"""
        try:
            raw = self._get_client(switch).get(filter_xml)
            parsed = self._parse_interfaces(raw)
            return parsed[0] if parsed else None
        except Exception as exc:
            logger.error("Failed to get interface status: %s", exc)
            return None

    def get_interface_status(self, switch: str, interface: str | int) -> Optional[InterfaceStatus]:
        if_index = self._resolve_if_index(switch, interface)
        if if_index is None:
            return None
        return self.get_interface_by_index(switch, if_index)

    def get_interface_config(self, switch: str, interface: str | int) -> Optional[InterfaceConfig]:
        if_index = self._resolve_if_index(switch, interface)
        if if_index is None:
            return None

        if self.dry_run:
            return InterfaceConfig(
                if_index=if_index,
                admin_status="up",
                description="",
                pvid=1,
                link_type="trunk",
            )

        filter_xml = f"""
        <Ifmgr xmlns=\"{NS_IFMGR_CONFIG}\">
          <Interfaces>
            <Interface>
              <IfIndex>{if_index}</IfIndex>
            </Interface>
          </Interfaces>
        </Ifmgr>"""
        try:
            raw = self._get_client(switch).get_config(filter_xml)
            return self._parse_interface_config(raw)
        except Exception as exc:
            logger.error("Failed to get interface config: %s", exc)
            return None

    def apply_interface_config(
        self,
        switch: str,
        interface: str | int,
        *,
        admin_status: int | None = None,
        description: str | None = None,
        pvid: int | None = None,
        link_type: int | None = None,
    ) -> ChannelResult:
        if_index = self._resolve_if_index(switch, interface)
        if if_index is None:
            return ChannelResult(success=False, error=f"Unable to resolve IfIndex: {interface}")

        fields: list[str] = [f"<IfIndex>{if_index}</IfIndex>"]
        if admin_status is not None:
            fields.append(f"<AdminStatus>{admin_status}</AdminStatus>")
        if description is not None:
            fields.append(f"<Description>{description}</Description>")
        if pvid is not None:
            fields.append(f"<PVID>{pvid}</PVID>")
        if link_type is not None:
            fields.append(f"<LinkType>{link_type}</LinkType>")

        if len(fields) == 1:
            return ChannelResult(success=False, error="No config fields provided")

        config_xml = f"""
        <config>
          <Ifmgr xmlns=\"{NS_IFMGR_CONFIG}\">
            <Interfaces>
              <Interface>
                {''.join(fields)}
              </Interface>
            </Interfaces>
          </Ifmgr>
        </config>"""

        if self.dry_run:
            return ChannelResult(success=True, dry_run=True)

        try:
            ok = self._get_client(switch).edit_config(config_xml)
            if ok:
                return ChannelResult(success=True)
            return ChannelResult(success=False, error="NETCONF edit-config failed")
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    def shutdown_port(self, switch: str, interface: str, fault_id: Optional[str] = None) -> ChannelResult:
        if self.wal and fault_id:
            if_index = self._resolve_if_index(switch, interface)
            self.wal.record(
                fault_id=fault_id,
                channel="switch",
                target=switch,
                inject_action="shutdown_port",
                inject_params={"interface": interface, "if_index": if_index},
                recover_action="bringup_port",
                recover_params={"interface": interface, "if_index": if_index},
            )
        return self.apply_interface_config(switch, interface, admin_status=2)

    def bringup_port(self, switch: str, interface: str, fault_id: Optional[str] = None) -> ChannelResult:
        result = self.apply_interface_config(switch, interface, admin_status=1)
        if result.success and self.wal and fault_id:
            self.wal.mark_recovered(fault_id)
        return result

    def apply_raw_config(self, switch: str, config_xml: str) -> ChannelResult:
        if self.dry_run:
            return ChannelResult(success=True, dry_run=True)
        try:
            ok = self._get_client(switch).edit_config(config_xml)
            if ok:
                return ChannelResult(success=True)
            return ChannelResult(success=False, error="NETCONF edit-config failed")
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    def get_raw_data(self, switch: str, filter_xml: str) -> ChannelResult:
        if self.dry_run:
            return ChannelResult(success=True, output="", dry_run=True)
        try:
            return ChannelResult(success=True, output=self._get_client(switch).get(filter_xml))
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    def get_raw_config(self, switch: str, filter_xml: str) -> ChannelResult:
        if self.dry_run:
            return ChannelResult(success=True, output="", dry_run=True)
        try:
            return ChannelResult(success=True, output=self._get_client(switch).get_config(filter_xml))
        except Exception as exc:
            return ChannelResult(success=False, error=str(exc))

    def verify_admin_state(self, switch: str, interface: str | int, expected: str) -> bool:
        status = self.get_interface_status(switch, interface)
        return bool(status and status.admin_status == expected)

    def _parse_interfaces(self, raw_xml: str) -> list[InterfaceStatus]:
        interfaces: list[InterfaceStatus] = []
        try:
            root = ET.fromstring(raw_xml)
            for elem in root.iter():
                if self._strip(elem.tag) != "Interface":
                    continue
                iface = InterfaceStatus(if_index=0)
                for child in elem:
                    tag = self._strip(child.tag)
                    value = (child.text or "").strip()
                    if tag == "IfIndex":
                        iface.if_index = int(value) if value.isdigit() else 0
                    elif tag == "Name":
                        iface.name = value
                    elif tag == "AbbreviatedName":
                        iface.abbreviated_name = value
                    elif tag == "AdminStatus":
                        iface.admin_status = ADMIN_STATUS_MAP.get(value, value)
                    elif tag == "OperStatus":
                        iface.oper_status = OPER_STATUS_MAP.get(value, value)
                    elif tag == "Description":
                        iface.description = value
                    elif tag == "ActualSpeed":
                        iface.actual_speed = int(value) if value.isdigit() else 0
                    elif tag == "ActualDuplex":
                        iface.actual_duplex = DUPLEX_MAP.get(value, value)
                    elif tag == "MAC":
                        iface.mac = value
                    elif tag == "PVID":
                        iface.pvid = int(value) if value.isdigit() else 0
                    elif tag == "LinkType":
                        iface.link_type = LINK_TYPE_MAP.get(value, value)
                if iface.if_index > 0:
                    interfaces.append(iface)
        except ET.ParseError as exc:
            logger.error("Failed to parse interface XML: %s", exc)
        return interfaces

    def _parse_interface_config(self, raw_xml: str) -> Optional[InterfaceConfig]:
        try:
            root = ET.fromstring(raw_xml)
            for elem in root.iter():
                if self._strip(elem.tag) != "Interface":
                    continue
                cfg = InterfaceConfig(if_index=0)
                for child in elem:
                    tag = self._strip(child.tag)
                    value = (child.text or "").strip()
                    if tag == "IfIndex":
                        cfg.if_index = int(value) if value.isdigit() else 0
                    elif tag == "AdminStatus":
                        cfg.admin_status = ADMIN_STATUS_MAP.get(value, value)
                    elif tag == "Description":
                        cfg.description = value
                    elif tag == "PVID":
                        cfg.pvid = int(value) if value.isdigit() else 0
                    elif tag == "LinkType":
                        cfg.link_type = LINK_TYPE_MAP.get(value, value)
                if cfg.if_index > 0:
                    return cfg
        except ET.ParseError as exc:
            logger.error("Failed to parse interface config XML: %s", exc)
        return None

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "shutdown_port":
            return self.shutdown_port(
                switch=params["switch"],
                interface=params["interface"],
                fault_id=params.get("fault_id"),
            )
        if action == "bringup_port":
            return self.bringup_port(
                switch=params["switch"],
                interface=params["interface"],
                fault_id=params.get("fault_id"),
            )
        if action == "get_interface_status":
            status = self.get_interface_status(params["switch"], params["interface"])
            if not status:
                return ChannelResult(success=False, error="Interface not found")
            return ChannelResult(
                success=True,
                output=f"{status.abbreviated_name}: admin={status.admin_status}, oper={status.oper_status}",
            )
        if action == "get_all_interfaces":
            interfaces = self.get_all_interfaces(params["switch"])
            output = "\n".join(
                f"{i.abbreviated_name}: admin={i.admin_status}, oper={i.oper_status}" for i in interfaces
            )
            return ChannelResult(success=True, output=output)
        if action == "apply_raw_config":
            return self.apply_raw_config(params["switch"], params["config_xml"])
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        if not self.guard:
            return
        action_text = f"switch:{action}"
        self.guard.check_command(action_text, "switch")

        if "interface" in params:
            interface = str(params["interface"])
            if not re.fullmatch(r"[A-Za-z0-9/._-]+", interface):
                raise ValueError(f"Unsafe interface value: {interface}")

    def close(self) -> None:
        for client in self._clients.values():
            try:
                client.disconnect()
            except Exception as exc:
                logger.warning("Failed to close switch connection: %s", exc)
        self._clients.clear()

    def test_connection(self, switch: str) -> bool:
        if self.dry_run:
            return True
        try:
            return len(self.get_all_interfaces(switch)) > 0
        except Exception:
            return False
