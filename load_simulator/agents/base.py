"""Base agent abstractions for the Load Simulator."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentResult:
    """Result returned by every agent after completing its load run.

    Attributes:
        name:       Agent identifier, e.g. ``"inference"``.
        status:     ``"success"`` or ``"error"``.
        metrics:    Flat dict of computed scalar metrics (rates, percentiles …).
        start_time: Wall-clock UNIX timestamp at which the agent started.
        end_time:   Wall-clock UNIX timestamp at which the agent finished.
        errors:     List of error messages encountered during the run.
        raw:        Optional extra payload (time-series data, per-request logs …).
    """

    name: str
    status: str  # "success" | "error"
    metrics: dict[str, Any] = field(default_factory=dict)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        """Elapsed wall-clock seconds for the run."""
        if self.start_time is not None and self.end_time is not None:
            return self.end_time - self.start_time
        return 0.0

    @classmethod
    def error_result(cls, name: str, message: str, start_time: float | None = None) -> "AgentResult":
        """Convenience constructor for a failed run."""
        return cls(
            name=name,
            status="error",
            errors=[message],
            start_time=start_time,
            end_time=time.time(),
        )


class BaseAgent(ABC):
    """Abstract base class for all load-test agents.

    Subclasses must implement :meth:`run`.  The base class provides a
    :meth:`_timed_run` helper that wraps :meth:`_execute` with timing and
    top-level error handling so subclasses only need to implement
    :meth:`_execute`.
    """

    #: Name used in results and logs.  Override in each subclass.
    agent_name: str = "base"

    @abstractmethod
    async def run(self, duration_seconds: int) -> AgentResult:
        """Execute the load test for *duration_seconds* wall-clock seconds.

        Args:
            duration_seconds: How long to generate load.

        Returns:
            An :class:`AgentResult` with metrics and any errors.
        """

    # ------------------------------------------------------------------ #
    # Protected helpers
    # ------------------------------------------------------------------ #

    async def _timed_run(self, duration_seconds: int) -> AgentResult:
        """Run :meth:`_execute`, wrapping it in timing + top-level error catching.

        If :meth:`_execute` raises an unhandled exception the agent returns an
        :class:`AgentResult` with ``status="error"`` instead of propagating.
        """
        start = time.time()
        try:
            result = await self._execute(duration_seconds)
            result.start_time = result.start_time or start
            result.end_time = result.end_time or time.time()
            return result
        except Exception as exc:  # noqa: BLE001
            return AgentResult.error_result(
                name=self.agent_name,
                message=f"Unhandled exception: {exc}",
                start_time=start,
            )

    async def _execute(self, duration_seconds: int) -> AgentResult:  # noqa: D401
        """Override this instead of :meth:`run` when you want free timing/error wrap."""
        raise NotImplementedError
