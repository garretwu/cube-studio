#!/usr/bin/env python3
"""
交换机操作测试脚本

测试 SwitchChannel 的端口操作 (shutdown/bringup)。
使用 tests/fixtures/switch_config.yaml 中的配置。

⚠️ 警告: 此测试会实际关闭和开启端口，请确保使用测试专用端口!

Usage:
    python -m fault_injector.tests.integration.test_switch_operations
    python -m fault_injector.tests.integration.test_switch_operations --dry-run
"""
import argparse
import io
import logging
import sys
import time
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

from lib.channels.switch import (
    SwitchChannel,
    InterfaceStatus,
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


def test_shutdown_port(dry_run: bool = True):
    """测试关闭端口"""
    print("\n" + "=" * 60)
    print("测试 1: 关闭端口 (shutdown)")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    test_interfaces = config.get("test_interfaces", [])
    
    if not test_interfaces:
        print("⚠️ 没有配置测试接口，跳过此测试")
        return True
    
    channel = SwitchChannel(devices=switches, dry_run=dry_run)
    
    try:
        switch_name = list(switches.keys())[0]
        
        for test_if in test_interfaces:
            iface_name = test_if["name"]
            print(f"\n关闭端口: {iface_name}")
            print(f"  Dry-run: {dry_run}")
            
            # 先获取当前状态
            if not dry_run:
                status_before = channel.get_interface_status(switch_name, iface_name)
                if status_before:
                    print(f"  当前状态: admin={status_before.admin_status}")
            
            # 关闭端口
            result = channel.shutdown_port(
                switch=switch_name,
                interface=iface_name,
                fault_id=f"test_shutdown_{iface_name}",
            )
            
            if result.success:
                print(f"  ✅ 关闭端口成功")
                if result.dry_run:
                    print(f"     (dry-run 模式，未实际执行)")
            else:
                print(f"  ❌ 关闭端口失败: {result.error}")
                return False
            
            # 验证状态
            if not dry_run:
                time.sleep(2)  # 等待配置生效
                status_after = channel.get_interface_status(switch_name, iface_name)
                if status_after:
                    print(f"  验证状态: admin={status_after.admin_status}")
                    if status_after.admin_status != "down":
                        print(f"  ⚠️ 端口未成功关闭")
                        return False
            
        return True
    finally:
        channel.close()


def test_bringup_port(dry_run: bool = True):
    """测试开启端口"""
    print("\n" + "=" * 60)
    print("测试 2: 开启端口 (undo shutdown)")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    test_interfaces = config.get("test_interfaces", [])
    
    if not test_interfaces:
        print("⚠️ 没有配置测试接口，跳过此测试")
        return True
    
    channel = SwitchChannel(devices=switches, dry_run=dry_run)
    
    try:
        switch_name = list(switches.keys())[0]
        
        for test_if in test_interfaces:
            iface_name = test_if["name"]
            print(f"\n开启端口: {iface_name}")
            print(f"  Dry-run: {dry_run}")
            
            # 先获取当前状态
            if not dry_run:
                status_before = channel.get_interface_status(switch_name, iface_name)
                if status_before:
                    print(f"  当前状态: admin={status_before.admin_status}")
            
            # 开启端口
            result = channel.bringup_port(
                switch=switch_name,
                interface=iface_name,
                fault_id=f"test_bringup_{iface_name}",
            )
            
            if result.success:
                print(f"  ✅ 开启端口成功")
                if result.dry_run:
                    print(f"     (dry-run 模式，未实际执行)")
            else:
                print(f"  ❌ 开启端口失败: {result.error}")
                return False
            
            # 验证状态
            if not dry_run:
                time.sleep(2)  # 等待配置生效
                status_after = channel.get_interface_status(switch_name, iface_name)
                if status_after:
                    print(f"  验证状态: admin={status_after.admin_status}")
                    if status_after.admin_status != "up":
                        print(f"  ⚠️ 端口未成功开启")
                        return False
            
        return True
    finally:
        channel.close()


def test_shutdown_bringup_cycle(dry_run: bool = True):
    """测试完整的关闭-开启周期"""
    print("\n" + "=" * 60)
    print("测试 3: 完整关闭-开启周期")
    print("=" * 60)
    
    config = load_config()
    switches = config.get("switches", {})
    test_interfaces = config.get("test_interfaces", [])
    
    if not test_interfaces:
        print("⚠️ 没有配置测试接口，跳过此测试")
        return True
    
    channel = SwitchChannel(devices=switches, dry_run=dry_run)
    
    try:
        switch_name = list(switches.keys())[0]
        iface_name = test_interfaces[0]["name"]
        
        print(f"\n测试接口: {iface_name}")
        print(f"  Dry-run: {dry_run}")
        
        # 1. 获取初始状态
        print("\n步骤 1: 获取初始状态")
        if not dry_run:
            initial_status = channel.get_interface_status(switch_name, iface_name)
            if initial_status:
                print(f"  初始状态: admin={initial_status.admin_status}")
        else:
            print("  (dry-run 模式，跳过)")
        
        # 2. 关闭端口
        print("\n步骤 2: 关闭端口")
        result = channel.shutdown_port(
            switch=switch_name,
            interface=iface_name,
            fault_id=f"test_cycle_{iface_name}",
        )
        if not result.success:
            print(f"  ❌ 关闭失败: {result.error}")
            return False
        print(f"  ✅ 关闭成功")
        
        if not dry_run:
            time.sleep(2)
            status = channel.get_interface_status(switch_name, iface_name)
            if status:
                print(f"  验证: admin={status.admin_status}")
        
        # 3. 开启端口
        print("\n步骤 3: 开启端口")
        result = channel.bringup_port(
            switch=switch_name,
            interface=iface_name,
            fault_id=f"test_cycle_{iface_name}",
        )
        if not result.success:
            print(f"  ❌ 开启失败: {result.error}")
            return False
        print(f"  ✅ 开启成功")
        
        if not dry_run:
            time.sleep(2)
            status = channel.get_interface_status(switch_name, iface_name)
            if status:
                print(f"  验证: admin={status.admin_status}")
        
        # 4. 验证最终状态
        print("\n步骤 4: 验证最终状态")
        if not dry_run:
            final_status = channel.get_interface_status(switch_name, iface_name)
            if final_status:
                print(f"  最终状态: admin={final_status.admin_status}")
                if final_status.admin_status != "up":
                    print(f"  ⚠️ 端口未恢复到 up 状态")
                    return False
        else:
            print("  (dry-run 模式，跳过)")
        
        print("\n✅ 完整周期测试通过")
        return True
        
    finally:
        channel.close()


def test_interface_by_index():
    """测试通过 IfIndex 获取接口"""
    print("\n" + "=" * 60)
    print("测试 4: 通过 IfIndex 获取接口")
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
            if_index = test_if.get("if_index")
            if not if_index:
                print(f"⚠️ 接口 {test_if['name']} 没有配置 if_index，跳过")
                continue
            
            print(f"\n通过 IfIndex 获取接口: {if_index}")
            
            status = channel.get_interface_by_index(switch_name, if_index)
            
            if status is None:
                print(f"  ❌ 接口未找到")
                return False
            
            print(f"  ✅ 接口信息:")
            print(f"    - IfIndex: {status.if_index}")
            print(f"    - AbbreviatedName: {status.abbreviated_name}")
            print(f"    - AdminStatus: {status.admin_status}")
            print(f"    - OperStatus: {status.oper_status}")
            
            # 验证 IfIndex 匹配
            if status.if_index != if_index:
                print(f"  ⚠️ IfIndex 不匹配: 期望 {if_index}, 实际 {status.if_index}")
                return False
        
        return True
    finally:
        channel.close()


def main():
    """运行所有测试"""
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description="交换机操作测试")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干运行模式，不实际执行操作",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="实际执行模式（危险！会修改端口状态）",
    )
    args = parser.parse_args()
    
    # 默认使用 dry-run，除非显式指定 --real
    dry_run = not args.real
    
    print("=" * 60)
    print("H3C 交换机操作测试")
    print("=" * 60)
    print(f"模式: {'DRY-RUN (不实际执行)' if dry_run else '实际执行 (会修改端口状态!)'}")
    print("=" * 60)
    
    if not dry_run:
        print("\n⚠️ 警告: 实际执行模式!")
        print("⚠️ 请确保测试接口不会影响生产网络!")
        response = input("确认继续? (yes/no): ")
        if response.lower() != "yes":
            print("已取消测试")
            return 1
    
    results = []
    
    # 测试 1: 关闭端口
    results.append(("关闭端口", test_shutdown_port(dry_run)))
    
    # 测试 2: 开启端口
    results.append(("开启端口", test_bringup_port(dry_run)))
    
    # 测试 3: 完整周期
    results.append(("完整关闭-开启周期", test_shutdown_bringup_cycle(dry_run)))
    
    # 测试 4: 通过 IfIndex 获取接口 (只读，始终实际执行)
    if not dry_run:
        results.append(("通过 IfIndex 获取接口", test_interface_by_index()))
    
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
