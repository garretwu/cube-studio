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
from load_simulator.orchestrator.preflight import failed_checks, has_blocking_failure
from load_simulator.reporting.bundle import write_report_bundle
from load_simulator.reporting.session_output import build_json_payload

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
@click.option(
    "--strict-preflight",
    is_flag=True,
    default=False,
    help="Exit non-zero when preflight has failed checks.",
)
@click.option(
    "--mode",
    "mode",
    type=click.Choice(["single", "mixed", "stress", "soak"], case_sensitive=False),
    default=None,
    help="Execution mode override.",
)
@click.option(
    "--session-dir",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    default="./reports/sessions",
    show_default=True,
    help="Directory for session checkpoints and resume data.",
)
@click.option(
    "--resume",
    "resume_session_id",
    type=str,
    default=None,
    help="Resume from an existing session id in --session-dir.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and simulate channel calls without executing external write operations.",
)
def run_cmd(
    config_path: Optional[str],
    output_format: str,
    only: tuple[str, ...],
    duration: Optional[int],
    concurrency: Optional[int],
    strict_preflight: bool,
    mode: Optional[str],
    session_dir: str,
    resume_session_id: Optional[str],
    dry_run: bool,
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

    if mode:
        cfg.mode = mode.lower()

    only_list: list[str] = list(only) if only else []

    console.rule("[bold blue]Load Simulator — Starting Session")
    t0 = time.monotonic()

    orchestrator = LoadOrchestrator(
        cfg,
        session_dir=session_dir,
        resume_session_id=resume_session_id,
        dry_run=dry_run,
    )
    try:
        session_result = asyncio.run(orchestrator.run(only=only_list))
    except KeyboardInterrupt:
        console.log("[yellow]Interrupted by user — partial results may be available.")
        sys.exit(130)

    elapsed = time.monotonic() - t0
    console.log(f"Session finished in [green]{elapsed:.1f}s[/green].")
    exit_code = _resolve_exit_code(session_result, strict_preflight)

    # Output
    if output_format == "json":
        _output_json(session_result, exit_code)
    elif output_format == "html":
        _output_html(session_result, cfg)
    else:
        _output_rich_summary(session_result)
    if exit_code != 0:
        console.log(
            f"[red]Strict preflight failed[/red]: "
            + ", ".join(failed_checks(getattr(session_result, "preflight", {})))
        )
        sys.exit(exit_code)


def _output_json(session_result, exit_code: int = 0) -> None:
    """Print JSON summary to stdout."""
    payload = build_json_payload(session_result, exit_code=exit_code)
    print(json.dumps(payload, indent=2, default=str))


def _resolve_exit_code(session_result, strict_preflight: bool) -> int:
    if strict_preflight and has_blocking_failure(getattr(session_result, "preflight", {})):
        return 2
    return 0


def _output_html(session_result, cfg: LoadSimulatorConfig) -> None:
    """Generate report bundle and print main html path."""
    output_dir = Path(cfg.output_dir)
    report_path = write_report_bundle(session_result, output_dir=output_dir)
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

    preflight = getattr(session_result, "preflight", {}) or {}
    if preflight:
        preflight_table = Table(title="Preflight Checks", show_lines=True)
        preflight_table.add_column("Check", style="bold cyan")
        preflight_table.add_column("OK")
        preflight_table.add_column("Detail")
        for name, item in preflight.items():
            ok = item.get("ok")
            if ok is True:
                ok_text = "[green]yes[/green]"
            elif ok is False:
                ok_text = "[red]no[/red]"
            else:
                ok_text = "[yellow]n/a[/yellow]"
            preflight_table.add_row(name, ok_text, str(item.get("detail", "")))
        console.print(preflight_table)

    adaptive_events = getattr(session_result, "adaptive_events", []) or []
    if adaptive_events:
        events_table = Table(title="Adaptive Events", show_lines=True)
        events_table.add_column("Stage", style="bold cyan")
        events_table.add_column("Action")
        events_table.add_column("Reason")
        for ev in adaptive_events:
            events_table.add_row(
                str(ev.get("stage", "")),
                str(ev.get("action", "")),
                str(ev.get("reason", "")),
            )
        console.print(events_table)

    breaking_point = getattr(session_result, "breaking_point", None)
    if breaking_point:
        console.print(
            "[yellow]Breaking point:[/yellow] "
            f"stage={breaking_point.get('stage')} "
            f"scale={breaking_point.get('concurrency_scale')} "
            f"reason={breaking_point.get('reason')}"
        )

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
