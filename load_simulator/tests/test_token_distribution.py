from __future__ import annotations

import unittest

from load_simulator.load.token_distribution import TokenDistribution


class TokenDistributionTests(unittest.TestCase):
    def test_short_range(self) -> None:
        dist = TokenDistribution(mode="short", seed=1)
        values = dist.sample_many(50)
        self.assertTrue(all(10 <= v <= 50 for v in values))

    def test_mixed_mode(self) -> None:
        dist = TokenDistribution(mode="mixed", mixed_weights={"short": 1, "medium": 1, "long": 1}, seed=2)
        values = dist.sample_many(120)
        self.assertGreaterEqual(min(values), 10)
        self.assertLessEqual(max(values), 2000)

    def test_invalid_mode(self) -> None:
        with self.assertRaises(ValueError):
            TokenDistribution(mode="x")


if __name__ == "__main__":
    unittest.main()
