"""Tests for load_simulator.agents.base."""
from __future__ import annotations

import asyncio
import unittest

from load_simulator.agents.base import AgentResult, BaseAgent


class AgentResultTests(unittest.TestCase):
    def test_duration_seconds(self):
        r = AgentResult(name="test", status="success", start_time=100.0, end_time=105.5)
        self.assertAlmostEqual(r.duration_seconds, 5.5)

    def test_duration_seconds_missing_times(self):
        r = AgentResult(name="test", status="success")
        self.assertEqual(r.duration_seconds, 0.0)

    def test_error_result(self):
        r = AgentResult.error_result("my_agent", "something broke", start_time=100.0)
        self.assertEqual(r.name, "my_agent")
        self.assertEqual(r.status, "error")
        self.assertEqual(r.errors, ["something broke"])
        self.assertEqual(r.start_time, 100.0)
        self.assertIsNotNone(r.end_time)

    def test_default_fields(self):
        r = AgentResult(name="test", status="success")
        self.assertEqual(r.metrics, {})
        self.assertEqual(r.errors, [])
        self.assertEqual(r.raw, {})


class _SuccessAgent(BaseAgent):
    agent_name = "success_agent"

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        return AgentResult(name=self.agent_name, status="success", metrics={"count": 1})


class _FailingAgent(BaseAgent):
    agent_name = "failing_agent"

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)

    async def _execute(self, duration_seconds: int) -> AgentResult:
        raise RuntimeError("agent crashed")


class _NoExecuteAgent(BaseAgent):
    agent_name = "no_execute"

    async def run(self, duration_seconds: int) -> AgentResult:
        return await self._timed_run(duration_seconds)


class TimedRunTests(unittest.TestCase):
    def test_successful_agent(self):
        agent = _SuccessAgent()
        result = asyncio.run(agent.run(1))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.metrics["count"], 1)
        self.assertIsNotNone(result.start_time)
        self.assertIsNotNone(result.end_time)

    def test_failing_agent_returns_error_result(self):
        agent = _FailingAgent()
        result = asyncio.run(agent.run(1))
        self.assertEqual(result.status, "error")
        self.assertEqual(result.name, "failing_agent")
        self.assertTrue(any("agent crashed" in e for e in result.errors))

    def test_no_execute_raises_not_implemented(self):
        agent = _NoExecuteAgent()
        result = asyncio.run(agent.run(1))
        self.assertEqual(result.status, "error")
        self.assertTrue(len(result.errors) > 0)
        self.assertIn("Unhandled exception", result.errors[0])


if __name__ == "__main__":
    unittest.main()
