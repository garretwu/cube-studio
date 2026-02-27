"""Tests for load_simulator.metrics.aggregator."""
from __future__ import annotations

import unittest

from load_simulator.metrics.aggregator import compute_percentiles, compute_rate, percentile


class PercentileTests(unittest.TestCase):
    def test_empty_data(self):
        self.assertEqual(percentile([], 50), 0.0)

    def test_single_element(self):
        self.assertEqual(percentile([42.0], 50), 42.0)
        self.assertEqual(percentile([42.0], 99), 42.0)

    def test_known_values(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        self.assertEqual(percentile(data, 50), 5.0)
        self.assertEqual(percentile(data, 100), 10.0)

    def test_unsorted_input(self):
        data = [5.0, 1.0, 3.0, 2.0, 4.0]
        p50 = percentile(data, 50)
        self.assertEqual(p50, 3.0)


class ComputePercentilesTests(unittest.TestCase):
    def test_empty(self):
        result = compute_percentiles([])
        self.assertEqual(result["p50"], 0.0)
        self.assertEqual(result["p95"], 0.0)
        self.assertEqual(result["p99"], 0.0)
        self.assertEqual(result["min"], 0.0)
        self.assertEqual(result["max"], 0.0)
        self.assertEqual(result["mean"], 0.0)

    def test_with_data(self):
        data = list(range(1, 101))  # 1..100
        result = compute_percentiles([float(x) for x in data])
        self.assertEqual(result["min"], 1.0)
        self.assertEqual(result["max"], 100.0)
        self.assertAlmostEqual(result["mean"], 50.5)
        self.assertGreater(result["p95"], result["p50"])
        self.assertGreater(result["p99"], result["p95"])

    def test_all_same_values(self):
        result = compute_percentiles([5.0] * 100)
        self.assertEqual(result["p50"], 5.0)
        self.assertEqual(result["p99"], 5.0)
        self.assertEqual(result["mean"], 5.0)


class ComputeRateTests(unittest.TestCase):
    def test_normal(self):
        self.assertAlmostEqual(compute_rate(100, 10.0), 10.0)

    def test_zero_elapsed(self):
        self.assertEqual(compute_rate(100, 0.0), 0.0)

    def test_negative_elapsed(self):
        self.assertEqual(compute_rate(100, -1.0), 0.0)

    def test_zero_count(self):
        self.assertAlmostEqual(compute_rate(0, 10.0), 0.0)


if __name__ == "__main__":
    unittest.main()
