"""
Click CLI for the AIDC Auto-SRE Load Simulator.

Commands:
  run              Execute a load test session.
  validate-config  Validate a YAML config file.
  list-scenarios   Show available agent scenarios.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from load_simulator.config.loader import load_config
from load_simulator.config.schema import LoadSimulatorConfig
from load_simulator.orchestrator.engine import LoadOrchestrator

console = Console(stderr=True)

AVAILABLE_SCENARIOS = ["inference", "pipeline", "finetune", "notebook"]


@click.group()
@click.version_option(package_name="load-simulator", prog_name="load-simulator")
def main() -> None:
    """AIDC Auto-SRE Load Simulator — stress-test AI datacenter services."""


@main.command("run")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    default=None,
    help="Path to YAML configuration file. Uses built-in defaults when omitted.",
)
@click.option(
    "--output-format",
    type=click.Choice(["json", "html", "none"], case_sensitive=False),
    default="none",
    show_default=True,
    help="Format for the final report written to stdout.",
)
@click.option(
    "--only",
    "only",
    multiple=True,
    type=click.Choice(AVAILABLE_SCENARIOS, case_sensitive=False),
    help="Run only the listed scenario(s). May be repeated. Default: run all agents in config.",
)
@click.option(
    "--duration",
    "duration",
    type=int,
    default=None,
    help="Override duration_seconds for every agent.",
)
@click.option(
    "--concurrency",
    "concurrency",
    type=int,
    default=None,
    help="Override concurrency for agents that support it.",
)
def run_cmd(
    config_path: Optional[str],
    output_format: str,
    only: tuple[str, ...],
    duration: Optional[int],
    concurrency: Optional[int],
) -> None:
    """Execute a load test session against AI datacenter services."""
    # Load config
    if config_path:
        console.log(f"Loading config from [cyan]{config_path}[/cyan]")
        cfg = load_config(config_path)
    else:
        console.log("No config supplied — using built-in defaults.")
        cfg = LoadSimulatorConfig()

    # Apply CLI overrides
    if duration is not None:
        cfg.inference.duration_seconds = duration
        cfg.pipeline.duration_seconds = duration
        cfg.finetune.duration_seconds = duration
        cfg.notebook.duration_seconds = duration

    if concurrency is not None:
        cfg.inference.concurrency = concurrency
        cfg.pipeline.concurrency = concurrency

    only_list: list[str] = list(only) if only else []

    console.rule("[bold blue]Load Simulator — Starting Session")
    t0 = time.monotonic()

    orchestrator = LoadOrchestrator(cfg)
    try:
        session_result = asyncio.run(orchestrator.run(only=only_list))
    except KeyboardInterrupt:
        console.log("[yellow]Interrupted by user — partial results may be available.")
        sys.exit(130)

    elapsed = time.monotonic() - t0
    console.log(f"Session finished in [green]{elapsed:.1f}s[/green].")

    # Output
    if output_format == "json":
        _output_json(session_result)
    elif output_format == "html":
        _output_html(session_result, cfg)
    else:
        _output_rich_summary(session_result)


def _output_json(session_result) -> None:
    """Print JSON summary to stdout."""
    scenarios = []
    for ar in session_result.agent_results:
        scenarios.append(
            {
                "name": ar.name,
                "status": ar.status,
                "metrics": ar.metrics,
                "errors": ar.errors,
                "duration_seconds": (
                    (ar.end_time - ar.start_time) if ar.end_time and ar.start_time else None
                ),
            }
        )

    payload = {
        "exit_code": 0,
        "summary": {
            "session_id": session_result.session_id,
            "duration_seconds": session_result.duration_seconds,
            "scenarios": scenarios,
            "bottlenecks": session_result.bottlenecks,
        },
    }
    print(json.dumps(payload, indent=2, default=str))


def _output_html(session_result, cfg: LoadSimulatorConfig) -> None:
    """Generate and write HTML report, then print path to stdout."""
    from load_simulator.reporting.html_report import generate_html_report

    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"report_{session_result.session_id}.html"
    html_content = generate_html_report(session_result)
    report_path.write_text(html_content, encoding="utf-8")
    console.log(f"HTML report written to [cyan]{report_path}[/cyan]")
    print(str(report_path))


def _output_rich_summary(session_result) -> None:
    """Print a human-friendly Rich table to stderr."""
    table = Table(title=f"Session {session_result.session_id}", show_lines=True)
    table.add_column("Agent", style="bold cyan")
    table.add_column("Status", style="bold")
    table.add_column("Key Metrics")
    table.add_column("Errors")

    for ar in session_result.agent_results:
        status_style = "green" if ar.status == "success" else "red"
        metrics_str = "\n".join(f"{k}: {v}" for k, v in (ar.metrics or {}).items())
        errors_str = "; ".join(ar.errors[:3]) if ar.errors else "—"
        table.add_row(
            ar.name,
            f"[{status_style}]{ar.status}[/{status_style}]",
            metrics_str or "—",
            errors_str,
        )

    console.print(table)

    if session_result.bottlenecks:
        console.rule("[bold red]Bottleneck Findings")
        for b in session_result.bottlenecks:
            console.print(
                f"  [{b['severity'].upper()}] layer={b['layer']} metric={b['metric']} "
                f"value={b['value']}{b['unit']} threshold={b['threshold']}{b['unit']}"
            )
    else:
        console.print("[green]No bottlenecks detected.[/green]")


# ---------------------------------------------------------------------------
# validate-config
# ---------------------------------------------------------------------------

@main.command("validate-config")
@click.argument("config_path", type=click.Path(exists=True, dir_okay=False, readable=True))
def validate_config_cmd(config_path: str) -> None:
    """Validate a YAML configuration file and report any errors."""
    try:
        cfg = load_config(config_path)
        console.print(f"[green]Config is valid.[/green]")
        console.print(f"  Agents   : {cfg.agents}")
        console.print(f"  Session  : {cfg.session_id or '(auto)'}")
        console.print(f"  Bottleneck analysis: {cfg.bottleneck_analysis}")
    except Exception as exc:
        console.print(f"[red]Config validation failed:[/red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# list-scenarios
# ---------------------------------------------------------------------------

@main.command("list-scenarios")
def list_scenarios_cmd() -> None:
    """List all available agent scenarios and their descriptions."""
    table = Table(title="Available Scenarios", show_lines=True)
    table.add_column("Name", style="bold cyan")
    table.add_column("Description")
    table.add_column("Config Section")

    rows = [
        ("inference", "Stress-tests vLLM /v1/chat/completions with concurrent async requests.", "inference"),
        ("pipeline", "Simulates Argo Workflow pipeline submissions to cube-studio.", "pipeline"),
        ("finetune", "Simulates LLaMA-Factory fine-tuning API load.", "finetune"),
        ("notebook", "Simulates Jupyter Kernel API execution requests.", "notebook"),
    ]
    for name, desc, section in rows:
        table.add_row(name, desc, section)

    console.print(table)
