"""WebSocket event contracts for SRE frontend/backend integration."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field, field_validator

from sre_agent.models.common import StrictFrozenModel


class EventType(str, Enum):
    """Canonical event types pushed over WebSocket."""

    THINKING_STEP = "thinking_step"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DIAGNOSIS_CANDIDATES_READY = "diagnosis_candidates_ready"
    DIAGNOSIS_RESULT = "diagnosis_result"
    APPROVAL_REQUIRED = "approval_required"
    LOOP_START = "loop_start"
    LOOP_PROGRESS = "loop_progress"
    REMEDIATION_PROGRESS = "remediation_progress"
    PLAN_REVISED = "plan_revised"
    EXECUTION_MOCKED = "execution_mocked"
    OBSERVATION_STARTED = "observation_started"
    OBSERVATION_RESULT = "observation_result"
    ESCALATION_REQUIRED = "escalation_required"
    DIAGNOSIS_STARTED = "diagnosis_started"
    DIAGNOSIS_TRIGGERED = "diagnosis_triggered"
    ALERT = "alert"
    TOPOLOGY = "topology"
    ERROR = "error"
    DONE = "done"

    # SSE streaming events
    TOKEN_DELTA = "token_delta"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    STATE_SNAPSHOT = "state_snapshot"


class WSEvent(StrictFrozenModel):
    """Unified WebSocket event schema with backward-compatible aliases."""

    schema_version: str = "1.0"
    type: EventType
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = Field(default_factory=dict, validation_alias=AliasChoices("data", "payload"))

    @field_validator("session_id")
    @classmethod
    def _session_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id must not be blank")
        return value


__all__ = [
    "EventType",
    "WSEvent",
]
