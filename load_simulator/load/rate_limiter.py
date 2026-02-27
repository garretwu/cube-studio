"""Async token-bucket rate limiter."""
from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """Deterministic token-bucket limiter for async workloads."""

    def __init__(self, rate_per_second: float, capacity: float | None = None) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        self._rate = float(rate_per_second)
        self._capacity = float(capacity if capacity is not None else rate_per_second)
        if self._capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self, now: float) -> None:
        delta = max(0.0, now - self._updated_at)
        self._updated_at = now
        self._tokens = min(self._capacity, self._tokens + delta * self._rate)

    async def acquire(self, tokens: float = 1.0, timeout_seconds: float | None = None) -> bool:
        """Acquire `tokens`, optionally waiting up to `timeout_seconds`."""
        if tokens <= 0:
            return True

        start = time.monotonic()
        while True:
            async with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                missing = tokens - self._tokens
                wait_for = missing / self._rate

            if timeout_seconds is not None and (time.monotonic() - start + wait_for) > timeout_seconds:
                return False

            await asyncio.sleep(wait_for)
