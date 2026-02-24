"""Session state tracking for the Load Simulator."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SessionState(str, Enum):
    """Lifecycle states of a load-test session."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SessionEvent:
    """A single lifecycle event recorded during a session."""

    timestamp: float
    event_type: str  # e.g. "agent_started", "agent_finished", "error"
    agent: Optional[str] = None
    detail: str = ""


@dataclass
class SessionTracker:
    """Mutable session state container used by the orchestrator.

    Tracks the lifecycle of a single load-test session, including start/end
    times, per-agent states, and a chronological event log.
    """

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    state: SessionState = SessionState.CREATED
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    agent_states: dict[str, str] = field(default_factory=dict)
    events: list[SessionEvent] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Lifecycle helpers
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Mark the session as running."""
        self.state = SessionState.RUNNING
        self.start_time = time.time()
        self._log("session_started")

    def complete(self) -> None:
        """Mark the session as successfully completed."""
        self.state = SessionState.COMPLETED
        self.end_time = time.time()
        self._log("session_completed")

    def fail(self, reason: str = "") -> None:
        """Mark the session as failed."""
        self.state = SessionState.FAILED
        self.end_time = time.time()
        self._log("session_failed", detail=reason)

    def cancel(self) -> None:
        """Mark the session as cancelled (e.g. KeyboardInterrupt)."""
        self.state = SessionState.CANCELLED
        self.end_time = time.time()
        self._log("session_cancelled")

    # ------------------------------------------------------------------ #
    # Agent helpers
    # ------------------------------------------------------------------ #

    def agent_started(self, agent_name: str) -> None:
        """Record that an agent has started."""
        self.agent_states[agent_name] = "running"
        self._log("agent_started", agent=agent_name)

    def agent_finished(self, agent_name: str, status: str) -> None:
        """Record that an agent has finished with *status*."""
        self.agent_states[agent_name] = status
        self._log("agent_finished", agent=agent_name, detail=status)

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def elapsed_seconds(self) -> float:
        """Elapsed wall-clock seconds since :meth:`start`."""
        if self.start_time is None:
            return 0.0
        end = self.end_time or time.time()
        return end - self.start_time

    @property
    def is_terminal(self) -> bool:
        """True if the session has reached a terminal state."""
        return self.state in (
            SessionState.COMPLETED,
            SessionState.FAILED,
            SessionState.CANCELLED,
        )

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _log(self, event_type: str, agent: Optional[str] = None, detail: str = "") -> None:
        self.events.append(
            SessionEvent(
                timestamp=time.time(),
                event_type=event_type,
                agent=agent,
                detail=detail,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the tracker to a plain dict."""
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "elapsed_seconds": self.elapsed_seconds,
            "agent_states": dict(self.agent_states),
            "event_count": len(self.events),
        }
