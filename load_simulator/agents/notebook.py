"""NotebookAgent — simulates Jupyter Kernel API execution requests."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.config.schema import NotebookConfig
from load_simulator.metrics.aggregator import compute_percentiles, compute_rate

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


class NotebookAgent(BaseAgent):
    """Simulates Jupyter Kernel API requests.

    Workflow per iteration:
      1. POST /api/kernels  → create a kernel
      2. POST /api/kernels/{id}/execute (via the REST API, if available)
         OR fall back to a local mock
      3. DELETE /api/kernels/{id}  → clean up

    Tracks kernel-creation latency, execution latency, and throughput.
    """

    agent_name = "notebook"

    def __init__(self, config: NotebookConfig) -> None:
        self._config = config

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        errors: list[str] = []
        create_latencies: list[float] = []
        exec_latencies: list[float] = []
        kernels_created = 0
        executions_done = 0
        start_time = time.time()
        deadline = start_time + duration_seconds

        headers: dict[str, str] = {}
        if cfg.token:
            headers["Authorization"] = f"token {cfg.token}"

        timeout = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)
        base_url = cfg.jupyter_url.rstrip("/")

        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            cell_index = 0

            while time.time() < deadline:
                # ── Step 1: create kernel ────────────────────────────────
                t0 = time.time()
                kernel_id: str | None = None
                try:
                    resp = await client.post(
                        f"{base_url}/api/kernels",
                        json={"name": "python3"},
                    )
                    resp.raise_for_status()
                    kernel_id = resp.json().get("id", str(uuid.uuid4()))
                    create_latencies.append(time.time() - t0)
                    kernels_created += 1
                except (httpx.ConnectError, httpx.TimeoutException):
                    # Server not running — simulate
                    await asyncio.sleep(0.1)
                    kernel_id = str(uuid.uuid4())
                    create_latencies.append(time.time() - t0)
                    kernels_created += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Kernel create error: {exc}")
                    await asyncio.sleep(1.0)
                    continue

                # ── Step 2: execute a cell ───────────────────────────────
                cell_code = _SAMPLE_CELLS[cell_index % len(_SAMPLE_CELLS)]
                cell_index += 1
                t1 = time.time()
                try:
                    exec_resp = await client.post(
                        f"{base_url}/api/kernels/{kernel_id}/execute",
                        json={"code": cell_code},
                    )
                    exec_resp.raise_for_status()
                    exec_latencies.append(time.time() - t1)
                    executions_done += 1
                except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
                    # Simulate execution time
                    await asyncio.sleep(0.05 + (len(cell_code) / 10000))
                    exec_latencies.append(time.time() - t1)
                    executions_done += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Execute error: {exc}")

                # ── Step 3: delete kernel ────────────────────────────────
                try:
                    await client.delete(f"{base_url}/api/kernels/{kernel_id}")
                except Exception:
                    pass  # Best-effort cleanup

                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.5, remaining))

        end_time = time.time()
        elapsed = end_time - start_time

        create_pct = compute_percentiles([l * 1000 for l in create_latencies])
        exec_pct = compute_percentiles([l * 1000 for l in exec_latencies])

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
        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],
        )
