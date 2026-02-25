from __future__ import annotations

import unittest

from load_simulator.agents.finetune import FineTuneAgent
from load_simulator.agents.inference import InferenceAgent
from load_simulator.agents.notebook import NotebookAgent
from load_simulator.agents.pipeline import PipelineAgent
from load_simulator.channels.inference import InferenceResult


class _InferenceCfg:
    endpoint = "http://localhost:8000/v1/chat/completions"
    model = "test-model"
    max_tokens = 32
    concurrency = 2
    prompt_pool_size = 10
    stream = False


class _NotebookCfg:
    jupyter_url = "http://localhost:8888"
    token = ""


class _PipelineCfg:
    cube_studio_url = "http://localhost"
    pipeline_id = None
    concurrency = 2


class _FineTuneCfg:
    llama_factory_url = "http://localhost"


class _FakeInferenceChannel:
    async def chat_completion(self, messages, *, max_tokens=256, stream=False, extra=None):  # noqa: ANN001, ANN201
        _ = messages, extra
        return InferenceResult(
            ok=True,
            status_code=200,
            latency_seconds=0.01,
            body={"usage": {"completion_tokens": max_tokens}},
            ttft_seconds=0.001 if stream else None,
        )


class _FakeNotebookChannel:
    def __init__(self) -> None:
        self.created = 0
        self.executed = 0
        self.deleted = 0

    async def create_kernel(self, kernel_name="python3"):  # noqa: ANN001, ANN201
        _ = kernel_name
        self.created += 1
        return {"id": f"k-{self.created}"}

    async def execute_code(self, kernel_id, code):  # noqa: ANN001, ANN201
        _ = kernel_id, code
        self.executed += 1
        return {"ok": True}

    async def delete_kernel(self, kernel_id):  # noqa: ANN001, ANN201
        _ = kernel_id
        self.deleted += 1
        return {"ok": True}


class _FakeCubeChannel:
    def __init__(self) -> None:
        self.next_id = 100

    async def create_pipeline(self, params):  # noqa: ANN001, ANN201
        _ = params
        self.next_id += 1
        return {"id": self.next_id}

    async def run_pipeline(self, pipeline_id):  # noqa: ANN001, ANN201
        _ = pipeline_id
        return {"status": "Succeeded", "steps_completed": 10, "gpu_util_pct": 88.0}

    async def create_task(self, params):  # noqa: ANN001, ANN201
        _ = params
        return {"id": 1}


class AgentChannelIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inference_agent_uses_channel(self) -> None:
        agent = InferenceAgent(_InferenceCfg(), channel=_FakeInferenceChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertGreater(result.metrics.get("requests_completed", 0), 0)
        self.assertGreater(result.metrics.get("tokens_per_sec", 0), 0)

    async def test_notebook_agent_uses_channel(self) -> None:
        fake = _FakeNotebookChannel()
        agent = NotebookAgent(_NotebookCfg(), channel=fake)
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertGreater(result.metrics.get("kernels_created", 0), 0)
        self.assertGreater(result.metrics.get("executions_done", 0), 0)
        self.assertEqual(fake.created, fake.deleted)

    async def test_pipeline_agent_uses_channel(self) -> None:
        agent = PipelineAgent(_PipelineCfg(), channel=_FakeCubeChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertGreater(result.metrics.get("jobs_submitted", 0), 0)

    async def test_finetune_agent_uses_channel(self) -> None:
        agent = FineTuneAgent(_FineTuneCfg(), channel=_FakeCubeChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertGreater(result.metrics.get("jobs_completed", 0), 0)
        self.assertGreater(result.metrics.get("total_steps", 0), 0)


    async def test_inference_agent_ttft_tracking(self) -> None:
        """When stream=True, TTFT metrics should be populated."""

        class _StreamCfg:
            endpoint = "http://localhost:8000/v1/chat/completions"
            model = "test-model"
            max_tokens = 32
            concurrency = 2
            prompt_pool_size = 10
            stream = True

        agent = InferenceAgent(_StreamCfg(), channel=_FakeInferenceChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertIn("ttft_p50_ms", result.metrics)
        self.assertIn("ttft_p95_ms", result.metrics)
        self.assertIn("ttft_p99_ms", result.metrics)
        self.assertIn("time_to_first_token_ms", result.metrics)
        # TTFT should be positive (the fake channel returns 0.001s = 1ms)
        self.assertGreater(result.metrics["ttft_p50_ms"], 0)
        # Request logs should be in raw
        self.assertIn("request_logs", result.raw)
        self.assertGreater(len(result.raw["request_logs"]), 0)
        # Each log entry should have time_to_first_token_ms
        first_log = result.raw["request_logs"][0]
        self.assertIn("time_to_first_token_ms", first_log)

    async def test_inference_agent_no_ttft_without_stream(self) -> None:
        """When stream=False, TTFT metrics should NOT be populated."""
        agent = InferenceAgent(_InferenceCfg(), channel=_FakeInferenceChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        self.assertNotIn("ttft_p50_ms", result.metrics)
        # Request logs should still be present
        self.assertIn("request_logs", result.raw)


if __name__ == "__main__":
    unittest.main()
