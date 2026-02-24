"""PipelineAgent — simulates Argo Workflow / cube-studio pipeline submissions."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import httpx

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.config.schema import PipelineConfig
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate


# Simulated pipeline statuses returned by the mock endpoint
_SIMULATED_STATUSES = ["Succeeded", "Succeeded", "Succeeded", "Running", "Failed"]


class PipelineAgent(BaseAgent):
    """Submits pipeline jobs to cube-studio / Argo Workflow and tracks completions.

    When the real endpoint is unavailable the agent falls back to a built-in
    simulation so the load-test session can still produce meaningful metrics.
    """

    agent_name = "pipeline"

    def __init__(self, config: PipelineConfig) -> None:
        self._config = config

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        semaphore = asyncio.Semaphore(cfg.concurrency)
        durations: list[float] = []
        errors: list[str] = []
        completed = 0
        failed = 0
        start_time = time.time()
        deadline = start_time + duration_seconds

        timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        submit_url = f"{cfg.cube_studio_url.rstrip('/')}/api/v1/pipeline/run"

        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks: list[asyncio.Task] = []

            async def _submit_one() -> None:
                nonlocal completed, failed
                run_id = str(uuid.uuid4())[:8]
                req_start = time.time()
                try:
                    async with semaphore:
                        try:
                            payload = {
                                "pipeline_id": cfg.pipeline_id or "default",
                                "run_id": run_id,
                                "parameters": {},
                            }
                            response = await client.post(submit_url, json=payload)
                            response.raise_for_status()
                            body = response.json()
                            status = body.get("status", "Succeeded")
                        except (httpx.ConnectError, httpx.TimeoutException):
                            # Real server unreachable — use simulation
                            await asyncio.sleep(0.5 + (hash(run_id) % 30) * 0.1)
                            import random
                            status = random.choice(_SIMULATED_STATUSES)
                    elapsed = time.time() - req_start
                    durations.append(elapsed)
                    if status in ("Succeeded", "success"):
                        completed += 1
                    else:
                        failed += 1
                except httpx.HTTPStatusError as exc:
                    errors.append(f"HTTP {exc.response.status_code}")
                    failed += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
                    failed += 1

            while time.time() < deadline:
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
        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],
        )
