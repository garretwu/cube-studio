from __future__ import annotations

import unittest
from dataclasses import dataclass

from load_simulator.agents.base import AgentResult
from load_simulator.orchestrator.engine import LoadOrchestrator


@dataclass
class _InferenceCfg:
    endpoint: str = "http://infer.local/v1/chat/completions"
    duration_seconds: int = 1
    concurrency: int = 2


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
    session_id = "s-mode"
    agents = ["inference", "pipeline", "finetune", "notebook"]
    bottleneck_analysis = False
    mode = "single"
    inference = _InferenceCfg()
    pipeline = _PipelineCfg()
    finetune = _FineTuneCfg()
    notebook = _NotebookCfg()


class _ModeTestOrchestrator(LoadOrchestrator):
    async def _run_stage(self, selected, *, duration_scale, concurrency_scale):  # noqa: ANN001, ANN201
        _ = selected, duration_scale
        if concurrency_scale >= 1.5:
            metrics = {"error_rate": 0.2, "latency_p99_ms": 8000}
        else:
            metrics = {"error_rate": 0.0, "latency_p99_ms": 100}
        results = [
            AgentResult(name="inference", status="success", metrics=metrics),
        ]
        return results, {"cpu_util_pct": 50.0}, []

    async def _run_preflight(self, selected):  # noqa: ANN001, ANN201
        _ = selected
        return {}


class OrchestratorModesTests(unittest.IsolatedAsyncioTestCase):
    async def test_mode_mixed_when_multiple_agents_selected(self) -> None:
        cfg = _Cfg()
        orch = _ModeTestOrchestrator(cfg, enable_monitor=False)
        result = await orch.run(only=["inference", "notebook"])
        self.assertEqual(result.mode, "mixed")
        self.assertGreaterEqual(len(result.adaptive_events), 1)

    async def test_stress_mode_records_breaking_point(self) -> None:
        cfg = _Cfg()
        cfg.mode = "stress"
        orch = _ModeTestOrchestrator(cfg, enable_monitor=False)
        result = await orch.run(only=["inference"])
        self.assertEqual(result.mode, "stress")
        self.assertIsNotNone(result.breaking_point)
        self.assertIn("stress", result.breaking_point["stage"])
        self.assertIn("inference", result.breaking_point["estimated_concurrency"])


if __name__ == "__main__":
    unittest.main()
