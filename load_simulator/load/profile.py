"""Deterministic load-profile primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Stage:
    """Single stage of a stepped profile."""

    value: float
    duration_seconds: int


class LoadProfile:
    """A time-indexed load profile used by agents."""

    def __init__(
        self,
        profile_type: str,
        *,
        stages: list[Stage] | None = None,
        constant_value: float | None = None,
        total_duration_seconds: int | None = None,
        base_value: float | None = None,
        spike_value: float | None = None,
        spike_start_second: int | None = None,
        spike_duration_seconds: int | None = None,
    ) -> None:
        self.profile_type = profile_type
        self.stages = stages or []
        self.constant_value = constant_value
        self.total_duration_seconds = total_duration_seconds or 0
        self.base_value = base_value
        self.spike_value = spike_value
        self.spike_start_second = spike_start_second
        self.spike_duration_seconds = spike_duration_seconds

    @classmethod
    def constant(cls, value: float, duration_seconds: int) -> "LoadProfile":
        return cls(
            "constant",
            constant_value=value,
            total_duration_seconds=duration_seconds,
        )

    @classmethod
    def stepped(cls, stages: Iterable[Stage]) -> "LoadProfile":
        stage_list = list(stages)
        return cls(
            "stepped",
            stages=stage_list,
            total_duration_seconds=sum(s.duration_seconds for s in stage_list),
        )

    @classmethod
    def spike(
        cls,
        *,
        base_value: float,
        spike_value: float,
        spike_start_second: int,
        spike_duration_seconds: int,
        total_duration_seconds: int,
    ) -> "LoadProfile":
        return cls(
            "spike",
            base_value=base_value,
            spike_value=spike_value,
            spike_start_second=spike_start_second,
            spike_duration_seconds=spike_duration_seconds,
            total_duration_seconds=total_duration_seconds,
        )

    def value_at(self, second: int) -> float:
        """Return load value at the given second offset from session start."""
        sec = max(0, second)

        if self.profile_type == "constant":
            return float(self.constant_value or 0.0)

        if self.profile_type == "stepped":
            elapsed = 0
            for stage in self.stages:
                elapsed += stage.duration_seconds
                if sec < elapsed:
                    return float(stage.value)
            return float(self.stages[-1].value if self.stages else 0.0)

        if self.profile_type == "spike":
            start = self.spike_start_second or 0
            dur = self.spike_duration_seconds or 0
            if start <= sec < start + dur:
                return float(self.spike_value or 0.0)
            return float(self.base_value or 0.0)

        raise ValueError(f"Unsupported profile type: {self.profile_type}")

    def timeline(self) -> list[tuple[int, float]]:
        """Return `(second, value)` points for the full profile duration."""
        return [(t, self.value_at(t)) for t in range(max(0, self.total_duration_seconds))]
