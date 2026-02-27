"""
SwitchChannel — H3C 交换机 NETCONF 管理 Channel

基于 ncclient 实现 H3C Comware 9 交换机的 NETCONF 管理。
遵循 H3C_NETCONF_GUIDE.md 规范。
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from ncclient import manager
from ncclient.operations import RPCError

from lib.channels.base import BaseChannel
from fault_injector.config.schema import ChannelResult

logger = logging.getLogger(__name__)


# ============================================================================
# H3C NETCONF Namespaces (from H3C_NETCONF_GUIDE.md)
# ============================================================================

NS_IFMGR_DATA = "http://www.h3c.com/netconf/data:1.0-Ifmgr"
NS_IFMGR_CONFIG = "http://www.h3c.com/netconf/config:1.0-Ifmgr"


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class InterfaceStatus:
    """接口状态信息"""
    if_index: int
    name: str = ""
    abbreviated_name: str = ""
    admin_status: str = "unknown"  # "up" or "down"
    oper_status: str = "unknown"   # "up" or "down"
    description: str = ""
    actual_speed: int = 0
    actual_duplex: str = "unknown"
    mac: str = ""
    pvid: int = 0
    link_type: str = "unknown"


@dataclass
class H3CDevice:
    """H3C 设备配置"""
    host: str
    username: str
    password: str
    port: int = 830
    hostkey_verify: bool = False
    timeout: int = 30


# ============================================================================
# Integer Mappings (from H3C_NETCONF_GUIDE.md)
# ============================================================================

ADMIN_STATUS_MAP = {"1": "up", "2": "down"}
OPER_STATUS_MAP = {"1": "up", "2": "down"}
DUPLEX_MAP = {"1": "full", "2": "half", "3": "auto"}
LINK_TYPE_MAP = {"1": "access", "2": "trunk", "3": "hybrid"}


# ============================================================================
# H3C NETCONF Client
# ============================================================================

class H3CNetconfClient:
    """
    H3C NETCONF 客户端。
    
    使用 ncclient 连接 H3C Comware 9 设备。
    必须使用 device_params={"name": "h3c"} 确保正确的 RPC framing。
    """
    
    def __init__(self, device: H3CDevice):
        self.device = device
        self._manager: Optional[manager.Manager] = None
    
    def connect(self) -> None:
        """建立 NETCONF 连接"""
        if self._manager is not None:
            return
        
        self._manager = manager.connect(
            host=self.device.host,
            port=self.device.port,
            username=self.device.username,
            password=self.device.password,
            hostkey_verify=self.device.hostkey_verify,
            device_params={"name": "h3c"},  # 必须指定 h3c
            timeout=self.device.timeout,
        )
        logger.info(f"NETCONF 连接成功: {self.device.host}:{self.device.port}")
    
    def disconnect(self) -> None:
        """断开 NETCONF 连接"""
        if self._manager is not None:
            try:
                self._manager.close_session()
            except Exception as e:
                logger.warning(f"关闭 NETCONF 会话失败: {e}")
            finally:
                self._manager = None
                logger.info(f"NETCONF 连接关闭: {self.device.host}")
    
    def get(self, filter_xml: str) -> str:
        """
        执行 NETCONF get 操作。
        
        Args:
            filter_xml: subtree filter XML
            
        Returns:
            str: XML 响应字符串
        """
        if self._manager is None:
            raise RuntimeError("NETCONF 未连接")
        # ncclient expects a filter element or (type, criteria) tuple.
        # Use subtree filter with the provided XML content.
        result = self._manager.get(("subtree", filter_xml))
        return str(result)
    
    def get_config(self, filter_xml: str, source: str = "running") -> str:
        """
        执行 NETCONF get-config 操作。
        
        Args:
            filter_xml: subtree filter XML
            source: 配置源 (running/startup/candidate)
            
        Returns:
            str: XML 响应字符串
        """
        if self._manager is None:
            raise RuntimeError("NETCONF 未连接")
        # ncclient expects a filter element or (type, criteria) tuple.
        # Use subtree filter with the provided XML content.
        result = self._manager.get_config(source=source, filter=("subtree", filter_xml))
        return str(result)
    
    def edit_config(self, config_xml: str, target: str = "running") -> bool:
        """
        执行 NETCONF edit-config 操作。
        
        Args:
            config_xml: config 元素 XML
            target: 目标配置 (running/candidate)
            
        Returns:
            bool: 操作是否成功
        """
        if self._manager is None:
            raise RuntimeError("NETCONF 未连接")
        try:
            result = self._manager.edit_config(target=target, config=config_xml)
            return "<ok" in str(result).lower()
        except RPCError as e:
            logger.error(f"NETCONF edit-config 失败: {e}")
            return False
    
    @staticmethod
    def pretty_print(xml_string: str) -> str:
        """格式化 XML 输出"""
        try:
            root = ET.fromstring(xml_string)
            ET.indent(root, space="  ")
            return ET.tostring(root, encoding="unicode")
        except ET.ParseError:
            return xml_string


# ============================================================================
# Switch Channel
# ============================================================================

class SwitchChannel(BaseChannel):
    """
    H3C 交换机 NETCONF Channel。
    
    功能：
    - NETCONF 连接管理
    - 接口状态查询
    - 端口 shutdown/undo shutdown
    - 自动注册恢复操作到 WAL
    """
    
    def __init__(
        self,
        devices: dict[str, dict[str, Any]],
        dry_run: bool = False,
        wal: Any = None,
        guard: Any = None,
        timeout: int = 30,
    ):
        """
        初始化 Switch Channel。
        
        Args:
            devices: 设备配置字典 {device_name: {host, port, username, password, ...}}
            dry_run: 干运行模式
            wal: 回滚日志
            guard: 安全守卫
            timeout: NETCONF 操作超时（秒）
        """
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.devices = devices
        self.timeout = timeout
        self._clients: dict[str, H3CNetconfClient] = {}
    
    def _get_device_config(self, name: str) -> H3CDevice:
        """获取设备配置"""
        if name not in self.devices:
            raise ValueError(f"未知交换机: {name}")
        dev = self.devices[name]
        return H3CDevice(
            host=dev["host"],
            port=dev.get("port", 830),
            username=dev.get("username", dev.get("user", "")),
            password=dev.get("password", ""),
            timeout=self.timeout,
        )
    
    def _get_client(self, name: str) -> H3CNetconfClient:
        """获取或创建 NETCONF 客户端"""
        if name not in self._clients:
            device = self._get_device_config(name)
            self._clients[name] = H3CNetconfClient(device)
            self._clients[name].connect()
        return self._clients[name]
    
    # ========================================================================
    # Interface Operations
    # ========================================================================
    
    def get_interface_status(
        self, switch: str, interface: str
    ) -> Optional[InterfaceStatus]:
        """
        获取接口状态。
        
        Args:
            switch: 交换机名称
            interface: 接口名称 (如 "200GE1/0/4")
            
        Returns:
            InterfaceStatus 或 None
        """
        # 先获取 IfIndex
        if_index = self._resolve_if_index(switch, interface)
        if if_index is None:
            logger.warning(f"无法解析接口 IfIndex: {interface}")
            return None
        
        return self.get_interface_by_index(switch, if_index)
    
    def get_interface_by_index(
        self, switch: str, if_index: int
    ) -> Optional[InterfaceStatus]:
        """
        通过 IfIndex 获取接口状态。
        
        Args:
            switch: 交换机名称
            if_index: 接口索引
            
        Returns:
            InterfaceStatus 或 None
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] 获取接口状态: switch={switch}, if_index={if_index}")
            return InterfaceStatus(
                if_index=if_index,
                name=f"Interface{if_index}",
                abbreviated_name=f"GE1/0/{if_index}",
                admin_status="up",
                oper_status="up",
            )
        
        client = self._get_client(switch)
        
        filter_xml = f"""
        <Ifmgr xmlns="{NS_IFMGR_DATA}">
          <Interfaces>
            <Interface>
              <IfIndex>{if_index}</IfIndex>
            </Interface>
          </Interfaces>
        </Ifmgr>"""
        
        try:
            raw = client.get(filter_xml)
            interfaces = self._parse_interfaces(raw)
            return interfaces[0] if interfaces else None
        except Exception as e:
            logger.error(f"获取接口状态失败: {e}")
            return None
    
    def get_all_interfaces(self, switch: str) -> list[InterfaceStatus]:
        """
        获取所有接口状态。
        
        Args:
            switch: 交换机名称
            
        Returns:
            list[InterfaceStatus]: 接口列表
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] 获取所有接口: switch={switch}")
            return []
        
        client = self._get_client(switch)
        
        # H3C Comware 9 支持 not-need-top capability
        # 直接使用模块名 Ifmgr 作为根元素（参考 H3C_NETCONF_GUIDE.md）
        filter_xml = f"""
        <Ifmgr xmlns="{NS_IFMGR_DATA}">
          <Interfaces/>
        </Ifmgr>"""
        
        try:
            raw = client.get(filter_xml)
            logger.debug(f"获取接口原始响应: {raw[:500]}...")
            return self._parse_interfaces(raw)
        except Exception as e:
            logger.error(f"获取所有接口失败: {e}")
            return []
    
    def _resolve_if_index(self, switch: str, abbreviated_name: str) -> Optional[int]:
        """
        解析接口名称到 IfIndex。
        
        Args:
            switch: 交换机名称
            abbreviated_name: 简短接口名 (如 "200GE1/0/4")
            
        Returns:
            int: IfIndex 或 None
        """
        if self.dry_run:
            # 干运行模式下返回一个假定的索引
            return 4
        
        interfaces = self.get_all_interfaces(switch)
        for iface in interfaces:
            if iface.abbreviated_name == abbreviated_name:
                return iface.if_index
        return None
    
    def shutdown_port(
        self,
        switch: str,
        interface: str,
        fault_id: Optional[str] = None,
    ) -> ChannelResult:
        """
        关闭端口 (shutdown)。
        
        Args:
            switch: 交换机名称
            interface: 接口名称或 IfIndex
            fault_id: 故障 ID（用于 WAL）
            
        Returns:
            ChannelResult
        """
        # 解析 IfIndex
        if isinstance(interface, str) and not interface.isdigit():
            if_index = self._resolve_if_index(switch, interface)
            if if_index is None:
                return ChannelResult(
                    success=False,
                    error=f"无法解析接口 IfIndex: {interface}"
                )
        else:
            if_index = int(interface)
        
        logger.info(f"关闭端口: switch={switch}, interface={interface}, if_index={if_index}")
        
        # 写入 WAL
        if self.wal and fault_id:
            self.wal.record(
                fault_id=fault_id,
                channel="switch",
                target=switch,
                inject_action="shutdown_port",
                inject_params={"interface": interface, "if_index": if_index},
                recover_action="bringup_port",
                recover_params={"interface": interface, "if_index": if_index},
            )
        
        # dry_run 模式
        if self.dry_run:
            logger.info(f"[DRY-RUN] 关闭端口: switch={switch}, if_index={if_index}")
            return ChannelResult(success=True, dry_run=True)
        
        # 执行 NETCONF edit-config
        config_xml = f"""
        <config>
          <Ifmgr xmlns="{NS_IFMGR_CONFIG}">
            <Interfaces>
              <Interface>
                <IfIndex>{if_index}</IfIndex>
                <AdminStatus>2</AdminStatus>
              </Interface>
            </Interfaces>
          </Ifmgr>
        </config>"""
        
        try:
            client = self._get_client(switch)
            success = client.edit_config(config_xml)
            if success:
                logger.info(f"端口关闭成功: switch={switch}, if_index={if_index}")
                return ChannelResult(success=True)
            else:
                return ChannelResult(success=False, error="NETCONF edit-config 失败")
        except Exception as e:
            logger.error(f"关闭端口失败: {e}")
            return ChannelResult(success=False, error=str(e))
    
    def bringup_port(
        self,
        switch: str,
        interface: str,
        fault_id: Optional[str] = None,
    ) -> ChannelResult:
        """
        开启端口 (undo shutdown)。
        
        Args:
            switch: 交换机名称
            interface: 接口名称或 IfIndex
            fault_id: 故障 ID（用于标记恢复）
            
        Returns:
            ChannelResult
        """
        # 解析 IfIndex
        if isinstance(interface, str) and not interface.isdigit():
            if_index = self._resolve_if_index(switch, interface)
            if if_index is None:
                return ChannelResult(
                    success=False,
                    error=f"无法解析接口 IfIndex: {interface}"
                )
        else:
            if_index = int(interface)
        
        logger.info(f"开启端口: switch={switch}, interface={interface}, if_index={if_index}")
        
        # 标记恢复
        if self.wal and fault_id:
            self.wal.mark_recovered(fault_id)
        
        # dry_run 模式
        if self.dry_run:
            logger.info(f"[DRY-RUN] 开启端口: switch={switch}, if_index={if_index}")
            return ChannelResult(success=True, dry_run=True)
        
        # 执行 NETCONF edit-config
        config_xml = f"""
        <config>
          <Ifmgr xmlns="{NS_IFMGR_CONFIG}">
            <Interfaces>
              <Interface>
                <IfIndex>{if_index}</IfIndex>
                <AdminStatus>1</AdminStatus>
              </Interface>
            </Interfaces>
          </Ifmgr>
        </config>"""
        
        try:
            client = self._get_client(switch)
            success = client.edit_config(config_xml)
            if success:
                logger.info(f"端口开启成功: switch={switch}, if_index={if_index}")
                return ChannelResult(success=True)
            else:
                return ChannelResult(success=False, error="NETCONF edit-config 失败")
        except Exception as e:
            logger.error(f"开启端口失败: {e}")
            return ChannelResult(success=False, error=str(e))
    
    # ========================================================================
    # XML Parsing
    # ========================================================================
    
    def _parse_interfaces(self, raw_xml: str) -> list[InterfaceStatus]:
        """
        解析接口状态 XML。
        
        Args:
            raw_xml: NETCONF get 响应 XML
            
        Returns:
            list[InterfaceStatus]
        """
        interfaces = []
        
        try:
            root = ET.fromstring(raw_xml)
            
            for elem in root.iter():
                # 去除 namespace 前缀
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                
                if tag != "Interface":
                    continue
                
                iface = InterfaceStatus(if_index=0)
                
                for child in elem:
                    t = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    v = (child.text or "").strip()
                    
                    if t == "IfIndex":
                        iface.if_index = int(v) if v.isdigit() else 0
                    elif t == "Name":
                        iface.name = v
                    elif t == "AbbreviatedName":
                        iface.abbreviated_name = v
                    elif t == "AdminStatus":
                        iface.admin_status = ADMIN_STATUS_MAP.get(v, v)
                    elif t == "OperStatus":
                        iface.oper_status = OPER_STATUS_MAP.get(v, v)
                    elif t == "Description":
                        iface.description = v
                    elif t == "ActualSpeed":
                        iface.actual_speed = int(v) if v.isdigit() else 0
                    elif t == "ActualDuplex":
                        iface.actual_duplex = DUPLEX_MAP.get(v, v)
                    elif t == "MAC":
                        iface.mac = v
                    elif t == "PVID":
                        iface.pvid = int(v) if v.isdigit() else 0
                    elif t == "LinkType":
                        iface.link_type = LINK_TYPE_MAP.get(v, v)
                
                if iface.if_index > 0:
                    interfaces.append(iface)
                    
        except ET.ParseError as e:
            logger.error(f"解析接口 XML 失败: {e}")
        
        return interfaces
    
    # ========================================================================
    # BaseChannel Implementation
    # ========================================================================
    
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
        if action == "shutdown_port":
            return self.shutdown_port(
                switch=params["switch"],
                interface=params["interface"],
                fault_id=params.get("fault_id"),
            )
        elif action == "bringup_port":
            return self.bringup_port(
                switch=params["switch"],
                interface=params["interface"],
                fault_id=params.get("fault_id"),
            )
        elif action == "get_interface_status":
            status = self.get_interface_status(
                switch=params["switch"],
                interface=params["interface"],
            )
            if status:
                return ChannelResult(
                    success=True,
                    output=f"{status.abbreviated_name}: admin={status.admin_status}, oper={status.oper_status}",
                )
            else:
                return ChannelResult(success=False, error="接口未找到")
        elif action == "get_all_interfaces":
            interfaces = self.get_all_interfaces(switch=params["switch"])
            output = "\n".join(
                f"{i.abbreviated_name}: admin={i.admin_status}, oper={i.oper_status}"
                for i in interfaces
            )
            return ChannelResult(success=True, output=output)
        else:
            return ChannelResult(success=False, error=f"未知操作: {action}")
    
    def close(self) -> None:
        """关闭所有连接"""
        for name, client in self._clients.items():
            try:
                client.disconnect()
            except Exception as e:
                logger.warning(f"关闭交换机连接失败 ({name}): {e}")
        self._clients.clear()
    
    def test_connection(self, switch: str) -> bool:
        """
        测试到交换机的连接。
        
        Args:
            switch: 交换机名称
            
        Returns:
            bool: 连接是否成功
        """
        try:
            if self.dry_run:
                logger.info(f"[DRY-RUN] 测试连接: switch={switch}")
                return True
            
            client = self._get_client(switch)
            # 尝试获取接口列表来验证连接
            interfaces = self.get_all_interfaces(switch)
            return len(interfaces) > 0
        except Exception as e:
            logger.error(f"连接测试失败: {e}")
            return False
