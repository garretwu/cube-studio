"""NotebookAgent — simulates Jupyter Kernel API execution requests."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.channels.notebook import NotebookChannel
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate

if TYPE_CHECKING:
    from load_simulator.config.schema import NotebookConfig

console = Console(stderr=True)
logger = logging.getLogger("load_simulator.agents.notebook")

# Code snippets to execute against the kernel
_SAMPLE_CELLS = [
    "import math; [math.sqrt(i) for i in range(1000)]",
    "x = list(range(10000)); sum(x)",
    "import os; os.cpu_count()",
    "','.join(str(i) for i in range(500))",
    "result = {i: i**2 for i in range(200)}; len(result)",
    "import sys; sys.version",
    "print('hello from load simulator')",
    "2 ** 32",
]


def setup_file_logger(log_dir: str | Path) -> None:
    """Attach a FileHandler to the ``load_simulator`` logger hierarchy.

    Writes to ``<log_dir>/notebook-agent.log``.  Safe to call multiple times —
    duplicate handlers are skipped.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "notebook-agent.log"

    root = logging.getLogger("load_simulator")
    # Avoid adding duplicate handlers on repeated calls
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_file.resolve():
            return

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)
    root.setLevel(logging.DEBUG)
    logger.info("Log file initialised: %s", log_file)


class NotebookAgent(BaseAgent):
    """Simulates Jupyter Kernel API requests.

    Workflow per iteration:
      1. POST /api/kernels                   → create a kernel
      2. WS   /api/kernels/{id}/channels     → execute code via WebSocket
      3. DELETE /api/kernels/{id}             → clean up

    Tracks kernel-creation latency, execution latency, and throughput.
    """

    agent_name = "notebook"

    def __init__(
        self,
        config: "NotebookConfig",
        channel: NotebookChannel | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self._config = config
        self._channel = channel or NotebookChannel(
            base_url=config.jupyter_url,
            token=config.token,
            username=config.username,
            timeout=30,
        )
        if log_dir is not None:
            setup_file_logger(log_dir)

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        errors: list[str] = []
        create_latencies: list[float] = []
        exec_latencies: list[float] = []
        request_logs: list[dict] = []
        kernels_created = 0
        executions_done = 0
        start_time = time.time()
        deadline = start_time + duration_seconds
        cell_index = 0
        last_progress_time = start_time
        progress_interval = 10  # Log progress every 10 seconds

        logger.info(
            "Notebook agent starting  url=%s  duration=%ds",
            cfg.jupyter_url, duration_seconds,
        )

        # Progress tracking
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]Notebook load test ({cfg.jupyter_url})",
                total=int(duration_seconds)
            )

            while time.time() < deadline:
                elapsed = int(time.time() - start_time)
                progress.update(task, completed=elapsed)

                # Log progress periodically to prevent perceived freeze
                current_time = time.time()
                if current_time - last_progress_time >= progress_interval:
                    elapsed_sec = int(current_time - start_time)
                    console.print(f"[yellow]Progress:[/yellow] {elapsed_sec}s / {duration_seconds}s, "
                                  f"kernels: {kernels_created}, executions: {executions_done}")
                    logger.info(
                        "Progress  %ds/%ds  kernels=%d  executions=%d  errors=%d",
                        elapsed_sec, duration_seconds, kernels_created, executions_done, len(errors),
                    )
                    last_progress_time = current_time

                # ── Step 1: create kernel ────────────────────────────────
                t0 = time.time()
                kernel_id: str | None = None
                try:
                    created = await self._channel.create_kernel("python3")
                    kernel_id = str(created.get("id") or uuid.uuid4())
                    lat = time.time() - t0
                    create_latencies.append(lat)
                    kernels_created += 1
                    request_logs.append({
                        "timestamp": t0,
                        "method": "POST",
                        "url": f"{cfg.jupyter_url}/api/kernels",
                        "status_code": 201,
                        "latency_ms": round(lat * 1000, 2),
                        "error_message": None,
                    })
                except Exception as exc:  # noqa: BLE001
                    lat = time.time() - t0
                    create_latencies.append(lat)
                    errors.append(f"Create kernel error: {exc}")
                    logger.error("create_kernel failed  latency=%.1fms  error=%s", lat * 1000, exc)
                    request_logs.append({
                        "timestamp": t0,
                        "method": "POST",
                        "url": f"{cfg.jupyter_url}/api/kernels",
                        "status_code": 500,
                        "latency_ms": round(lat * 1000, 2),
                        "error_message": str(exc),
                    })

                # ── Step 2: execute a cell ───────────────────────────────
                cell_code = _SAMPLE_CELLS[cell_index % len(_SAMPLE_CELLS)]
                cell_index += 1
                t1 = time.time()
                try:
                    if kernel_id is None:
                        raise RuntimeError("no kernel available")
                    _ = await self._channel.execute_code(kernel_id, cell_code)
                    lat = time.time() - t1
                    exec_latencies.append(lat)
                    executions_done += 1
                    request_logs.append({
                        "timestamp": t1,
                        "method": "POST",
                        "url": f"{cfg.jupyter_url}/api/kernels/{kernel_id}/channels",
                        "status_code": 200,
                        "latency_ms": round(lat * 1000, 2),
                        "error_message": None,
                    })
                except Exception as exc:  # noqa: BLE001
                    lat = time.time() - t1
                    exec_latencies.append(lat)
                    errors.append(f"Execute error: {exc}")
                    logger.error("execute_code failed  kernel=%s  latency=%.1fms  error=%s", kernel_id, lat * 1000, exc)
                    request_logs.append({
                        "timestamp": t1,
                        "method": "POST",
                        "url": f"{cfg.jupyter_url}/api/kernels/{kernel_id}/channels",
                        "status_code": 500,
                        "latency_ms": round(lat * 1000, 2),
                        "error_message": str(exc),
                    })

                # ── Step 3: delete kernel ────────────────────────────────
                try:
                    if kernel_id is not None:
                        await self._channel.delete_kernel(kernel_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("delete_kernel failed  kernel=%s  error=%s", kernel_id, exc)

                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.5, remaining))

        await self._channel.close()

        end_time = time.time()
        elapsed = end_time - start_time

        create_pct = compute_percentiles([lat * 1000 for lat in create_latencies])
        exec_pct = compute_percentiles([lat * 1000 for lat in exec_latencies])

        metrics: dict[str, Any] = {
            "kernels_created": kernels_created,
            "executions_done": executions_done,
            "exec_rate_per_sec": round(compute_rate(executions_done, elapsed), 3),
            "kernel_create_p50_ms": round(create_pct["p50"], 2),
            "kernel_create_p95_ms": round(create_pct["p95"], 2),
            "kernel_create_p99_ms": round(create_pct["p99"], 2),
            "exec_latency_p50_ms": round(exec_pct["p50"], 2),
            "exec_latency_p95_ms": round(exec_pct["p95"], 2),
            "exec_latency_p99_ms": round(exec_pct["p99"], 2),
            "exec_latency_mean_ms": round(exec_pct["mean"], 2),
            "jupyter_url": cfg.jupyter_url,
        }

        status = "success" if kernels_created > 0 else "error"

        logger.info(
            "Notebook agent finished  status=%s  duration=%.1fs  "
            "kernels=%d  executions=%d  errors=%d  "
            "create_p50=%.1fms  create_p99=%.1fms  "
            "exec_p50=%.1fms  exec_p99=%.1fms  exec_mean=%.1fms  "
            "rate=%.3f req/s",
            status, elapsed,
            kernels_created, executions_done, len(errors),
            metrics["kernel_create_p50_ms"], metrics["kernel_create_p99_ms"],
            metrics["exec_latency_p50_ms"], metrics["exec_latency_p99_ms"],
            metrics["exec_latency_mean_ms"],
            metrics["exec_rate_per_sec"],
        )

        console.print(f"[green]Notebook agent completed:[/green] {kernels_created} kernels, "
                      f"{executions_done} executions in {elapsed:.1f}s")
        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],
            raw={"request_logs": request_logs},
        )
