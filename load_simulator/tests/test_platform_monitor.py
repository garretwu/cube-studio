"""Tests for load_simulator.orchestrator.platform_monitor."""
from __future__ import annotations

import unittest

from load_simulator.orchestrator.platform_monitor import PlatformMonitor, PlatformSnapshot


class _FakeCubeChannel:
    """Fake CubeStudio channel that returns mock notebook data."""

    def __init__(self, notebooks: int = 3, pipelines: int = 2, services: int = 1) -> None:
        self._notebooks = notebooks
        self._pipelines = pipelines
        self._services = services

    async def list_notebooks(self):
        return {"data": [{"id": i} for i in range(self._notebooks)]}

    async def list_pipelines(self):
        return {"data": [{"id": i} for i in range(self._pipelines)]}

    async def list_inference_services(self):
        return {"data": [{"id": i} for i in range(self._services)]}


class _FailingChannel:
    """Channel that always raises."""

    async def list_notebooks(self):
        raise RuntimeError("connection refused")


class PlatformSnapshotTests(unittest.TestCase):
    def test_defaults(self) -> None:
        snap = PlatformSnapshot(timestamp=1.0)
        self.assertEqual(snap.active_notebooks, 0)
        self.assertEqual(snap.active_pipelines, 0)
        self.assertEqual(snap.inference_services, 0)


class PlatformMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_collects_snapshots(self) -> None:
        monitor = PlatformMonitor(channel=_FakeCubeChannel(notebooks=5), interval_seconds=0.05)
        await monitor.start(duration_seconds=0.15)
        self.assertGreater(len(monitor.snapshots), 0)
        self.assertEqual(monitor.snapshots[0].active_notebooks, 5)

    async def test_aggregate_returns_last(self) -> None:
        monitor = PlatformMonitor(channel=_FakeCubeChannel(notebooks=2), interval_seconds=0.05)
        await monitor.start(duration_seconds=0.1)
        agg = monitor.aggregate()
        self.assertEqual(agg["platform_active_notebooks"], 2)
        self.assertEqual(agg["platform_active_pipelines"], 2)
        self.assertEqual(agg["platform_inference_services"], 1)

    async def test_collects_pipeline_and_inference_counts(self) -> None:
        monitor = PlatformMonitor(
            channel=_FakeCubeChannel(notebooks=1, pipelines=4, services=3), interval_seconds=0.05
        )
        await monitor.start(duration_seconds=0.1)
        snap = monitor.snapshots[-1]
        self.assertEqual(snap.active_notebooks, 1)
        self.assertEqual(snap.active_pipelines, 4)
        self.assertEqual(snap.inference_services, 3)

    async def test_aggregate_empty(self) -> None:
        monitor = PlatformMonitor(channel=_FakeCubeChannel(), interval_seconds=0.05)
        self.assertEqual(monitor.aggregate(), {})

    async def test_stop_terminates_early(self) -> None:
        import asyncio

        monitor = PlatformMonitor(channel=_FakeCubeChannel(), interval_seconds=0.05)
        task = asyncio.create_task(monitor.start(duration_seconds=5.0))
        await asyncio.sleep(0.1)
        monitor.stop()
        await task
        # Should have stopped early
        self.assertLess(len(monitor.snapshots), 50)

    async def test_handles_channel_failure(self) -> None:
        monitor = PlatformMonitor(channel=_FailingChannel(), interval_seconds=0.05)
        await monitor.start(duration_seconds=0.1)
        self.assertGreater(len(monitor.snapshots), 0)
        # Failed collection should result in zero counts
        self.assertEqual(monitor.snapshots[0].active_notebooks, 0)


if __name__ == "__main__":
    unittest.main()
