#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NETCONF 连接测试脚本

测试到 H3C 交换机的 NETCONF 连接。
使用 tests/fixtures/switch_config.yaml 中的配置。

Usage:
    python -m fault_injector.tests.integration.test_netconf_connection
"""
import io
import logging
import sys
from pathlib import Path

import yaml

def _configure_console_encoding() -> None:
    """Fix Windows console encoding when running as script."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 添加项目根目录到 path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from lib.fchannels.switch import (
    H3CDevice,
    H3CNetconfClient,
    SwitchChannel,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """加载测试配置"""
    config_path = Path(__file__).parent.parent / "fixtures" / "switch_config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_netconf_basic_connection():
    """测试基本 NETCONF 连接"""
    print("\n" + "=" * 60)
    print("测试 1: 基本 NETCONF 连接")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    
    if not switches:
        print("❌ 没有配置交换机")
        return False
    
    for name, sw_config in switches.items():
        print(f"\n连接交换机: {name}")
        print(f"  主机: {sw_config['host']}")
        print(f"  端口: {sw_config.get('port', 830)}")
        print(f"  用户: {sw_config.get('username', sw_config.get('user', ''))}")
        
        device = H3CDevice(
            host=sw_config["host"],
            port=sw_config.get("port", 830),
            username=sw_config.get("username", sw_config.get("user", "")),
            password=sw_config.get("password", ""),
        )
        
        client = H3CNetconfClient(device)
        
        try:
            client.connect()
            print("  ✅ NETCONF 连接成功")
            client.disconnect()
            print("  ✅ NETCONF 断开成功")
            return True
        except Exception as e:
            print(f"  ❌ 连接失败: {e}")
            return False


def test_switch_channel_connection():
    """测试 SwitchChannel 连接"""
    print("\n" + "=" * 60)
    print("测试 2: SwitchChannel 连接")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    
    channel = SwitchChannel(devices=switches, dry_run=False)
    
    try:
        for name in switches:
            print(f"\n测试连接: {name}")
            result = channel.test_connection(name)
            if result:
                print(f"  ✅ 连接测试成功")
            else:
                print(f"  ❌ 连接测试失败")
                return False
        return True
    finally:
        channel.close()


def test_get_all_interfaces():
    """测试获取所有接口"""
    print("\n" + "=" * 60)
    print("测试 3: 获取所有接口")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    
    channel = SwitchChannel(devices=switches, dry_run=False)
    
    try:
        for name in switches:
            print(f"\n获取接口列表: {name}")
            interfaces = channel.get_all_interfaces(name)
            
            if not interfaces:
                print(f"  ❌ 未获取到接口")
                return False
            
            print(f"  ✅ 获取到 {len(interfaces)} 个接口")
            
            # 显示前 5 个接口
            for iface in interfaces[:5]:
                print(f"    - {iface.abbreviated_name}: admin={iface.admin_status}, oper={iface.oper_status}")
            
            if len(interfaces) > 5:
                print(f"    ... 还有 {len(interfaces) - 5} 个接口")
            
            return True
    finally:
        channel.close()


def test_get_interface_status():
    """测试获取特定接口状态"""
    print("\n" + "=" * 60)
    print("测试 4: 获取特定接口状态")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    test_interfaces = config.get("test_interfaces", [])
    
    if not test_interfaces:
        print("⚠️ 没有配置测试接口，跳过此测试")
        return True
    
    channel = SwitchChannel(devices=switches, dry_run=False)
    
    try:
        switch_name = list(switches.keys())[0]
        
        for test_if in test_interfaces:
            iface_name = test_if["name"]
            print(f"\n获取接口状态: {iface_name}")
            
            status = channel.get_interface_status(switch_name, iface_name)
            
            if status is None:
                print(f"  ❌ 接口未找到")
                return False
            
            print(f"  ✅ 接口状态:")
            print(f"    - IfIndex: {status.if_index}")
            print(f"    - Name: {status.name}")
            print(f"    - AbbreviatedName: {status.abbreviated_name}")
            print(f"    - AdminStatus: {status.admin_status}")
            print(f"    - OperStatus: {status.oper_status}")
            print(f"    - Description: {status.description}")
            print(f"    - MAC: {status.mac}")
            print(f"    - Speed: {status.actual_speed}")
            print(f"    - Duplex: {status.actual_duplex}")
            print(f"    - LinkType: {status.link_type}")
            print(f"    - PVID: {status.pvid}")
            
        return True
    finally:
        channel.close()


def main():
    """运行所有测试"""
    _configure_console_encoding()
    print("=" * 60)
    print("H3C NETCONF 连接测试")
    print("=" * 60)
    
    results = []
    
    # 测试 1: 基本连接
    results.append(("基本 NETCONF 连接", test_netconf_basic_connection()))
    
    # 测试 2: SwitchChannel 连接
    results.append(("SwitchChannel 连接", test_switch_channel_connection()))
    
    # 测试 3: 获取所有接口
    results.append(("获取所有接口", test_get_all_interfaces()))
    
    # 测试 4: 获取特定接口状态
    results.append(("获取特定接口状态", test_get_interface_status()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过!")
    else:
        print("⚠️ 部分测试失败")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
