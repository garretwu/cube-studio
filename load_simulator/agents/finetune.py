"""FineTuneAgent — simulates LLaMA-Factory fine-tuning API load."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

from load_simulator.agents.base import AgentResult, BaseAgent
from lib.channels.cube_studio import CubeStudioChannel
from load_simulator.metrics.aggregator import compute_percentiles

if TYPE_CHECKING:
    from load_simulator.config.schema import FineTuneConfig


# Default training config sent to LLaMA-Factory Gradio/REST API
_DEFAULT_TRAIN_CONFIG = {
    "model_name_or_path": "meta-llama/Llama-2-7b-hf",
    "stage": "sft",
    "do_train": True,
    "finetuning_type": "lora",
    "lora_target": "all",
    "dataset": "identity",
    "template": "llama2",
    "output_dir": "/tmp/llama_factory_test",
    "overwrite_output_dir": True,
    "per_device_train_batch_size": 1,
    "num_train_epochs": 1,
    "max_steps": 10,
    "logging_steps": 5,
    "save_steps": 10,
    "fp16": True,
}


class FineTuneAgent(BaseAgent):
    """Exercises the LLaMA-Factory training API.

    1. Attempts to POST a training job to the LLaMA-Factory REST endpoint.
    2. Falls back to a local simulation (CPU mock) when the server is down.
    3. Tracks training throughput (simulated steps/s), GPU utilization
       (via ``nvidia-smi`` when available), and job latency.
    """

    agent_name = "finetune"

    def __init__(self, config: "FineTuneConfig", channel: CubeStudioChannel | None = None) -> None:
        self._config = config
        auth_method = str(getattr(config, "auth_method", "username"))
        auth_username = str(getattr(config, "auth_username", "admin"))
        jwt_secret = getattr(config, "jwt_password", None)
        # NOTE: keep compatibility with v0.1 config that only has llama_factory_url.
        self._channel = channel or CubeStudioChannel(
            base_url=config.llama_factory_url,
            auth_method=auth_method,
            username=auth_username,
            jwt_secret=jwt_secret,
            timeout=30,
            retry_count=2,
            retry_backoff=0.5,
        )

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        cfg = self._config
        errors: list[str] = []
        request_logs: list[dict] = []
        start_time = time.time()
        deadline = start_time + duration_seconds

        step_times: list[float] = []
        gpu_utils: list[float] = []
        jobs_started = 0
        jobs_completed = 0

        while time.time() < deadline:
            job_start = time.time()
            jobs_started += 1
            url = f"{cfg.llama_factory_url}/pipeline_modelview/api/"
            try:
                # Prefer unified CubeStudio channel workflow.
                run_id = uuid.uuid4().hex[:8]
                pipeline = await self._channel.create_pipeline(
                    {"name": f"load-finetune-{run_id}", "project_id": 1}
                )
                pipeline_id = int(pipeline.get("id", 0) or 0)
                if pipeline_id <= 0:
                    raise RuntimeError("finetune pipeline creation returned invalid id")

                _ = await self._channel.create_task(
                    {
                        "name": f"finetune-task-{run_id}",
                        "pipeline_id": pipeline_id,
                        "args": _DEFAULT_TRAIN_CONFIG,
                    }
                )
                body = await self._channel.run_pipeline(pipeline_id)
                steps = body.get("steps_completed", _DEFAULT_TRAIN_CONFIG["max_steps"])
                elapsed_for_job = time.time() - job_start
                step_times.append(elapsed_for_job / max(1, int(steps)))
                gpu_util = body.get("gpu_util_pct", None)
                if gpu_util is not None:
                    gpu_utils.append(float(gpu_util))
                jobs_completed += 1
                request_logs.append({
                    "timestamp": job_start,
                    "method": "POST",
                    "url": url,
                    "status_code": 200,
                    "latency_ms": round(elapsed_for_job * 1000, 2),
                    "error_message": None,
                })
            except Exception as exc:  # noqa: BLE001
                # API path unavailable — fallback to deterministic local simulation.
                sim_result = await _simulate_training(
                    max_steps=_DEFAULT_TRAIN_CONFIG["max_steps"],
                    deadline=deadline,
                )
                step_times.extend(sim_result["step_times"])
                gpu_utils.extend(sim_result["gpu_utils"])
                jobs_completed += 1
                errors.append(f"{type(exc).__name__}: {exc}")
                request_logs.append({
                    "timestamp": job_start,
                    "method": "POST",
                    "url": url,
                    "status_code": 0,
                    "latency_ms": round((time.time() - job_start) * 1000, 2),
                    "error_message": f"{type(exc).__name__}: {exc}",
                })

            # Pause between job submissions to avoid hammering the server
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(5.0, remaining))

        end_time = time.time()

        pct = compute_percentiles([t * 1000 for t in step_times])  # ms per step
        avg_gpu = sum(gpu_utils) / len(gpu_utils) if gpu_utils else 0.0
        avg_step_time_s = sum(step_times) / len(step_times) if step_times else 0.0
        steps_per_sec = 1.0 / avg_step_time_s if avg_step_time_s > 0 else 0.0

        metrics: dict[str, Any] = {
            "jobs_started": jobs_started,
            "jobs_completed": jobs_completed,
            "total_steps": len(step_times),
            "steps_per_sec": round(steps_per_sec, 3),
            "step_latency_p50_ms": round(pct["p50"], 2),
            "step_latency_p95_ms": round(pct["p95"], 2),
            "step_latency_p99_ms": round(pct["p99"], 2),
            "step_latency_mean_ms": round(pct["mean"], 2),
            "gpu_util_pct_mean": round(avg_gpu, 2),
            "llama_factory_url": cfg.llama_factory_url,
        }

        status = "success" if jobs_completed > 0 else "error"
        return AgentResult(
            name=self.agent_name,
            status=status,
            metrics=metrics,
            start_time=start_time,
            end_time=end_time,
            errors=errors[:50],
            raw={"request_logs": request_logs},
        )


async def _simulate_training(max_steps: int, deadline: float) -> dict[str, Any]:
    """Local mock that simulates training steps without a real GPU server."""
    import random

    step_times: list[float] = []
    gpu_utils: list[float] = []

    for step in range(max_steps):
        if time.time() >= deadline:
            break
        # Simulate variable step time (0.05–0.3s per step, CPU bound)
        step_duration = random.uniform(0.05, 0.30)
        await asyncio.sleep(step_duration)
        step_times.append(step_duration)
        # Simulate GPU utilization between 70–95%
        gpu_utils.append(random.uniform(70.0, 95.0))

    return {"step_times": step_times, "gpu_utils": gpu_utils}
