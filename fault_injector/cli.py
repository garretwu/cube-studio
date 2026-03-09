"""
Click CLI for fault injector.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from fault_injector.config.defaults import get_default_config
from fault_injector.config.loader import load_config
from fault_injector.orchestrator.engine import FaultOrchestrator
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.scenarios.registry import list_scenarios

console = Console(stderr=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(package_name="fault-injector", prog_name="fault-injector")
def main() -> None:
    """AIDC Auto-SRE Fault Injector."""


@main.command("run")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), default=None)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--no-monitor", is_flag=True, default=False, help="Disable monitoring (Prometheus) for this run.")
@click.option("--scenario", "scenario_name", type=str, default=None)
@click.option("--yes", "-y", is_flag=True, default=False)
def run_cmd(
    config_path: Optional[str],
    dry_run: bool,
    no_monitor: bool,
    scenario_name: Optional[str],
    yes: bool,
) -> None:
    """Execute a fault injection session via orchestrator."""
    if config_path:
        config = load_config(config_path)
    else:
        config = get_default_config()

    if dry_run:
        config.global_.safety.dry_run = True

    if no_monitor:
        config.monitor.enabled = False

    if scenario_name:
        for name, sc in config.scenarios.items():
            sc.enabled = name == scenario_name

    if not config.inventory:
        console.print("[red]No inventory configured[/red]")
        sys.exit(1)

    if not config.global_.safety.dry_run and not yes:
        if not Confirm.ask("Proceed with fault injection execution?"):
            console.print("[yellow]Cancelled[/yellow]")
            sys.exit(0)

    async def _run() -> None:
        orchestrator = FaultOrchestrator(
            config=config,
            dry_run=config.global_.safety.dry_run,
            session_dir=config.global_.session_dir,
        )
        session = await orchestrator.run()
        console.print("[green]Session completed[/green]")
        console.print(f"  session_id: [cyan]{session.session_id}[/cyan]")
        console.print(f"  status: [cyan]{session.status.value}[/cyan]")
        console.print(f"  session_path: [cyan]{session.session_path}[/cyan]")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted by user[/yellow]")
        sys.exit(130)
    except Exception as exc:
        console.print(f"[red]Execution failed: {exc}[/red]")
        logger.exception("fault injector run failed")
        sys.exit(1)


@main.command("list-scenarios")
def list_scenarios_cmd() -> None:
    scenarios = list_scenarios()
    table = Table(title="Available Scenarios", show_lines=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Description")
    table.add_column("Layer")
    for item in scenarios:
        table.add_row(item["name"], item["description"], item["layer"])
    console.print(table)


@main.command("validate-config")
@click.argument("config_path", type=click.Path(exists=True, dir_okay=False))
def validate_config_cmd(config_path: str) -> None:
    try:
        config = load_config(config_path)
        console.print("[green]Config validation passed[/green]")
        console.print(f"  session_dir: {config.global_.session_dir}")
        console.print(f"  log_level: {config.global_.log_level}")
        console.print(f"  inventory_groups: {list(config.inventory.keys())}")
        console.print(f"  switches: {list(config.switches.keys())}")
        console.print(f"  scenarios: {list(config.scenarios.keys())}")
    except Exception as exc:
        console.print(f"[red]Config validation failed: {exc}[/red]")
        sys.exit(1)


@main.command("recover")
@click.option("--session", "session_id", type=str, required=True)
@click.option("--session-dir", type=click.Path(exists=True, file_okay=False), default="./fault_injector/fault_reports/sessions/")
def recover_cmd(session_id: str, session_dir: str) -> None:
    """Recover active faults from a session rollback journal."""
    journal_path = Path(session_dir) / session_id / "rollback.jsonl"
    if not journal_path.exists():
        console.print(f"[red]Session journal not found: {journal_path}[/red]")
        sys.exit(1)

    rollback = RollbackJournal(journal_path)
    active = rollback.get_active_faults()
    if not active:
        console.print("[green]No active faults found[/green]")
        return

    console.print(f"[yellow]Found {len(active)} active faults[/yellow]")
    for entry in active:
        console.print(f"  - {entry.fault_id}: {entry.inject_action}")

    if not Confirm.ask("Recover all active faults?"):
        console.print("[yellow]Cancelled[/yellow]")
        return

    async def _recover() -> None:
        results = await rollback.recover_all()
        success = sum(1 for r in results if r.success)
        failed = len(results) - success
        console.print(f"[green]Recovery complete[/green] success={success} failed={failed}")

    asyncio.run(_recover())


@main.command("resume")
@click.option("--session", "session_id", type=str, required=True)
@click.option("--session-dir", type=click.Path(exists=True, file_okay=False), default="./fault_injector/fault_reports/sessions/")
def resume_cmd(session_id: str, session_dir: str) -> None:
    """Recover-only resume for interrupted sessions."""

    async def _resume() -> None:
        session = await FaultOrchestrator.resume(session_id=session_id, session_dir=session_dir)
        console.print("[green]Resume recovery complete[/green]")
        console.print(f"  session_id: [cyan]{session.session_id}[/cyan]")
        console.print(f"  status: [cyan]{session.status.value}[/cyan]")

    try:
        asyncio.run(_resume())
    except Exception as exc:
        console.print(f"[red]Resume failed: {exc}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
