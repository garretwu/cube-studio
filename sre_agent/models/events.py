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
    DIAGNOSIS_RESULT = "diagnosis_result"
    APPROVAL_REQUIRED = "approval_required"
    LOOP_START = "loop_start"
    LOOP_PROGRESS = "loop_progress"
    REMEDIATION_PROGRESS = "remediation_progress"
    ALERT = "alert"
    ERROR = "error"
    DONE = "done"


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

