from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass

from load_simulator.agents.base import AgentResult
from load_simulator.orchestrator.engine import LoadOrchestrator
from load_simulator.orchestrator.session_store import SessionStore


@dataclass
class _InferenceCfg:
    endpoint: str = "http://infer.local/v1/chat/completions"
    duration_seconds: int = 1
    concurrency: int = 2
    prompt_pool_size: int = 10
    model: str = "m"
    max_tokens: int = 16


@dataclass
class _PipelineCfg:
    cube_studio_url: str = "http://cube.local"
    duration_seconds: int = 1
    concurrency: int = 1
    pipeline_id: str | None = None


@dataclass
class _FineTuneCfg:
    llama_factory_url: str = "http://finetune.local"
    duration_seconds: int = 1


@dataclass
class _NotebookCfg:
    jupyter_url: str = "http://nb.local"
    duration_seconds: int = 1
    token: str = ""


class _Cfg:
    session_id = "s-resume"
    agents = ["inference"]
    bottleneck_analysis = False
    mode = "single"
    inference = _InferenceCfg()
    pipeline = _PipelineCfg()
    finetune = _FineTuneCfg()
    notebook = _NotebookCfg()


class _ResumeOrchestrator(LoadOrchestrator):
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self.stage_calls = 0

    async def _run_preflight(self, selected):  # noqa: ANN001, ANN201
        _ = selected
        return {}

    async def _run_stage(self, selected, *, duration_scale, concurrency_scale):  # noqa: ANN001, ANN201
        _ = selected, duration_scale, concurrency_scale
        self.stage_calls += 1
        return [AgentResult(name="inference", status="success", metrics={"latency_p99_ms": 100.0})], {}, []


class OrchestratorResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_skips_completed_stages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(tmp)
            sid = "s-resume"
            store.start(
                session_id=sid,
                mode="single",
                selected_agents=["inference"],
                preflight={},
                plan=[{"name": "single", "duration_scale": 1.0, "concurrency_scale": 1.0}],
                resumed=False,
            )
            store.update_stage(
                sid,
                stage_name="single",
                stage_index=0,
                agent_results=[{"name": "inference", "status": "success", "metrics": {"latency_p99_ms": 100.0}}],
                adaptive_event={"stage": "single", "action": "CONTINUE"},
                system_metrics={},
                breaking_point=None,
            )

            orch = _ResumeOrchestrator(_Cfg(), enable_monitor=False, session_dir=tmp, resume_session_id=sid)
            result = await orch.run(only=["inference"])
            self.assertEqual(orch.stage_calls, 0)
            self.assertEqual(len(result.agent_results), 1)
            self.assertEqual(result.agent_results[0].name, "inference")


if __name__ == "__main__":
    unittest.main()
