from __future__ import annotations

import unittest

from load_simulator.load.prompt_pool import PromptPool


class PromptPoolTests(unittest.TestCase):
    def test_pool_build_and_sample(self) -> None:
        pool = PromptPool(size=20, mode="mixed", seed=3)
        self.assertEqual(len(pool), 20)
        prompt = pool.sample()
        self.assertIn("Analyze system health", prompt)
        self.assertGreater(len(prompt.split()), 10)

    def test_invalid_size(self) -> None:
        with self.assertRaises(ValueError):
            PromptPool(size=0)


if __name__ == "__main__":
    unittest.main()
