"""Async metric collection helpers.

Provides :class:`MetricCollector`, a lightweight async context manager that
accumulates raw samples and exposes them for aggregation.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine


@dataclass
class RawSample:
    """A single metric observation."""

    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)


class MetricCollector:
    """Accumulates :class:`RawSample` objects from concurrent producers.

    Usage::

        collector = MetricCollector()
        collector.record("latency_ms", 42.3)
        samples = collector.get_samples("latency_ms")
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._samples: dict[str, list[RawSample]] = {}

    async def record_async(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Thread-safe async record."""
        sample = RawSample(
            name=name,
            value=value,
            timestamp=timestamp or time.time(),
            labels=labels or {},
        )
        async with self._lock:
            self._samples.setdefault(name, []).append(sample)

    def record(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Synchronous record (no lock — safe only from single-threaded contexts)."""
        sample = RawSample(
            name=name,
            value=value,
            timestamp=timestamp or time.time(),
            labels=labels or {},
        )
        self._samples.setdefault(name, []).append(sample)

    def get_samples(self, name: str) -> list[RawSample]:
        """Return all samples for *name* in insertion order."""
        return list(self._samples.get(name, []))

    def get_values(self, name: str) -> list[float]:
        """Return just the float values for *name*."""
        return [s.value for s in self._samples.get(name, [])]

    def metric_names(self) -> list[str]:
        """Return sorted list of recorded metric names."""
        return sorted(self._samples.keys())

    def to_flat_dict(self) -> dict[str, list[float]]:
        """Serialise all metrics to a plain dict of value lists."""
        return {name: self.get_values(name) for name in self.metric_names()}

    def clear(self) -> None:
        """Remove all accumulated samples."""
        self._samples.clear()


async def collect_periodic(
    collector: MetricCollector,
    name: str,
    probe: Callable[[], Coroutine[Any, Any, float]],
    interval_seconds: float = 5.0,
    duration_seconds: float = 60.0,
) -> None:
    """Run an async *probe* coroutine every *interval_seconds* and record results.

    Args:
        collector:        Target :class:`MetricCollector`.
        name:             Metric name to record under.
        probe:            Async callable that returns a float.
        interval_seconds: Polling interval.
        duration_seconds: Total collection duration before returning.
    """
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        try:
            value = await probe()
            await collector.record_async(name, value)
        except Exception:
            pass  # Don't crash the collector on probe errors
        remaining = deadline - time.monotonic()
        await asyncio.sleep(min(interval_seconds, max(0.0, remaining)))
