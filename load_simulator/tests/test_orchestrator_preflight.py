from __future__ import annotations

import unittest
from dataclasses import dataclass

from load_simulator.agents.base import AgentResult
from load_simulator.orchestrator.engine import LoadOrchestrator


class _InferenceTarget:
    def __init__(self, name: str = "default", endpoint: str = "http://infer.local/v1/chat/completions",
                 api_key: str = "", model: str = "m") -> None:
        self.name = name
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model


@dataclass
class _InferenceCfg:
    endpoint: str = "http://infer.local/v1/chat/completions"
    duration_seconds: int = 1

    def resolved_targets(self) -> list:
        return [_InferenceTarget(endpoint=self.endpoint)]


@dataclass
class _PipelineCfg:
    cube_studio_url: str = "http://cube.local"
    duration_seconds: int = 1


@dataclass
class _FineTuneCfg:
    llama_factory_url: str = "http://finetune.local"
    duration_seconds: int = 1


@dataclass
class _NotebookCfg:
    jupyter_url: str = "http://nb.local"
    duration_seconds: int = 1


class _Cfg:
    session_id = "s-preflight"
    agents = ["inference", "pipeline", "finetune", "notebook"]
    bottleneck_analysis = False
    inference = _InferenceCfg()
    pipeline = _PipelineCfg()
    finetune = _FineTuneCfg()
    notebook = _NotebookCfg()


class _GlobalCfg:
    prometheus_url = "http://prom.local:9090"


class _PromCfg:
    enabled = True


class _CfgWithProm(_Cfg):
    global_config = _GlobalCfg()
    prometheus_queries = _PromCfg()


class _FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, duration_seconds: int):  # noqa: ANN201
        _ = duration_seconds
        return AgentResult(name=self.name, status="success", metrics={"ok": 1})


class _TestOrchestrator(LoadOrchestrator):
    def _build_agent(self, name, *, duration_scale=1.0, concurrency_scale=1.0):  # noqa: ANN001, ANN201
        _ = duration_scale, concurrency_scale
        return _FakeAgent(name), 0

    def _build_inference_agents(self, *, duration_scale=1.0, concurrency_scale=1.0):  # noqa: ANN001, ANN201
        _ = duration_scale, concurrency_scale
        targets = self._config.inference.resolved_targets()
        return [(f"inference:{t.name}", _FakeAgent(f"inference:{t.name}"), 0) for t in targets]


class OrchestratorPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_preflight_keys(self) -> None:
        async def checker(name: str, url: str):  # noqa: ANN202
            return (True, f"{name} ok {url}")

        orch = _TestOrchestrator(_Cfg(), preflight_checker=checker, enable_monitor=False)
        result = await orch.run(only=["inference", "notebook"])
        self.assertIn("inference_endpoint:default", result.preflight)
        self.assertIn("notebook_api", result.preflight)
        self.assertIn("prometheus", result.preflight)
        self.assertEqual(result.preflight["inference_endpoint:default"]["ok"], True)
        self.assertIsNone(result.preflight["prometheus"]["ok"])

    async def test_preflight_failure_propagates_to_result(self) -> None:
        async def checker(name: str, url: str):  # noqa: ANN202
            _ = url
            if name == "finetune_endpoint":
                return (False, "connection refused")
            return (True, "ok")

        orch = _TestOrchestrator(_Cfg(), preflight_checker=checker, enable_monitor=False)
        result = await orch.run(only=["finetune"])
        self.assertIn("finetune_endpoint", result.preflight)
        self.assertEqual(result.preflight["finetune_endpoint"]["ok"], False)
        self.assertIn("connection refused", result.preflight["finetune_endpoint"]["detail"])

    async def test_prometheus_preflight_when_enabled(self) -> None:
        seen = {}

        async def checker(name: str, url: str):  # noqa: ANN202
            seen[name] = url
            return (True, "ok")

        orch = _TestOrchestrator(_CfgWithProm(), preflight_checker=checker, enable_monitor=False)
        result = await orch.run(only=["inference"])
        self.assertIn("prometheus", result.preflight)
        self.assertEqual(result.preflight["prometheus"]["ok"], True)
        self.assertIn("/api/v1/query?query=up", seen["prometheus"])


if __name__ == "__main__":
    unittest.main()
