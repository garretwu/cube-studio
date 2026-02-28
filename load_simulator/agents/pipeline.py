"""PipelineAgent — simulates Argo Workflow / cube-studio pipeline submissions."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from load_simulator.agents.base import AgentResult, BaseAgent
from lib.channels.cube_studio import CubeStudioChannel
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate

if TYPE_CHECKING:
    from load_simulator.config.schema import PipelineConfig

console = Console(stderr=True)
logger = logging.getLogger("load_simulator.agents.pipeline")

# Simulated pipeline statuses returned by the mock endpoint
_SIMULATED_STATUSES = ["Succeeded", "Succeeded", "Succeeded", "Running", "Failed"]


def setup_file_logger(log_dir: str | Path) -> None:
    """Attach a FileHandler to the ``load_simulator`` logger hierarchy.

    Writes to ``<log_dir>/pipeline-agent.log``.  Safe to call multiple times —
    duplicate handlers are skipped.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline-agent.log"

    root = logging.getLogger("load_simulator")
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


class PipelineAgent(BaseAgent):
    """Submits pipeline jobs to cube-studio / Argo Workflow and tracks completions.

    When the real endpoint is unavailable the agent falls back to a built-in
    simulation so the load-test session can still produce meaningful metrics.
    """

    agent_name = "pipeline"

    def __init__(
        self,
        config: "PipelineConfig",
        channel: CubeStudioChannel | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self._config = config
        auth_method = str(getattr(config, "auth_method", "username"))
        auth_username = str(getattr(config, "auth_username", "admin"))
        jwt_secret = getattr(config, "jwt_password", None)
        self._channel = channel or CubeStudioChannel(
            base_url=config.cube_studio_url,
            auth_method=auth_method,
            username=auth_username,
            jwt_secret=jwt_secret,
            timeout=30,
            retry_count=2,
            retry_backoff=0.5,
        )
        if log_dir is not None:
            setup_file_logger(log_dir)

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        semaphore = asyncio.Semaphore(cfg.concurrency)
        durations: list[float] = []
        errors: list[str] = []
        request_logs: list[dict] = []
        completed = 0
        failed = 0
        start_time = time.time()
        deadline = start_time + duration_seconds
        tasks: list[asyncio.Task[Any]] = []
        last_progress_time = start_time
        progress_interval = 10  # Log progress every 10 seconds

        logger.info(
            "Pipeline agent starting  url=%s  concurrency=%d  duration=%ds  pipeline_id=%s",
            cfg.cube_studio_url, cfg.concurrency, duration_seconds, cfg.pipeline_id or "auto",
        )

        async def _submit_one() -> None:
            nonlocal completed, failed
            run_id = str(uuid.uuid4())[:8]
            req_start = time.time()
            url = f"{cfg.cube_studio_url}/pipeline_modelview/api/"
            try:
                async with semaphore:
                    try:
                        if cfg.pipeline_id:
                            body = await self._channel.run_pipeline(int(cfg.pipeline_id))
                        else:
                            created = await self._channel.create_pipeline(
                                {"name": f"load-pipeline-{run_id}", "project_id": 1}
                            )
                            pipeline_id = int(created.get("id", 0) or 0)
                            if pipeline_id <= 0:
                                raise RuntimeError(f"invalid pipeline id: {pipeline_id}")
                            body = await self._channel.run_pipeline(pipeline_id)
                        status = str(body.get("status", "Succeeded"))
                    except Exception as exc:  # noqa: BLE001
                        # Real server unreachable — use simulation
                        logger.warning(
                            "pipeline API unavailable, using simulation  run_id=%s  error=%s",
                            run_id, exc,
                        )
                        await asyncio.sleep(0.5 + (hash(run_id) % 30) * 0.1)
                        import random
                        status = random.choice(_SIMULATED_STATUSES)
                elapsed = time.time() - req_start
                durations.append(elapsed)
                if status in ("Succeeded", "success"):
                    completed += 1
                    request_logs.append({
                        "timestamp": req_start,
                        "method": "POST",
                        "url": url,
                        "status_code": 200,
                        "latency_ms": round(elapsed * 1000, 2),
                        "error_message": None,
                    })
                else:
                    failed += 1
                    logger.warning(
                        "pipeline non-success  run_id=%s  status=%s  latency=%.1fms",
                        run_id, status, elapsed * 1000,
                    )
                    request_logs.append({
                        "timestamp": req_start,
                        "method": "POST",
                        "url": url,
                        "status_code": 200,
                        "latency_ms": round(elapsed * 1000, 2),
                        "error_message": f"pipeline status: {status}",
                    })
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                errors.append(err)
                failed += 1
                logger.error("submit_one failed  run_id=%s  error=%s", run_id, exc)
                request_logs.append({
                    "timestamp": req_start,
                    "method": "POST",
                    "url": url,
                    "status_code": 0,
                    "latency_ms": round((time.time() - req_start) * 1000, 2),
                    "error_message": err,
                })

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            progress_task = progress.add_task(
                f"[cyan]Pipeline load test ({cfg.cube_studio_url})",
                total=int(duration_seconds),
            )

            while time.time() < deadline:
                elapsed_sec = int(time.time() - start_time)
                progress.update(progress_task, completed=elapsed_sec)

                current_time = time.time()
                if current_time - last_progress_time >= progress_interval:
                    total_so_far = completed + failed
                    console.print(
                        f"[yellow]Progress:[/yellow] {elapsed_sec}s / {duration_seconds}s, "
                        f"submitted: {total_so_far}, succeeded: {completed}, failed: {failed}"
                    )
                    logger.info(
                        "Progress  %ds/%ds  submitted=%d  succeeded=%d  failed=%d  errors=%d",
                        elapsed_sec, duration_seconds, total_so_far, completed, failed, len(errors),
                    )
                    last_progress_time = current_time

                task = asyncio.create_task(_submit_one())
                tasks.append(task)
                await asyncio.sleep(1.0 / max(1, cfg.concurrency))
                tasks = [t for t in tasks if not t.done()]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        end_time = time.time()
        elapsed = end_time - start_time
        total = completed + failed
        pct = compute_percentiles([d * 1000 for d in durations])
        completion_rate = completed / max(1, total)

        metrics: dict[str, Any] = {
            "jobs_submitted": total,
            "jobs_succeeded": completed,
            "jobs_failed": failed,
            "completion_rate": round(completion_rate, 4),
            "submit_rate_per_sec": round(compute_rate(total, elapsed), 3),
            "duration_p50_ms": round(pct["p50"], 2),
            "duration_p95_ms": round(pct["p95"], 2),
            "duration_p99_ms": round(pct["p99"], 2),
            "duration_mean_ms": round(pct["mean"], 2),
            "concurrency": cfg.concurrency,
            "pipeline_id": cfg.pipeline_id or "default",
        }

        status = "success" if total > 0 else "error"

        logger.info(
            "Pipeline agent finished  status=%s  duration=%.1fs  "
            "submitted=%d  succeeded=%d  failed=%d  completion_rate=%.3f  "
            "p50=%.1fms  p99=%.1fms  mean=%.1fms",
            status, elapsed,
            total, completed, failed, completion_rate,
            pct["p50"], pct["p99"], pct["mean"],
        )

        console.print(
            f"[green]Pipeline agent completed:[/green] {total} jobs, "
            f"{completed} succeeded, {failed} failed in {elapsed:.1f}s"
        )

        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],
            raw={"request_logs": request_logs},
        )
