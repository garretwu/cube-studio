from __future__ import annotations

import unittest
from dataclasses import dataclass

from load_simulator.agents.base import AgentResult
from load_simulator.orchestrator.engine import LoadOrchestrator


@dataclass
class _RulesCfg:
    pause_error_rate: float = 0.05
    breaking_error_rate: float = 0.10
    p99_breaking_multiplier: float = 10.0
    gpu_mem_reduce_pct: float = 95.0
    cpu_pause_pct: float = 95.0


@dataclass
class _InferenceCfg:
    endpoint: str = "http://infer.local/v1/chat/completions"
    duration_seconds: int = 1
    concurrency: int = 2
    prompt_pool_size: int = 10
    model: str = "m"
    max_tokens: int = 32


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
    session_id = "s-adapt"
    agents = ["inference"]
    bottleneck_analysis = False
    mode = "stress"
    inference = _InferenceCfg()
    pipeline = _PipelineCfg()
    finetune = _FineTuneCfg()
    notebook = _NotebookCfg()
    adaptive_rules = _RulesCfg()


class _AdaptiveConfigOrchestrator(LoadOrchestrator):
    async def _run_stage(self, selected, *, duration_scale, concurrency_scale):  # noqa: ANN001, ANN201
        _ = selected, duration_scale, concurrency_scale
        return [AgentResult(name="inference", status="success", metrics={"error_rate": 0.15, "latency_p99_ms": 120})], {}, []

    async def _run_preflight(self, selected):  # noqa: ANN001, ANN201
        _ = selected
        return {}


class OrchestratorAdaptiveConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_threshold_breaks(self) -> None:
        cfg = _Cfg()
        cfg.adaptive_rules = _RulesCfg()
        orch = _AdaptiveConfigOrchestrator(cfg, enable_monitor=False)
        result = await orch.run(only=["inference"])
        self.assertIsNotNone(result.breaking_point)

    async def test_custom_threshold_avoids_break(self) -> None:
        cfg = _Cfg()
        cfg.adaptive_rules = _RulesCfg()
        cfg.adaptive_rules.breaking_error_rate = 0.20
        orch = _AdaptiveConfigOrchestrator(cfg, enable_monitor=False)
        result = await orch.run(only=["inference"])
        self.assertIsNone(result.breaking_point)
        self.assertGreaterEqual(len(result.adaptive_events), 1)


if __name__ == "__main__":
    unittest.main()
