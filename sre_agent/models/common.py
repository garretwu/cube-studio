"""Common contracts shared by all SRE agent model modules."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, ClassVar, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class StrictFrozenModel(BaseModel):
    """Base model config for long-term stable shared contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class ErrorCode(str, Enum):
    """Unified error codes shared across SRE subsystems."""

    DIAGNOSIS_TIMEOUT = "DIAG_TIMEOUT"
    DIAGNOSIS_LLM_ERROR = "DIAG_LLM_ERR"
    DIAGNOSIS_NO_RESULT = "DIAG_NO_RESULT"

    REMEDIATION_PLAN_INVALID = "REM_PLAN_INVALID"
    REMEDIATION_PLAN_VERSION_OUTDATED = "plan_version_outdated"
    REMEDIATION_APPROVAL_DENIED = "REM_APPROVAL_DENIED"
    REMEDIATION_APPROVAL_TIMEOUT = "REM_APPROVAL_TIMEOUT"
    REMEDIATION_EXECUTION_FAILED = "REM_EXEC_FAILED"
    REMEDIATION_STALE_OR_MISMATCHED_PID = "stale_or_mismatched_pid"
    REMEDIATION_ROLLBACK_FAILED = "REM_ROLLBACK_FAILED"
    REMEDIATION_BLOCKED = "REM_BLOCKED"

    RESOURCE_LOCKED = "RES_LOCKED"
    ALERT_DUPLICATE = "ALERT_DUPLICATE"
    CONCURRENT_LIMIT = "CONCURRENT_LIMIT"

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"

    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class SafetyLevel(str, Enum):
    """Canonical safety levels for tool/action execution control."""

    READONLY = "readonly"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def _missing_(cls, value: object) -> SafetyLevel | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        normalized = LEGACY_SAFETY_LEVEL_MAP.get(normalized, normalized)
        for member in cls:
            if member.value == normalized:
                return member
        return None

    @classmethod
    def from_legacy(cls, value: str) -> SafetyLevel:
        """Convert legacy safety-level strings to canonical enum values."""
        return cls(value)


class SREError(StrictFrozenModel):
    """Unified structured error payload."""

    code: ErrorCode
    message: str = Field(min_length=1)
    details: dict[str, Any] | None = None
    trace_id: str | None = None

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class SREResponse(StrictFrozenModel, Generic[T]):
    """Unified response envelope for typed success/error outcomes."""

    success: bool
    data: T | None = None
    error: SREError | None = None
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("trace_id")
    @classmethod
    def _trace_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trace_id must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_outcome_consistency(self) -> SREResponse[T]:
        if not self.success and self.error is None:
            raise ValueError("failed responses must include error")
        # Compatibility: duplicate alerts may be represented as success + structured error
        # so callers can continue an existing session flow without treating it as hard failure.
        if self.success and self.error is not None and self.error.code != ErrorCode.ALERT_DUPLICATE:
            raise ValueError(
                "successful responses may include error only for ALERT_DUPLICATE compatibility flow"
            )
        return self


LEGACY_SAFETY_LEVEL_MAP: ClassVar[dict[str, str]] = {
    "read_only": "readonly",
    "write_confirm": "high",
    "write_blocked": "critical",
}


__all__ = [
    "StrictFrozenModel",
    "ErrorCode",
    "SafetyLevel",
    "SREError",
    "SREResponse",
]
