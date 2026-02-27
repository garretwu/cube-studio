from __future__ import annotations

import unittest

from load_simulator.load.rate_limiter import TokenBucketRateLimiter


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_success(self) -> None:
        limiter = TokenBucketRateLimiter(rate_per_second=50, capacity=5)
        ok = await limiter.acquire(tokens=3, timeout_seconds=0.1)
        self.assertTrue(ok)

    async def test_acquire_timeout(self) -> None:
        limiter = TokenBucketRateLimiter(rate_per_second=1, capacity=1)
        self.assertTrue(await limiter.acquire(tokens=1, timeout_seconds=0.1))
        ok = await limiter.acquire(tokens=2, timeout_seconds=0.01)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
