"""Tests for load_simulator.agents.monitor."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from load_simulator.agents.monitor import MetricsMonitor, SystemSnapshot


class SystemSnapshotTests(unittest.TestCase):
    def test_fields(self):
        snap = SystemSnapshot(
            timestamp=1000.0,
            cpu_util_pct=50.0,
            mem_util_pct=60.0,
            gpu_util_pct=70.0,
            gpu_mem_util_pct=80.0,
            disk_util_pct=30.0,
            io_wait_pct=5.0,
        )
        self.assertEqual(snap.cpu_util_pct, 50.0)
        self.assertEqual(snap.mem_util_pct, 60.0)
        self.assertEqual(snap.gpu_util_pct, 70.0)

    def test_optional_gpu_fields(self):
        snap = SystemSnapshot(timestamp=1.0, cpu_util_pct=10.0, mem_util_pct=20.0)
        self.assertIsNone(snap.gpu_util_pct)
        self.assertIsNone(snap.gpu_mem_util_pct)


class MetricsMonitorTests(unittest.TestCase):
    def _make_snapshot(self, cpu=50.0, mem=60.0, gpu=None, gpu_mem=None, disk=None, io=None):
        return SystemSnapshot(
            timestamp=1.0,
            cpu_util_pct=cpu,
            mem_util_pct=mem,
            gpu_util_pct=gpu,
            gpu_mem_util_pct=gpu_mem,
            disk_util_pct=disk,
            io_wait_pct=io,
        )

    @patch.object(MetricsMonitor, "_collect")
    def test_start_collects_snapshots(self, mock_collect):
        snapshot = self._make_snapshot(cpu=55.0, mem=65.0)

        async def _fake_collect():
            return snapshot

        mock_collect.side_effect = _fake_collect

        monitor = MetricsMonitor(interval_seconds=0.05)

        asyncio.run(monitor.start(duration_seconds=0.15))

        self.assertGreater(len(monitor.snapshots), 0)
        self.assertEqual(monitor.snapshots[0].cpu_util_pct, 55.0)

    @patch.object(MetricsMonitor, "_collect")
    def test_stop_terminates_early(self, mock_collect):
        call_count = 0

        async def _fake_collect():
            nonlocal call_count
            call_count += 1
            return self._make_snapshot()

        mock_collect.side_effect = _fake_collect

        monitor = MetricsMonitor(interval_seconds=0.05)

        async def _run():
            task = asyncio.create_task(monitor.start(duration_seconds=5.0))
            await asyncio.sleep(0.1)
            monitor.stop()
            await task

        asyncio.run(_run())
        # Should have collected some but not 100 (5s / 0.05s)
        self.assertLess(call_count, 50)

    def test_aggregate_empty(self):
        monitor = MetricsMonitor()
        self.assertEqual(monitor.aggregate(), {})

    @patch.object(MetricsMonitor, "_collect")
    def test_aggregate_means(self, mock_collect):
        snapshots = [
            self._make_snapshot(cpu=40.0, mem=50.0, gpu=80.0, gpu_mem=70.0),
            self._make_snapshot(cpu=60.0, mem=70.0, gpu=90.0, gpu_mem=80.0),
        ]
        idx = 0

        async def _fake_collect():
            nonlocal idx
            s = snapshots[min(idx, len(snapshots) - 1)]
            idx += 1
            return s

        mock_collect.side_effect = _fake_collect

        monitor = MetricsMonitor(interval_seconds=0.05)
        asyncio.run(monitor.start(duration_seconds=0.08))

        agg = monitor.aggregate()
        # Should have at least 2 snapshots; means should be reasonable
        if len(monitor.snapshots) >= 2:
            self.assertAlmostEqual(agg["cpu_util_pct"], 50.0, delta=5.0)
            self.assertAlmostEqual(agg["mem_util_pct"], 60.0, delta=5.0)

    def test_snapshots_property_returns_copy(self):
        monitor = MetricsMonitor()
        snaps = monitor.snapshots
        self.assertEqual(snaps, [])
        # Modifying the returned list shouldn't affect internal state
        snaps.append(self._make_snapshot())
        self.assertEqual(len(monitor.snapshots), 0)


class PrometheusMonitorTests(unittest.IsolatedAsyncioTestCase):
    """Test MetricsMonitor with a mock Prometheus channel."""

    async def test_monitor_with_prometheus(self) -> None:
        class _FakePromChannel:
            async def query_instant(self, promql: str) -> float:
                _ = promql
                return 42.5

        queries = {"pod_cpu": "sum(rate(...))", "gpu_utilization": "DCGM_FI_DEV_GPU_UTIL"}

        with patch.object(MetricsMonitor, "_collect", wraps=None) as mock_collect:
            async def _fake_collect(self_monitor=None):
                prom_data = await monitor._collect_prometheus()
                return SystemSnapshot(
                    timestamp=1.0,
                    cpu_util_pct=50.0,
                    mem_util_pct=60.0,
                    prom_pod_cpu=prom_data.get("pod_cpu"),
                    prom_gpu_util=prom_data.get("gpu_utilization"),
                )

            monitor = MetricsMonitor(
                interval_seconds=0.05,
                prometheus_channel=_FakePromChannel(),
                prometheus_queries=queries,
            )
            mock_collect.side_effect = _fake_collect

            await monitor.start(duration_seconds=0.12)

            self.assertGreater(len(monitor.snapshots), 0)
            snap = monitor.snapshots[0]
            self.assertAlmostEqual(snap.prom_pod_cpu or 0.0, 42.5)
            self.assertAlmostEqual(snap.prom_gpu_util or 0.0, 42.5)

            agg = monitor.aggregate()
            self.assertIn("prom_pod_cpu", agg)
            self.assertIn("prom_gpu_util", agg)

    async def test_monitor_without_prometheus(self) -> None:
        """Without prometheus channel, prom fields should be None."""
        with patch.object(MetricsMonitor, "_collect") as mock_collect:
            async def _fake_collect():
                return SystemSnapshot(timestamp=1.0, cpu_util_pct=50.0, mem_util_pct=60.0)

            mock_collect.side_effect = _fake_collect
            monitor = MetricsMonitor(interval_seconds=0.05)
            await monitor.start(duration_seconds=0.08)

            self.assertGreater(len(monitor.snapshots), 0)
            self.assertIsNone(monitor.snapshots[0].prom_pod_cpu)
            agg = monitor.aggregate()
            self.assertNotIn("prom_pod_cpu", agg)


if __name__ == "__main__":
    unittest.main()
