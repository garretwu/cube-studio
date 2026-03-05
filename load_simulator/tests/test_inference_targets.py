"""Tests for InferenceConfig.resolved_targets() and multi-target orchestrator support."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.config.loader import load_config
from load_simulator.config.schema import (
    InferenceConfig,
    InferenceTargetConfig,
    LoadSimulatorConfig,
)
from load_simulator.orchestrator.engine import LoadOrchestrator


# ---------------------------------------------------------------------------
# resolved_targets() unit tests
# ---------------------------------------------------------------------------

class TestResolvedTargets(unittest.TestCase):
    def test_no_targets_falls_back_to_top_level(self) -> None:
        cfg = InferenceConfig(endpoint="http://host:8000", model="my-model", api_key="sk-abc")
        targets = cfg.resolved_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].name, "default")
        self.assertEqual(targets[0].endpoint, "http://host:8000")
        self.assertEqual(targets[0].model, "my-model")
        self.assertEqual(targets[0].api_key, "sk-abc")

    def test_explicit_targets_override_top_level(self) -> None:
        cfg = InferenceConfig(
            endpoint="http://should-be-ignored",
            model="ignored-model",
            targets=[
                InferenceTargetConfig(name="a", endpoint="http://a:8000", model="model-a"),
                InferenceTargetConfig(name="b", endpoint="http://b:8000", model="model-b", api_key="sk-b"),
            ],
        )
        targets = cfg.resolved_targets()
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].name, "a")
        self.assertEqual(targets[0].endpoint, "http://a:8000")
        self.assertEqual(targets[1].name, "b")
        self.assertEqual(targets[1].api_key, "sk-b")

    def test_single_target_in_list(self) -> None:
        cfg = InferenceConfig(
            targets=[InferenceTargetConfig(name="only", endpoint="http://only:9000", model="m")],
        )
        targets = cfg.resolved_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].name, "only")

    def test_target_defaults(self) -> None:
        t = InferenceTargetConfig()
        self.assertEqual(t.name, "default")
        self.assertEqual(t.endpoint, "http://localhost:8000")
        self.assertEqual(t.api_key, "")
        self.assertEqual(t.model, "deepseek-r1")

    def test_resolved_targets_returns_copy(self) -> None:
        cfg = InferenceConfig(
            targets=[InferenceTargetConfig(name="x", endpoint="http://x", model="mx")],
        )
        a = cfg.resolved_targets()
        b = cfg.resolved_targets()
        self.assertIsNot(a, b)

    def test_empty_targets_list_falls_back(self) -> None:
        cfg = InferenceConfig(endpoint="http://e", model="m", api_key="k", targets=[])
        targets = cfg.resolved_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].endpoint, "http://e")
        self.assertEqual(targets[0].model, "m")
        self.assertEqual(targets[0].api_key, "k")


# ---------------------------------------------------------------------------
# YAML config loading with targets
# ---------------------------------------------------------------------------

class TestMultiTargetYamlLoading(unittest.TestCase):
    def test_load_yaml_with_targets(self) -> None:
        content = """\
agents:
  - inference
mode: single
inference:
  concurrency: 2
  duration_seconds: 10
  targets:
    - name: "model-a"
      endpoint: "http://a:8000"
      model: "A"
      api_key: "sk-a"
    - name: "model-b"
      endpoint: "http://b:8000"
      model: "B"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertEqual(len(cfg.inference.targets), 2)
            self.assertEqual(cfg.inference.targets[0].name, "model-a")
            self.assertEqual(cfg.inference.targets[0].endpoint, "http://a:8000")
            self.assertEqual(cfg.inference.targets[0].api_key, "sk-a")
            self.assertEqual(cfg.inference.targets[1].name, "model-b")
            self.assertEqual(cfg.inference.targets[1].api_key, "")  # default
            # resolved_targets should return the explicit list
            resolved = cfg.inference.resolved_targets()
            self.assertEqual(len(resolved), 2)
        finally:
            os.unlink(path)

    def test_load_yaml_without_targets_backward_compat(self) -> None:
        content = """\
agents:
  - inference
mode: single
inference:
  endpoint: "http://legacy:8000"
  model: "legacy-model"
  api_key: "sk-legacy"
  concurrency: 2
  duration_seconds: 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertEqual(len(cfg.inference.targets), 0)
            resolved = cfg.inference.resolved_targets()
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0].name, "default")
            self.assertEqual(resolved[0].endpoint, "http://legacy:8000")
            self.assertEqual(resolved[0].model, "legacy-model")
            self.assertEqual(resolved[0].api_key, "sk-legacy")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Orchestrator _build_inference_agents tests
# ---------------------------------------------------------------------------

class _StubAgent(BaseAgent):
    agent_name = "inference"

    def __init__(self, config=None, channel=None):  # noqa: ANN001
        self._config = config
        self._channel = channel

    async def run(self, duration_seconds: int) -> AgentResult:
        return AgentResult(
            name=self.agent_name,
            status="success",
            metrics={"latency_p99_ms": 10.0, "error_rate": 0.0},
            start_time=0.0,
            end_time=0.1,
        )


class TestBuildInferenceAgents(unittest.TestCase):
    def test_single_target_backward_compat(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(
                endpoint="http://single:8000",
                model="single-model",
                api_key="sk-single",
                concurrency=2,
                duration_seconds=10,
            ),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        agents = orch._build_inference_agents(duration_scale=1.0, concurrency_scale=1.0)
        self.assertEqual(len(agents), 1)
        name, agent, duration = agents[0]
        self.assertEqual(name, "inference:default")
        self.assertEqual(agent.agent_name, "inference:default")
        self.assertEqual(agent._channel.endpoint, "http://single:8000")
        self.assertEqual(agent._channel.model, "single-model")
        self.assertEqual(duration, 10)

    def test_multi_target_creates_multiple_agents(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(
                concurrency=4,
                duration_seconds=20,
                targets=[
                    InferenceTargetConfig(name="alpha", endpoint="http://a:8000", model="model-a", api_key="sk-a"),
                    InferenceTargetConfig(name="beta", endpoint="http://b:8000", model="model-b"),
                ],
            ),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        agents = orch._build_inference_agents(duration_scale=1.0, concurrency_scale=1.0)
        self.assertEqual(len(agents), 2)

        name_a, agent_a, dur_a = agents[0]
        self.assertEqual(name_a, "inference:alpha")
        self.assertEqual(agent_a.agent_name, "inference:alpha")
        self.assertEqual(agent_a._channel.endpoint, "http://a:8000")
        self.assertEqual(agent_a._channel.model, "model-a")
        self.assertEqual(agent_a._channel.api_key, "sk-a")
        self.assertEqual(dur_a, 20)

        name_b, agent_b, dur_b = agents[1]
        self.assertEqual(name_b, "inference:beta")
        self.assertEqual(agent_b._channel.endpoint, "http://b:8000")
        self.assertEqual(agent_b._channel.model, "model-b")
        self.assertEqual(agent_b._channel.api_key, "")
        self.assertEqual(dur_b, 20)

    def test_concurrency_scale_applied(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(concurrency=10, duration_seconds=10),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        agents = orch._build_inference_agents(duration_scale=1.0, concurrency_scale=0.5)
        _, agent, _ = agents[0]
        # concurrency should be scaled: round(10 * 0.5) = 5
        self.assertEqual(agent._config.concurrency, 5)

    def test_duration_scale_applied(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(concurrency=2, duration_seconds=100),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        agents = orch._build_inference_agents(duration_scale=0.5, concurrency_scale=1.0)
        _, _, duration = agents[0]
        self.assertEqual(duration, 50)


# ---------------------------------------------------------------------------
# Orchestrator multi-target preflight tests
# ---------------------------------------------------------------------------

class TestMultiTargetPreflight(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_checks_each_target(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(
                targets=[
                    InferenceTargetConfig(name="t1", endpoint="http://t1:8000", model="m1"),
                    InferenceTargetConfig(name="t2", endpoint="http://t2:9000", model="m2"),
                ],
            ),
        )

        seen_checks: dict[str, str] = {}

        async def checker(name: str, url: str) -> tuple[bool, str]:
            seen_checks[name] = url
            return (True, "ok")

        orch = LoadOrchestrator(cfg, preflight_checker=checker, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = await orch.run(only=["inference"])
        # Both targets should be checked independently
        self.assertIn("inference_endpoint:t1", result.preflight)
        self.assertIn("inference_endpoint:t2", result.preflight)
        self.assertEqual(result.preflight["inference_endpoint:t1"]["ok"], True)
        self.assertEqual(result.preflight["inference_endpoint:t2"]["ok"], True)
        self.assertEqual(seen_checks["inference_endpoint:t1"], "http://t1:8000/v1/models")
        self.assertEqual(seen_checks["inference_endpoint:t2"], "http://t2:9000/v1/models")

    async def test_preflight_single_target_uses_default_name(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            inference=InferenceConfig(endpoint="http://single:8000", model="m"),
        )

        async def checker(name: str, url: str) -> tuple[bool, str]:
            return (True, "ok")

        orch = LoadOrchestrator(cfg, preflight_checker=checker, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = await orch.run(only=["inference"])
        self.assertIn("inference_endpoint:default", result.preflight)


# ---------------------------------------------------------------------------
# End-to-end multi-target orchestrator run
# ---------------------------------------------------------------------------

class TestMultiTargetOrchestratorRun(unittest.IsolatedAsyncioTestCase):
    async def test_multi_target_produces_separate_agent_results(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            mode="single",
            inference=InferenceConfig(
                concurrency=1,
                duration_seconds=1,
                targets=[
                    InferenceTargetConfig(name="x", endpoint="http://x:8000", model="mx"),
                    InferenceTargetConfig(name="y", endpoint="http://y:8000", model="my"),
                ],
            ),
        )

        async def checker(name: str, url: str) -> tuple[bool, str]:
            return (True, "ok")

        orch = LoadOrchestrator(cfg, preflight_checker=checker, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = await orch.run()
        names = [r.name for r in result.agent_results]
        self.assertIn("inference:x", names)
        self.assertIn("inference:y", names)
        self.assertEqual(len(result.agent_results), 2)

    async def test_single_target_backward_compat_run(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            mode="single",
            inference=InferenceConfig(
                endpoint="http://legacy:8000",
                model="legacy-m",
                concurrency=1,
                duration_seconds=1,
            ),
        )

        async def checker(name: str, url: str) -> tuple[bool, str]:
            return (True, "ok")

        orch = LoadOrchestrator(cfg, preflight_checker=checker, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = await orch.run()
        self.assertEqual(len(result.agent_results), 1)
        self.assertEqual(result.agent_results[0].name, "inference:default")


if __name__ == "__main__":
    unittest.main()
