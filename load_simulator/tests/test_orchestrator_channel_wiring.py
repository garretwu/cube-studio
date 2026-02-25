from __future__ import annotations

import unittest

from load_simulator.agents.inference import InferenceAgent
from load_simulator.agents.pipeline import PipelineAgent
from load_simulator.config.schema import (
    ChannelConfig,
    ChannelRuntimeConfig,
    GlobalConfig,
    InferenceConfig,
    LoadSimulatorConfig,
    PipelineConfig,
)
from load_simulator.orchestrator.engine import LoadOrchestrator


class OrchestratorChannelWiringTests(unittest.TestCase):
    def test_pipeline_agent_uses_global_auth_and_channel_runtime(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["pipeline"],
            global_config=GlobalConfig(
                cube_studio_url="http://cube.local",
                auth_method="jwt",
                auth_username="alice",
                jwt_password="secret",
            ),
            channels=ChannelConfig(
                cube_studio=ChannelRuntimeConfig(timeout=17, retry_count=4, retry_backoff=0.25)
            ),
            pipeline=PipelineConfig(cube_studio_url="http://cube.local", concurrency=1, duration_seconds=1),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False, dry_run=True)
        agent, _ = orch._build_agent("pipeline")
        self.assertIsInstance(agent, PipelineAgent)
        channel = agent._channel  # noqa: SLF001
        self.assertEqual(channel.timeout, 17)
        self.assertEqual(channel.retry_count, 4)
        self.assertEqual(channel.retry_backoff, 0.25)
        self.assertTrue(channel.dry_run)
        token = channel._headers.get("Authorization", "")  # noqa: SLF001
        self.assertEqual(len(token.split(".")), 3)

    def test_inference_agent_uses_inference_channel_timeout(self) -> None:
        cfg = LoadSimulatorConfig(
            agents=["inference"],
            channels=ChannelConfig(
                inference=ChannelRuntimeConfig(timeout=77, retry_count=1, retry_backoff=0.1)
            ),
            inference=InferenceConfig(duration_seconds=1, concurrency=1),
        )
        orch = LoadOrchestrator(cfg, enable_monitor=False)
        agent, _ = orch._build_agent("inference")
        self.assertIsInstance(agent, InferenceAgent)
        self.assertEqual(agent._channel.timeout, 77)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
