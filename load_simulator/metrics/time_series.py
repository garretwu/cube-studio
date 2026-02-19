"""Time-series data structure for metric samples."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T", int, float)


@dataclass
class Sample(Generic[T]):
    """A single timestamped metric sample."""

    timestamp: float  # Unix epoch seconds (float for sub-second precision)
    value: T

    @classmethod
    def now(cls, value: T) -> "Sample[T]":
        """Create a sample stamped at the current wall-clock time."""
        return cls(timestamp=time.time(), value=value)


@dataclass
class TimeSeries(Generic[T]):
    """Ordered collection of :class:`Sample` objects for a named metric.

    Samples are appended in chronological order; no sorting is performed.
    """

    name: str
    unit: str = ""
    samples: list[Sample[T]] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # Mutation helpers
    # ------------------------------------------------------------------ #

    def append(self, value: T, timestamp: float | None = None) -> None:
        """Append a new sample.

        Args:
            value:     Metric value.
            timestamp: Unix epoch seconds. Defaults to :func:`time.time`.
        """
        ts = timestamp if timestamp is not None else time.time()
        self.samples.append(Sample(timestamp=ts, value=value))

    def extend(self, values: list[T], base_timestamp: float | None = None) -> None:
        """Append multiple values with evenly-spaced virtual timestamps.

        Args:
            values:         List of metric values.
            base_timestamp: Starting timestamp. Defaults to :func:`time.time`.
        """
        if not values:
            return
        t = base_timestamp if base_timestamp is not None else time.time()
        for v in values:
            self.samples.append(Sample(timestamp=t, value=v))
            t += 1.0

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #

    def values(self) -> list[T]:
        """Return all raw values in insertion order."""
        return [s.value for s in self.samples]

    def timestamps(self) -> list[float]:
        """Return all timestamps in insertion order."""
        return [s.timestamp for s in self.samples]

    def slice(self, start: float, end: float) -> "TimeSeries[T]":
        """Return a new TimeSeries containing only samples within [start, end].

        Args:
            start: Inclusive lower bound (Unix epoch seconds).
            end:   Inclusive upper bound (Unix epoch seconds).

        Returns:
            A new :class:`TimeSeries` with a subset of samples.
        """
        filtered = [s for s in self.samples if start <= s.timestamp <= end]
        return TimeSeries(name=self.name, unit=self.unit, samples=filtered)

    def __len__(self) -> int:
        return len(self.samples)

    def __repr__(self) -> str:
        return (
            f"TimeSeries(name={self.name!r}, unit={self.unit!r}, "
            f"samples={len(self.samples)})"
        )


class TimeSeriesStore:
    """Container that holds multiple named :class:`TimeSeries` objects."""

    def __init__(self) -> None:
        self._series: dict[str, TimeSeries] = {}

    def get_or_create(self, name: str, unit: str = "") -> TimeSeries:
        """Return the series for *name*, creating it if absent."""
        if name not in self._series:
            self._series[name] = TimeSeries(name=name, unit=unit)
        return self._series[name]

    def append(self, name: str, value: float, unit: str = "", timestamp: float | None = None) -> None:
        """Append a single sample to the named series."""
        self.get_or_create(name, unit).append(value, timestamp)

    def all_series(self) -> dict[str, TimeSeries]:
        """Return a shallow copy of the internal series dict."""
        return dict(self._series)

    def to_dict(self) -> dict[str, list[float]]:
        """Serialise all series to a plain dict of value lists."""
        return {name: ts.values() for name, ts in self._series.items()}
