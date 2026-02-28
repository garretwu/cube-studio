"""InferenceAgent — stress-tests a vLLM-compatible /v1/chat/completions endpoint."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.channels.inference import InferenceChannel
from load_simulator.load.prompt_pool import PromptPool
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate

if TYPE_CHECKING:
    from load_simulator.config.schema import InferenceConfig

console = Console(stderr=True)
logger = logging.getLogger("load_simulator.agents.inference")


def setup_file_logger(log_dir: str | Path) -> None:
    """Attach a FileHandler to the ``load_simulator`` logger hierarchy.

    Writes to ``<log_dir>/inference-agent.log``.  Safe to call multiple times —
    duplicate handlers are skipped.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "inference-agent.log"

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


class InferenceAgent(BaseAgent):
    """Sends concurrent chat-completion requests to a vLLM endpoint.

    Tracks per-request latency (p50/p95/p99), throughput (req/s), token
    throughput (tokens/s), and error rate.
    """

    agent_name = "inference"

    def __init__(
        self,
        config: "InferenceConfig",
        channel: InferenceChannel | None = None,
        log_dir: str | Path | None = None,
    ) -> None:
        self._config = config
        self._prompt_pool = PromptPool(size=config.prompt_pool_size)
        self._channel = channel or InferenceChannel(
            endpoint=config.endpoint,
            model=config.model,
            timeout=60,
        )
        if log_dir is not None:
            setup_file_logger(log_dir)

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        semaphore = asyncio.Semaphore(cfg.concurrency)
        latencies: list[float] = []
        ttft_values: list[float] = []
        token_counts: list[int] = []
        errors: list[str] = []
        request_logs: list[dict] = []
        completed = 0
        start_time = time.time()
        deadline = start_time + duration_seconds
        tasks: list[asyncio.Task[Any]] = []
        last_progress_time = start_time
        progress_interval = 10  # Log progress every 10 seconds

        logger.info(
            "Inference agent starting  endpoint=%s  model=%s  concurrency=%d  duration=%ds  max_tokens=%d",
            cfg.endpoint, cfg.model, cfg.concurrency, duration_seconds, cfg.max_tokens,
        )

        async def _one_request() -> None:
            nonlocal completed
            prompt = self._prompt_pool.sample()
            req_start = time.time()
            try:
                async with semaphore:
                    result = await self._channel.chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=cfg.max_tokens,
                        stream=getattr(cfg, "stream", False),
                    )
                if not result.ok:
                    err = result.error or "inference request failed"
                    errors.append(err)
                    logger.warning(
                        "request failed  status=%s  error=%s",
                        getattr(result, "status_code", "?"), err,
                    )
                    request_logs.append({
                        "timestamp": req_start,
                        "method": "POST",
                        "url": cfg.endpoint,
                        "status_code": getattr(result, "status_code", 500),
                        "latency_ms": round((time.time() - req_start) * 1000, 2),
                        "error_message": err,
                    })
                    return
                elapsed = time.time() - req_start
                latencies.append(elapsed)
                if result.ttft_seconds is not None:
                    ttft_values.append(result.ttft_seconds)
                usage = result.body.get("usage") if isinstance(result.body, dict) else {}
                usage = usage or {}
                tokens = usage.get("completion_tokens") or usage.get("total_tokens") or cfg.max_tokens
                token_counts.append(int(tokens))
                completed += 1
                input_tokens = usage.get("prompt_tokens")
                output_tokens = usage.get("completion_tokens")
                tps = int(tokens) / elapsed if elapsed > 0 else 0
                request_logs.append({
                    "timestamp": req_start,
                    "method": "POST",
                    "url": cfg.endpoint,
                    "status_code": getattr(result, "status_code", 200),
                    "latency_ms": round(elapsed * 1000, 2),
                    "error_message": None,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "time_to_first_token_ms": round(result.ttft_seconds * 1000, 2) if result.ttft_seconds is not None else None,
                    "tokens_per_second": round(tps, 2),
                })
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                errors.append(err)
                logger.error("request exception  endpoint=%s  error=%s", cfg.endpoint, exc)
                request_logs.append({
                    "timestamp": req_start,
                    "method": "POST",
                    "url": cfg.endpoint,
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
                f"[cyan]Inference load test ({cfg.endpoint})",
                total=int(duration_seconds),
            )

            # Continuously fire requests until deadline
            while time.time() < deadline:
                elapsed_sec = int(time.time() - start_time)
                progress.update(progress_task, completed=elapsed_sec)

                current_time = time.time()
                if current_time - last_progress_time >= progress_interval:
                    console.print(
                        f"[yellow]Progress:[/yellow] {elapsed_sec}s / {duration_seconds}s, "
                        f"completed: {completed}, errors: {len(errors)}, in-flight: {len(tasks)}"
                    )
                    logger.info(
                        "Progress  %ds/%ds  completed=%d  errors=%d  in_flight=%d",
                        elapsed_sec, duration_seconds, completed, len(errors), len(tasks),
                    )
                    last_progress_time = current_time

                t = asyncio.create_task(_one_request())
                tasks.append(t)
                # Stagger slightly to avoid a thundering herd at t=0
                await asyncio.sleep(0.01)
                # Prune completed tasks to avoid unbounded list growth
                tasks = [t for t in tasks if not t.done()]

        # Wait for all in-flight requests to finish
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        end_time = time.time()
        elapsed = end_time - start_time

        pct = compute_percentiles([lat * 1000 for lat in latencies])  # → ms
        req_rate = compute_rate(completed, elapsed)
        total_tokens = sum(token_counts)
        token_rate = compute_rate(total_tokens, elapsed)
        error_rate = len(errors) / max(1, completed + len(errors))

        metrics: dict[str, Any] = {
            "requests_completed": completed,
            "requests_failed": len(errors),
            "error_rate": round(error_rate, 4),
            "req_per_sec": round(req_rate, 2),
            "tokens_per_sec": round(token_rate, 2),
            "latency_p50_ms": round(pct["p50"], 2),
            "latency_p95_ms": round(pct["p95"], 2),
            "latency_p99_ms": round(pct["p99"], 2),
            "latency_min_ms": round(pct["min"], 2),
            "latency_max_ms": round(pct["max"], 2),
            "latency_mean_ms": round(pct["mean"], 2),
            "total_tokens": total_tokens,
            "concurrency": cfg.concurrency,
            "endpoint": cfg.endpoint,
            "model": cfg.model,
        }

        if ttft_values:
            ttft_pct = compute_percentiles([t * 1000 for t in ttft_values])
            metrics["time_to_first_token_ms"] = round(ttft_pct["mean"], 2)
            metrics["ttft_p50_ms"] = round(ttft_pct["p50"], 2)
            metrics["ttft_p95_ms"] = round(ttft_pct["p95"], 2)
            metrics["ttft_p99_ms"] = round(ttft_pct["p99"], 2)

        status = "success" if completed > 0 else "error"

        logger.info(
            "Inference agent finished  status=%s  duration=%.1fs  "
            "completed=%d  failed=%d  error_rate=%.3f  "
            "req/s=%.2f  tokens/s=%.2f  "
            "p50=%.1fms  p95=%.1fms  p99=%.1fms  mean=%.1fms",
            status, elapsed,
            completed, len(errors), error_rate,
            req_rate, token_rate,
            pct["p50"], pct["p95"], pct["p99"], pct["mean"],
        )

        console.print(
            f"[green]Inference agent completed:[/green] {completed} requests, "
            f"{len(errors)} errors in {elapsed:.1f}s  "
            f"(p99={pct['p99']:.0f}ms, {req_rate:.1f} req/s)"
        )

        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],  # cap stored errors
            raw={"request_logs": request_logs},
        )
