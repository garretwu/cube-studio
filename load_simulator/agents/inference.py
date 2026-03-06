"""InferenceAgent — stress-tests a vLLM-compatible /v1/chat/completions endpoint."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.channels.inference import InferenceChannel
from load_simulator.load.prompt_pool import PromptPool
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate

if TYPE_CHECKING:
    from load_simulator.config.schema import InferenceConfig

console = Console(stderr=True)
logger = logging.getLogger("load_simulator.agents.inference")


class InferenceAgent(BaseAgent):
    """Sends concurrent chat-completion requests to a vLLM endpoint.

    Tracks per-request latency (p50/p95/p99), throughput (req/s), token
    throughput (tokens/s), and error rate.
    """

    agent_name = "inference"

    def __init__(self, config: "InferenceConfig", channel: InferenceChannel | None = None) -> None:
        self._config = config
        self._prompt_pool = PromptPool(size=config.prompt_pool_size)
        self._channel = channel or InferenceChannel(
            endpoint=config.endpoint,
            model=config.model,
            api_key=config.api_key,
            timeout=60,
        )

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
            "Inference agent starting  name=%s  endpoint=%s  model=%s  concurrency=%d  duration=%ds",
            self.agent_name, self._channel.endpoint, self._channel.model,
            cfg.concurrency, duration_seconds,
        )

        async def _one_request() -> None:
            nonlocal completed
            prompt = self._prompt_pool.sample()
            req_start = time.time()
            try:
                try:
                    result = await self._channel.chat_completion(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=cfg.max_tokens,
                        stream=getattr(cfg, "stream", False),
                    )
                finally:
                    semaphore.release()
                if not result.ok:
                    errors.append(result.error or "inference request failed")
                    request_logs.append({
                        "timestamp": req_start,
                        "method": "POST",
                        "url": self._channel.endpoint,
                        "status_code": getattr(result, "status_code", 500),
                        "latency_ms": round((time.time() - req_start) * 1000, 2),
                        "error_message": result.error or "inference request failed",
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
                    "url": self._channel.endpoint,
                    "status_code": getattr(result, "status_code", 200),
                    "latency_ms": round(elapsed * 1000, 2),
                    "error_message": None,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "time_to_first_token_ms": round(result.ttft_seconds * 1000, 2) if result.ttft_seconds is not None else None,
                    "tokens_per_second": round(tps, 2),
                })
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
                request_logs.append({
                    "timestamp": req_start,
                    "method": "POST",
                    "url": self._channel.endpoint,
                    "status_code": 0,
                    "latency_ms": round((time.time() - req_start) * 1000, 2),
                    "error_message": f"{type(exc).__name__}: {exc}",
                })

        # Progress tracking
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            progress_task = progress.add_task(
                f"[cyan]{self.agent_name} ({self._channel.endpoint})",
                total=int(duration_seconds),
            )

            # Continuously fire requests until deadline, respecting concurrency
            while time.time() < deadline:
                elapsed_so_far = int(time.time() - start_time)
                progress.update(progress_task, completed=elapsed_so_far)

                # Periodic console + logger output
                current_time = time.time()
                if current_time - last_progress_time >= progress_interval:
                    elapsed_sec = int(current_time - start_time)
                    total_tok = sum(token_counts)
                    tok_rate = total_tok / elapsed_sec if elapsed_sec > 0 else 0
                    console.print(
                        f"[yellow]Progress:[/yellow] {self.agent_name}  "
                        f"{elapsed_sec}s / {duration_seconds}s, "
                        f"reqs: {completed}, errors: {len(errors)}, "
                        f"tokens/s: {tok_rate:.1f}"
                    )
                    logger.info(
                        "Progress  %s  %ds/%ds  completed=%d  errors=%d  tokens/s=%.1f",
                        self.agent_name, elapsed_sec, duration_seconds,
                        completed, len(errors), tok_rate,
                    )
                    last_progress_time = current_time

                await semaphore.acquire()
                if time.time() >= deadline:
                    semaphore.release()
                    break
                task = asyncio.create_task(_one_request())
                tasks.append(task)
                # Prune completed tasks periodically
                if len(tasks) > cfg.concurrency * 2:
                    tasks = [t for t in tasks if not t.done()]

            progress.update(progress_task, completed=int(duration_seconds))

        # Wait for in-flight requests (at most concurrency tasks)
        tasks = [t for t in tasks if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        end_time = time.time()
        elapsed = end_time - start_time

        pct = compute_percentiles([l * 1000 for l in latencies])  # → ms
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
            "endpoint": self._channel.endpoint,
            "model": self._channel.model,
        }

        if ttft_values:
            ttft_pct = compute_percentiles([t * 1000 for t in ttft_values])
            metrics["time_to_first_token_ms"] = round(ttft_pct["mean"], 2)
            metrics["ttft_p50_ms"] = round(ttft_pct["p50"], 2)
            metrics["ttft_p95_ms"] = round(ttft_pct["p95"], 2)
            metrics["ttft_p99_ms"] = round(ttft_pct["p99"], 2)

        status = "success" if completed > 0 else "error"

        logger.info(
            "Inference agent finished  name=%s  status=%s  duration=%.1fs  "
            "completed=%d  errors=%d  req/s=%.2f  tokens/s=%.2f  "
            "p50=%.1fms  p99=%.1fms",
            self.agent_name, status, elapsed,
            completed, len(errors), req_rate, token_rate,
            metrics["latency_p50_ms"], metrics["latency_p99_ms"],
        )
        console.print(
            f"[green]{self.agent_name} completed:[/green] "
            f"{completed} reqs, {len(errors)} errors, "
            f"{req_rate:.2f} req/s, {token_rate:.1f} tok/s in {elapsed:.1f}s"
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
