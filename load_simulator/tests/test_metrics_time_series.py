"""Tests for load_simulator.metrics.time_series."""
from __future__ import annotations

import time
import unittest

from load_simulator.metrics.time_series import Sample, TimeSeries, TimeSeriesStore


class SampleTests(unittest.TestCase):
    def test_now_creates_sample_with_current_time(self):
        before = time.time()
        sample = Sample.now(42.0)
        after = time.time()
        self.assertEqual(sample.value, 42.0)
        self.assertGreaterEqual(sample.timestamp, before)
        self.assertLessEqual(sample.timestamp, after)

    def test_explicit_timestamp(self):
        sample = Sample(timestamp=1000.0, value=7)
        self.assertEqual(sample.timestamp, 1000.0)
        self.assertEqual(sample.value, 7)


class TimeSeriesTests(unittest.TestCase):
    def test_append_and_values(self):
        ts = TimeSeries(name="latency", unit="ms")
        ts.append(10.0, timestamp=1.0)
        ts.append(20.0, timestamp=2.0)
        ts.append(30.0, timestamp=3.0)
        self.assertEqual(ts.values(), [10.0, 20.0, 30.0])
        self.assertEqual(ts.timestamps(), [1.0, 2.0, 3.0])
        self.assertEqual(len(ts), 3)

    def test_extend(self):
        ts = TimeSeries(name="cpu", unit="%")
        ts.extend([1.0, 2.0, 3.0], base_timestamp=100.0)
        self.assertEqual(ts.values(), [1.0, 2.0, 3.0])
        self.assertEqual(ts.timestamps(), [100.0, 101.0, 102.0])

    def test_extend_empty(self):
        ts = TimeSeries(name="cpu")
        ts.extend([])
        self.assertEqual(len(ts), 0)

    def test_slice(self):
        ts = TimeSeries(name="mem")
        for i in range(10):
            ts.append(float(i), timestamp=float(i))
        sliced = ts.slice(3.0, 6.0)
        self.assertEqual(sliced.values(), [3.0, 4.0, 5.0, 6.0])
        self.assertEqual(sliced.name, "mem")

    def test_repr(self):
        ts = TimeSeries(name="test", unit="ms")
        ts.append(1.0)
        r = repr(ts)
        self.assertIn("test", r)
        self.assertIn("ms", r)
        self.assertIn("1", r)


class TimeSeriesStoreTests(unittest.TestCase):
    def test_get_or_create(self):
        store = TimeSeriesStore()
        ts1 = store.get_or_create("latency", "ms")
        ts2 = store.get_or_create("latency", "ms")
        self.assertIs(ts1, ts2)

    def test_append_and_to_dict(self):
        store = TimeSeriesStore()
        store.append("a", 1.0)
        store.append("a", 2.0)
        store.append("b", 10.0)
        d = store.to_dict()
        self.assertEqual(d["a"], [1.0, 2.0])
        self.assertEqual(d["b"], [10.0])

    def test_all_series(self):
        store = TimeSeriesStore()
        store.append("x", 1.0)
        store.append("y", 2.0)
        series = store.all_series()
        self.assertIn("x", series)
        self.assertIn("y", series)


if __name__ == "__main__":
    unittest.main()
