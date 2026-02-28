"""Unit tests for agent file-logging (setup_file_logger + log output)."""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from load_simulator.agents.finetune import FineTuneAgent
from load_simulator.agents.finetune import setup_file_logger as finetune_setup
from load_simulator.agents.inference import InferenceAgent
from load_simulator.agents.inference import setup_file_logger as inference_setup
from load_simulator.agents.notebook import NotebookAgent
from load_simulator.agents.notebook import setup_file_logger as notebook_setup
from load_simulator.agents.pipeline import PipelineAgent
from load_simulator.agents.pipeline import setup_file_logger as pipeline_setup
from load_simulator.channels.inference import InferenceResult


# ── Minimal config stubs ──────────────────────────────────────────────────────

class _InferenceCfg:
    endpoint = "http://localhost:8000/v1/chat/completions"
    model = "test-model"
    max_tokens = 32
    concurrency = 1
    prompt_pool_size = 5
    stream = False


class _PipelineCfg:
    cube_studio_url = "http://localhost"
    pipeline_id = None
    concurrency = 1
    auth_method = "username"
    auth_username = "admin"
    jwt_password = None


class _FineTuneCfg:
    llama_factory_url = "http://localhost"
    auth_method = "username"
    auth_username = "admin"
    jwt_password = None


class _NotebookCfg:
    jupyter_url = "http://localhost:8888"
    token = ""
    username = "admin"


# ── Fake channels ─────────────────────────────────────────────────────────────

class _FakeInferenceChannel:
    async def chat_completion(self, messages, *, max_tokens=256, stream=False, extra=None):  # noqa: ANN001
        _ = messages, extra
        return InferenceResult(
            ok=True,
            status_code=200,
            latency_seconds=0.01,
            body={"usage": {"completion_tokens": max_tokens}},
            ttft_seconds=None,
        )


class _FakeCubeChannel:
    def __init__(self) -> None:
        self._id = 100

    async def create_pipeline(self, params):  # noqa: ANN001
        self._id += 1
        return {"id": self._id}

    async def run_pipeline(self, pipeline_id):  # noqa: ANN001
        return {"status": "Succeeded", "steps_completed": 5, "gpu_util_pct": 80.0}

    async def create_task(self, params):  # noqa: ANN001
        return {"id": 1}


class _FakeNotebookChannel:
    async def create_kernel(self, kernel_name="python3"):  # noqa: ANN001
        return {"id": "k1"}

    async def execute_code(self, kernel_id, code):  # noqa: ANN001
        return {"status": "ok", "msg_id": "m1"}

    async def delete_kernel(self, kernel_id):  # noqa: ANN001
        return None

    async def close(self) -> None:
        pass


# ── setup_file_logger tests ───────────────────────────────────────────────────

class SetupFileLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        # Remove all FileHandlers from the load_simulator root between tests
        root = logging.getLogger("load_simulator")
        root.handlers = [h for h in root.handlers if not isinstance(h, logging.FileHandler)]

    def test_inference_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            inference_setup(d)
            self.assertTrue((Path(d) / "inference-agent.log").exists())

    def test_pipeline_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pipeline_setup(d)
            self.assertTrue((Path(d) / "pipeline-agent.log").exists())

    def test_finetune_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            finetune_setup(d)
            self.assertTrue((Path(d) / "finetune-agent.log").exists())

    def test_notebook_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            notebook_setup(d)
            self.assertTrue((Path(d) / "notebook-agent.log").exists())

    def test_idempotent_no_duplicate_handlers(self) -> None:
        """Calling setup_file_logger twice must not add duplicate handlers."""
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            inference_setup(d)
            inference_setup(d)
            fh_count = sum(
                1 for h in root.handlers
                if isinstance(h, logging.FileHandler)
                and Path(h.baseFilename).name == "inference-agent.log"
            )
            self.assertEqual(fh_count, 1)

    def test_creates_parent_directory(self) -> None:
        """setup_file_logger creates nested directories if they don't exist."""
        with tempfile.TemporaryDirectory() as base:
            nested = Path(base) / "sessions" / "abc123"
            inference_setup(nested)
            self.assertTrue((nested / "inference-agent.log").exists())

    def test_log_written_to_file(self) -> None:
        """Messages from the load_simulator hierarchy appear in the log file."""
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            pipeline_setup(d)
            logging.getLogger("load_simulator.agents.pipeline").info("hello-test-marker")
            # Flush handlers
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "pipeline-agent.log").read_text(encoding="utf-8")
            self.assertIn("hello-test-marker", text)


# ── Agent log_dir wiring tests ────────────────────────────────────────────────

class AgentLogDirTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = logging.getLogger("load_simulator")
        root.handlers = [h for h in root.handlers if not isinstance(h, logging.FileHandler)]

    async def test_inference_agent_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            agent = InferenceAgent(_InferenceCfg(), channel=_FakeInferenceChannel(), log_dir=d)
            result = await agent.run(duration_seconds=1)
            self.assertEqual(result.status, "success")
            self.assertTrue((Path(d) / "inference-agent.log").exists())

    async def test_inference_log_contains_start_and_end(self) -> None:
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            agent = InferenceAgent(_InferenceCfg(), channel=_FakeInferenceChannel(), log_dir=d)
            await agent.run(duration_seconds=1)
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "inference-agent.log").read_text(encoding="utf-8")
            self.assertIn("Inference agent starting", text)
            self.assertIn("Inference agent finished", text)
            self.assertIn("endpoint=", text)
            self.assertIn("completed=", text)

    async def test_pipeline_agent_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            agent = PipelineAgent(_PipelineCfg(), channel=_FakeCubeChannel(), log_dir=d)
            result = await agent.run(duration_seconds=1)
            self.assertEqual(result.status, "success")
            self.assertTrue((Path(d) / "pipeline-agent.log").exists())

    async def test_pipeline_log_contains_start_and_end(self) -> None:
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            agent = PipelineAgent(_PipelineCfg(), channel=_FakeCubeChannel(), log_dir=d)
            await agent.run(duration_seconds=1)
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "pipeline-agent.log").read_text(encoding="utf-8")
            self.assertIn("Pipeline agent starting", text)
            self.assertIn("Pipeline agent finished", text)
            self.assertIn("url=", text)
            self.assertIn("succeeded=", text)

    async def test_finetune_agent_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            agent = FineTuneAgent(_FineTuneCfg(), channel=_FakeCubeChannel(), log_dir=d)
            result = await agent.run(duration_seconds=2)
            self.assertEqual(result.status, "success")
            self.assertTrue((Path(d) / "finetune-agent.log").exists())

    async def test_finetune_log_contains_start_and_end(self) -> None:
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            agent = FineTuneAgent(_FineTuneCfg(), channel=_FakeCubeChannel(), log_dir=d)
            await agent.run(duration_seconds=2)
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "finetune-agent.log").read_text(encoding="utf-8")
            self.assertIn("FineTune agent starting", text)
            self.assertIn("FineTune agent finished", text)
            self.assertIn("jobs_completed=", text)

    async def test_notebook_agent_creates_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            agent = NotebookAgent(_NotebookCfg(), channel=_FakeNotebookChannel(), log_dir=d)
            result = await agent.run(duration_seconds=1)
            self.assertEqual(result.status, "success")
            self.assertTrue((Path(d) / "notebook-agent.log").exists())

    async def test_notebook_log_contains_start_and_end(self) -> None:
        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            agent = NotebookAgent(_NotebookCfg(), channel=_FakeNotebookChannel(), log_dir=d)
            await agent.run(duration_seconds=1)
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "notebook-agent.log").read_text(encoding="utf-8")
            self.assertIn("Notebook agent starting", text)
            self.assertIn("Notebook agent finished", text)

    async def test_no_log_file_without_log_dir(self) -> None:
        """Agents without log_dir must not create any log files."""
        agent = InferenceAgent(_InferenceCfg(), channel=_FakeInferenceChannel())
        result = await agent.run(duration_seconds=1)
        self.assertEqual(result.status, "success")
        # No FileHandler should have been added by this agent
        root = logging.getLogger("load_simulator")
        fh_names = [
            Path(h.baseFilename).name
            for h in root.handlers
            if isinstance(h, logging.FileHandler)
        ]
        self.assertNotIn("inference-agent.log", fh_names)

    async def test_error_logged_on_request_failure(self) -> None:
        """Inference errors are written to the log file."""
        class _FailChannel:
            async def chat_completion(self, messages, *, max_tokens=256, stream=False, extra=None):  # noqa: ANN001
                raise RuntimeError("connection refused")

        root = logging.getLogger("load_simulator")
        with tempfile.TemporaryDirectory() as d:
            agent = InferenceAgent(_InferenceCfg(), channel=_FailChannel(), log_dir=d)
            result = await agent.run(duration_seconds=1)
            for h in root.handlers:
                h.flush()
            text = (Path(d) / "inference-agent.log").read_text(encoding="utf-8")
            self.assertIn("connection refused", text)
            # status is error when no requests complete
            self.assertEqual(result.status, "error")


if __name__ == "__main__":
    unittest.main()
