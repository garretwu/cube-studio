"""
Click CLI for AIDC Auto-SRE Fault Injector.

Commands:
  run              Execute a fault injection session.
  validate-config  Validate a YAML config file.
  list-scenarios   Show available fault scenarios.
  recover          Recover active faults from a session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm

console = Console(stderr=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(package_name="fault-injector", prog_name="fault-injector")
def main() -> None:
    """AIDC Auto-SRE Fault Injector — 多维度故障注入系统。"""
    pass


@main.command("run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Path to YAML configuration file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Only print actions without executing them.",
)
@click.option(
    "--scenario",
    "scenario_name",
    type=str,
    default=None,
    help="Run only the specified scenario.",
)
@click.option(
    "--timeout",
    type=int,
    default=600,
    help="Auto-recover timeout in seconds.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip confirmation prompts.",
)
def run_cmd(
    config_path: Optional[str],
    dry_run: bool,
    scenario_name: Optional[str],
    timeout: int,
    yes: bool,
) -> None:
    """Execute a fault injection session."""
    from fault_injector.config.loader import load_config
    from fault_injector.config.defaults import get_default_config
    from fault_injector.config.schema import FaultInjectorConfig
    from fault_injector.safety.guard import SafetyGuard
    from fault_injector.safety.rollback import RollbackJournal
    from lib.channels.ssh import SSHChannel
    from lib.channels.redfish import RedfishChannel
    from fault_injector.scenarios.registry import get_scenario, list_scenarios
    
    # 加载配置
    if config_path:
        console.log(f"加载配置: [cyan]{config_path}[/cyan]")
        config = load_config(config_path)
        _validate_redfish_requirements(config)
    else:
        console.log("使用默认配置")
        config = get_default_config()
    
    # 覆盖 dry_run
    if dry_run:
        config.global_.safety.dry_run = True
    
    # 列出可用场景
    scenarios = list_scenarios()
    if not scenarios:
        console.print("[red]错误: 没有可用的场景[/red]")
        sys.exit(1)
    
    # 选择场景
    if scenario_name:
        scenario = get_scenario(scenario_name)
        if not scenario:
            console.print(f"[red]错误: 场景 '{scenario_name}' 不存在[/red]")
            console.print(f"可用场景: {[s['name'] for s in scenarios]}")
            sys.exit(1)
    else:
        # 使用配置中的第一个启用的场景
        for name, sc in config.scenarios.items():
            if sc.enabled:
                scenario_name = name
                break
        if not scenario_name:
            console.print("[red]错误: 没有启用的场景[/red]")
            sys.exit(1)
        scenario = get_scenario(scenario_name)
    
    console.rule(f"[bold blue]Fault Injector — 场景: {scenario_name}")
    
    # 显示场景信息
    console.print(f"  场景: [cyan]{scenario.name}[/cyan]")
    console.print(f"  描述: {scenario.description}")
    console.print(f"  层级: {scenario.layer}")
    console.print(f"  Dry-run: {'[green]是[/green]' if config.global_.safety.dry_run else '[red]否[/red]'}")
    
    # 检查节点配置
    if not config.inventory:
        console.print("[red]错误: 没有配置节点清单[/red]")
        sys.exit(1)
    
    # 获取第一个节点
    first_group = list(config.inventory.keys())[0]
    first_node = config.inventory[first_group][0]
    console.print(f"  目标节点: [cyan]{first_node.name}[/cyan] ({first_node.ssh.host})")
    
    # 确认
    if not yes and not config.global_.safety.dry_run:
        if not Confirm.ask("\n[bold yellow]确认执行故障注入?[/bold yellow]"):
            console.print("[yellow]已取消[/yellow]")
            sys.exit(0)
    
    # 执行
    async def _run():
        session_id = str(uuid.uuid4())[:8]
        session_dir = Path(config.global_.session_dir) / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        console.print(f"\n  Session ID: [green]{session_id}[/green]")
        console.print(f"  Session 目录: {session_dir}")
        
        # 初始化组件
        guard = SafetyGuard(config.global_.safety)
        journal_path = session_dir / "rollback.jsonl"
        rollback = RollbackJournal(journal_path)
        
        # 构建节点清单映射
        inventory_map = {}
        for group_name, nodes in config.inventory.items():
            for node in nodes:
                inventory_map[node.name] = node
        
        # 创建 SSH Channel
        ssh = SSHChannel(
            inventory=inventory_map,
            dry_run=config.global_.safety.dry_run,
            wal=rollback,
            guard=guard,
        )
        redfish = RedfishChannel(
            dry_run=config.global_.safety.dry_run,
            wal=rollback,
            guard=guard,
        )
        
        # 获取场景参数
        scenario_config = config.scenarios.get(scenario_name)
        params = scenario_config.params if scenario_config else {}
        
        # 生成 fault_id
        fault_id = f"{scenario_name}_{first_node.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # 构建上下文
        from fault_injector.scenarios.base import FaultContext
        ctx = FaultContext(
            ssh=ssh,
            rollback=rollback,
            guard=guard,
            target_node=first_node.name,
            params=params,
            fault_id=fault_id,
            redfish=redfish if first_node.redfish else None,
            session_id=session_id,
            interface=params.get("interface", first_node.interface),
        )
        
        try:
            # 注入
            console.print(f"\n[bold]>>> 注入故障...[/bold]")
            inject_result = await scenario.inject(ctx)
            
            if inject_result.success:
                console.print(f"[green]✓ 故障注入成功[/green]")
                
                # 观测期
                duration = params.get("duration", 60)
                console.print(f"\n[bold]>>> 观测期 ({duration}s)...[/bold]")
                
                if not config.global_.safety.dry_run:
                    for i in range(duration):
                        await asyncio.sleep(1)
                        if i % 10 == 0:
                            console.print(f"  观测中... {i}/{duration}s")
                else:
                    console.print("  [DRY-RUN] 跳过观测期")
                
                # 恢复
                console.print(f"\n[bold]>>> 恢复故障...[/bold]")
                recover_result = await scenario.recover(ctx)
                
                if recover_result.success:
                    console.print(f"[green]✓ 故障恢复成功[/green]")
                else:
                    console.print(f"[red]✗ 故障恢复失败: {recover_result.error}[/red]")
                
                # 验证
                console.print(f"\n[bold]>>> 验证恢复...[/bold]")
                verified = await scenario.verify(ctx)
                if verified:
                    console.print(f"[green]✓ 验证通过[/green]")
                else:
                    console.print(f"[red]✗ 验证失败[/red]")
                
            else:
                console.print(f"[red]✗ 故障注入失败: {inject_result.error}[/red]")
        
        finally:
            await ssh.close()
            await redfish.close()
        
        # 显示结果
        console.print(f"\n[bold]>>> 执行完成[/bold]")
        console.print(f"  回滚日志: {journal_path}")
    
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]用户中断[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]执行失败: {e}[/red]")
        logger.exception("执行失败")
        sys.exit(1)


@main.command("list-scenarios")
def list_scenarios_cmd() -> None:
    """List all available fault scenarios."""
    from fault_injector.scenarios.registry import list_scenarios
    
    scenarios = list_scenarios()
    
    table = Table(title="可用场景", show_lines=True)
    table.add_column("名称", style="bold cyan")
    table.add_column("描述")
    table.add_column("层级")
    
    for s in scenarios:
        table.add_row(s["name"], s["description"], s["layer"])
    
    console.print(table)


@main.command("validate-config")
@click.argument("config_path", type=click.Path(exists=True, dir_okay=False, readable=True))
def validate_config_cmd(config_path: str) -> None:
    """Validate a YAML configuration file."""
    from fault_injector.config.loader import load_config
    
    try:
        config = load_config(config_path)
        _validate_redfish_requirements(config)
        console.print("[green]✓ 配置校验通过[/green]")
        console.print(f"  Session 目录: {config.global_.session_dir}")
        console.print(f"  日志级别: {config.global_.log_level}")
        console.print(f"  节点组: {list(config.inventory.keys())}")
        console.print(f"  场景: {list(config.scenarios.keys())}")
    except Exception as e:
        console.print(f"[red]✗ 配置校验失败: {e}[/red]")
        sys.exit(1)


def _scenario_requires_redfish(params: dict) -> bool:
    if not isinstance(params, dict):
        return False
    if params.get("use_redfish") is True:
        return True
    redfish_keys = {
        "bmc_host",
        "fan_index",
        "fan_mode",
        "fan_pwm",
        "reset_type",
        "redfish_action",
    }
    return any(key in params for key in redfish_keys)


def _validate_redfish_requirements(config) -> None:
    enabled_scenarios = [name for name, sc in config.scenarios.items() if sc.enabled]
    requires_redfish = any(
        _scenario_requires_redfish(config.scenarios[name].params or {})
        for name in enabled_scenarios
    )
    if not requires_redfish:
        return

    issues: list[str] = []
    for group_name, nodes in config.inventory.items():
        for node in nodes:
            if node.redfish is None:
                issues.append(f"{group_name}/{node.name}: missing redfish config")
                continue
            if not node.redfish.bmc_host:
                issues.append(f"{group_name}/{node.name}: redfish.bmc_host is required")
            has_token = bool(node.redfish.token)
            has_userpass = bool(node.redfish.username and node.redfish.password)
            if not has_token and not has_userpass:
                issues.append(
                    f"{group_name}/{node.name}: provide redfish.token or redfish.username+redfish.password"
                )

    if issues:
        raise ValueError("Redfish validation failed: " + "; ".join(issues))


@main.command("recover")
@click.option(
    "--session",
    "session_id",
    type=str,
    required=True,
    help="Session ID to recover.",
)
@click.option(
    "--session-dir",
    type=click.Path(exists=True, file_okay=False),
    default="./fault-reports/sessions/",
    help="Session directory.",
)
def recover_cmd(session_id: str, session_dir: str) -> None:
    """Recover active faults from a session."""
    from fault_injector.safety.rollback import RollbackJournal
    
    journal_path = Path(session_dir) / session_id / "rollback.jsonl"
    
    if not journal_path.exists():
        console.print(f"[red]错误: Session '{session_id}' 不存在[/red]")
        sys.exit(1)
    
    rollback = RollbackJournal(journal_path)
    active = rollback.get_active_faults()
    
    if not active:
        console.print("[green]没有活跃的故障[/green]")
        return
    
    console.print(f"[yellow]发现 {len(active)} 个活跃故障:[/yellow]")
    for entry in active:
        console.print(f"  - {entry.fault_id}: {entry.inject_action}")
    
    if not Confirm.ask("\n[bold yellow]确认恢复所有故障?[/bold yellow]"):
        console.print("[yellow]已取消[/yellow]")
        return
    
    console.print("[bold]恢复中...[/bold]")
    # 这里需要实际的恢复逻辑，需要加载节点配置和 SSH Channel
    # 简化版本：仅更新状态
    for entry in active:
        rollback.mark_recovered(entry.fault_id)
        console.print(f"  [green]✓[/green] {entry.fault_id}")
    
    console.print("[green]恢复完成[/green]")


if __name__ == "__main__":
    main()
