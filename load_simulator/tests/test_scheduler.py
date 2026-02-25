"""Tests for load_simulator.orchestrator.scheduler."""
from __future__ import annotations

import asyncio
import unittest

from load_simulator.orchestrator.scheduler import LoadScheduler, ScheduledTask, run_with_ramp_up


class ScheduledTaskTests(unittest.TestCase):
    def test_defaults(self):
        task = ScheduledTask(name="test", coroutine_factory=lambda: asyncio.sleep(0))
        self.assertEqual(task.name, "test")
        self.assertEqual(task.start_delay_seconds, 0.0)
        self.assertIsNone(task.repeat_interval_seconds)


class LoadSchedulerTests(unittest.TestCase):
    def test_run_once_task(self):
        results = []

        async def work():
            results.append("done")
            return "ok"

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(name="once", coroutine_factory=work))
        asyncio.run(scheduler.run(duration_seconds=0.5))
        self.assertEqual(results, ["done"])
        self.assertEqual(len(scheduler.results), 1)
        self.assertEqual(scheduler.results[0], ("once", "ok"))

    def test_repeating_task(self):
        call_count = 0

        async def work():
            nonlocal call_count
            call_count += 1
            return call_count

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(
            name="repeat",
            coroutine_factory=work,
            repeat_interval_seconds=0.05,
        ))
        asyncio.run(scheduler.run(duration_seconds=0.25))
        self.assertGreater(call_count, 1)

    def test_delayed_start(self):
        started = []

        async def work():
            started.append(True)

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(
            name="delayed",
            coroutine_factory=work,
            start_delay_seconds=0.1,
        ))
        asyncio.run(scheduler.run(duration_seconds=0.3))
        self.assertEqual(len(started), 1)

    def test_multiple_tasks(self):
        a_count = 0
        b_count = 0

        async def work_a():
            nonlocal a_count
            a_count += 1

        async def work_b():
            nonlocal b_count
            b_count += 1

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(name="a", coroutine_factory=work_a))
        scheduler.add(ScheduledTask(name="b", coroutine_factory=work_b))
        asyncio.run(scheduler.run(duration_seconds=0.3))
        self.assertEqual(a_count, 1)
        self.assertEqual(b_count, 1)

    def test_exception_captured(self):
        async def failing():
            raise ValueError("boom")

        scheduler = LoadScheduler()
        scheduler.add(ScheduledTask(name="fail", coroutine_factory=failing))
        asyncio.run(scheduler.run(duration_seconds=0.2))
        self.assertEqual(len(scheduler.results), 1)
        name, result = scheduler.results[0]
        self.assertEqual(name, "fail")
        self.assertIsInstance(result, ValueError)


class RunWithRampUpTests(unittest.TestCase):
    def test_ramp_up_completes(self):
        call_count = 0

        async def work():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return call_count

        results = asyncio.run(run_with_ramp_up(
            coroutine_factory=work,
            target_concurrency=3,
            ramp_seconds=0.1,
            duration_seconds=0.3,
        ))
        self.assertGreater(call_count, 0)
        self.assertIsInstance(results, list)


if __name__ == "__main__":
    unittest.main()
