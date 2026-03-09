"""Tests for load_simulator.metrics.collector."""
from __future__ import annotations

import asyncio
import unittest

from load_simulator.metrics.collector import MetricCollector, RawSample, collect_periodic


class RawSampleTests(unittest.TestCase):
    def test_defaults(self):
        s = RawSample(name="cpu", value=42.0)
        self.assertEqual(s.name, "cpu")
        self.assertEqual(s.value, 42.0)
        self.assertIsInstance(s.timestamp, float)
        self.assertEqual(s.labels, {})


class MetricCollectorTests(unittest.TestCase):
    def test_record_and_get_values(self):
        mc = MetricCollector()
        mc.record("latency", 10.0)
        mc.record("latency", 20.0)
        mc.record("throughput", 100.0)
        self.assertEqual(mc.get_values("latency"), [10.0, 20.0])
        self.assertEqual(mc.get_values("throughput"), [100.0])
        self.assertEqual(mc.get_values("nonexistent"), [])

    def test_get_samples(self):
        mc = MetricCollector()
        mc.record("x", 5.0, labels={"host": "a"})
        samples = mc.get_samples("x")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].value, 5.0)
        self.assertEqual(samples[0].labels, {"host": "a"})

    def test_metric_names(self):
        mc = MetricCollector()
        mc.record("b", 1.0)
        mc.record("a", 2.0)
        self.assertEqual(mc.metric_names(), ["a", "b"])

    def test_to_flat_dict(self):
        mc = MetricCollector()
        mc.record("x", 1.0)
        mc.record("x", 2.0)
        mc.record("y", 3.0)
        d = mc.to_flat_dict()
        self.assertEqual(d, {"x": [1.0, 2.0], "y": [3.0]})

    def test_clear(self):
        mc = MetricCollector()
        mc.record("a", 1.0)
        mc.clear()
        self.assertEqual(mc.metric_names(), [])

    def test_record_async(self):
        mc = MetricCollector()

        async def _run():
            await mc.record_async("cpu", 50.0)
            await mc.record_async("cpu", 60.0)

        asyncio.run(_run())
        self.assertEqual(mc.get_values("cpu"), [50.0, 60.0])


class CollectPeriodicTests(unittest.TestCase):
    def test_collect_periodic_accumulates_samples(self):
        mc = MetricCollector()
        call_count = 0

        async def probe() -> float:
            nonlocal call_count
            call_count += 1
            return float(call_count)

        async def _run():
            await collect_periodic(
                mc, "test_metric", probe,
                interval_seconds=0.05,
                duration_seconds=0.2,
            )

        asyncio.run(_run())
        values = mc.get_values("test_metric")
        self.assertGreater(len(values), 0)
        self.assertEqual(values[0], 1.0)


if __name__ == "__main__":
    unittest.main()
