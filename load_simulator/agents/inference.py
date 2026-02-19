"""InferenceAgent — stress-tests a vLLM-compatible /v1/chat/completions endpoint."""
from __future__ import annotations

import asyncio
import statistics
import time
from typing import Any

import httpx

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.config.schema import InferenceConfig
from load_simulator.load.prompt_pool import PromptPool
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate


class InferenceAgent(BaseAgent):
    """Sends concurrent chat-completion requests to a vLLM endpoint.

    Tracks per-request latency (p50/p95/p99), throughput (req/s), token
    throughput (tokens/s), and error rate.
    """

    agent_name = "inference"

    def __init__(self, config: InferenceConfig) -> None:
        self._config = config
        self._prompt_pool = PromptPool(size=config.prompt_pool_size)

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        semaphore = asyncio.Semaphore(cfg.concurrency)
        latencies: list[float] = []
        token_counts: list[int] = []
        errors: list[str] = []
        completed = 0
        start_time = time.time()
        deadline = start_time + duration_seconds

        timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks: list[asyncio.Task] = []

            async def _one_request() -> None:
                nonlocal completed
                prompt = self._prompt_pool.sample()
                payload: dict[str, Any] = {
                    "model": cfg.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": cfg.max_tokens,
                    "stream": False,
                }
                req_start = time.time()
                try:
                    async with semaphore:
                        response = await client.post(cfg.endpoint, json=payload)
                    response.raise_for_status()
                    elapsed = time.time() - req_start
                    latencies.append(elapsed)
                    # Extract token count if present
                    try:
                        body = response.json()
                        usage = body.get("usage") or {}
                        tokens = usage.get("completion_tokens") or usage.get("total_tokens") or cfg.max_tokens
                        token_counts.append(int(tokens))
                    except Exception:
                        token_counts.append(cfg.max_tokens)
                    completed += 1
                except httpx.TimeoutException as exc:
                    errors.append(f"Timeout: {exc}")
                except httpx.HTTPStatusError as exc:
                    errors.append(f"HTTP {exc.response.status_code}: {exc.response.text[:120]}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")

            # Continuously fire requests until deadline
            while time.monotonic() < (start_time + duration_seconds - time.monotonic() + time.monotonic()):
                if time.time() >= deadline:
                    break
                task = asyncio.create_task(_one_request())
                tasks.append(task)
                # Stagger slightly to avoid a thundering herd at t=0
                await asyncio.sleep(0.01)
                # Prune completed tasks to avoid unbounded list growth
                tasks = [t for t in tasks if not t.done()]

            # Wait for all in-flight requests to finish
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
            "endpoint": cfg.endpoint,
            "model": cfg.model,
        }

        status = "success" if completed > 0 else "error"
        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],  # cap stored errors
        )
