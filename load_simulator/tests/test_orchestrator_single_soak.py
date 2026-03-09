"""Tests for single and soak orchestrator modes (extending test_orchestrator_modes.py)."""
from __future__ import annotations

import asyncio
import unittest

from load_simulator.agents.base import AgentResult, BaseAgent
from load_simulator.config.schema import InferenceConfig, LoadSimulatorConfig
from load_simulator.orchestrator.engine import LoadOrchestrator


class _StubAgent(BaseAgent):
    agent_name = "inference"

    def __init__(self, config=None, channel=None):
        pass

    async def run(self, duration_seconds: int) -> AgentResult:
        return AgentResult(
            name=self.agent_name,
            status="success",
            metrics={"latency_p99_ms": 10.0, "error_rate": 0.0},
            start_time=0.0,
            end_time=0.1,
        )


class OrchestratorSingleModeTests(unittest.TestCase):
    def test_single_mode_one_stage(self):
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            mode="single",
            inference=InferenceConfig(duration_seconds=1, concurrency=1),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        # Patch agent factory
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = asyncio.run(orch.run())
        self.assertEqual(result.mode, "single")
        self.assertGreater(len(result.agent_results), 0)
        self.assertEqual(len(result.adaptive_events), 1)
        self.assertEqual(result.adaptive_events[0]["stage"], "single")


class OrchestratorSoakModeTests(unittest.TestCase):
    def test_soak_mode_multiple_stages(self):
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            mode="soak",
            inference=InferenceConfig(duration_seconds=1, concurrency=1),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        orch._AGENT_FACTORIES = {"inference": _StubAgent}

        result = asyncio.run(orch.run())
        self.assertEqual(result.mode, "soak")
        # Soak mode has 3 stages: soak-warmup, soak-steady-1, soak-steady-2
        self.assertEqual(len(result.adaptive_events), 3)
        stage_names = [e["stage"] for e in result.adaptive_events]
        self.assertIn("soak-warmup", stage_names)
        self.assertIn("soak-steady-1", stage_names)

    def test_no_agents_returns_empty(self):
        cfg = LoadSimulatorConfig(agents=[], mode="single")
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        result = asyncio.run(orch.run())
        self.assertEqual(result.agent_results, [])
        self.assertIn("No agents", result.summary)


if __name__ == "__main__":
    unittest.main()
