"""Alert-domain models for the shared SRE contract layer."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from sre_agent.models.common import StrictFrozenModel


class AlertSeverity(str, Enum):
    """Canonical alert severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class AlertStatus(str, Enum):
    """Canonical alert state values."""

    FIRING = "firing"
    RESOLVED = "resolved"
    SILENCED = "silenced"

    @classmethod
    def _missing_(cls, value: object) -> AlertStatus | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if normalized == "suppressed":
            normalized = "silenced"
        for member in cls:
            if member.value == normalized:
                return member
        return None


class Alert(StrictFrozenModel):
    """Unified alert payload model with Alertmanager compatibility aliases."""

    alert_name: str = Field(validation_alias=AliasChoices("alert_name", "alertname"))
    severity: AlertSeverity
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(validation_alias=AliasChoices("starts_at", "startsAt"))
    ends_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("ends_at", "endsAt"),
    )
    fingerprint: str
    status: AlertStatus = AlertStatus.FIRING
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _compat_populate_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        labels = normalized.get("labels", {})

        if isinstance(labels, dict):
            if "alert_name" not in normalized and "alertname" not in normalized:
                if isinstance(labels.get("alertname"), str):
                    normalized["alert_name"] = labels["alertname"]
            if "severity" not in normalized and isinstance(labels.get("severity"), str):
                normalized["severity"] = labels["severity"]

        annotations = normalized.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        for key in ("summary", "description"):
            value = normalized.pop(key, None)
            if isinstance(value, str) and key not in annotations:
                annotations[key] = value
        normalized["annotations"] = annotations

        status = normalized.get("status")
        if isinstance(status, dict):
            normalized["status"] = (
                status.get("state")
                or status.get("status")
                or status.get("value")
                or AlertStatus.FIRING.value
            )
        return normalized

    @field_validator("alert_name", "fingerprint")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_time_order(self) -> Alert:
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("ends_at must be greater than or equal to starts_at")
        return self

    @property
    def summary(self) -> str | None:
        value = self.annotations.get("summary")
        return value if isinstance(value, str) else None

    @property
    def description(self) -> str | None:
        value = self.annotations.get("description")
        return value if isinstance(value, str) else None


class AlertRule(StrictFrozenModel):
    """Normalized alerting-rule model (Prometheus-compatible payloads)."""

    name: str = Field(validation_alias=AliasChoices("name", "alert"))
    query: str | None = Field(default=None, validation_alias=AliasChoices("query", "expr"))
    duration: str | None = Field(
        default=None,
        validation_alias=AliasChoices("duration", "for"),
    )
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    state: str | None = None
    health: str | None = None
    group: str | None = None
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_mappings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        labels = normalized.get("labels")
        if not isinstance(labels, dict):
            labels = {}
        annotations = normalized.get("annotations")
        if not isinstance(annotations, dict):
            annotations = {}
        normalized["labels"] = {
            str(k): str(v)
            for k, v in labels.items()
            if isinstance(k, str)
        }
        normalized["annotations"] = {
            str(k): str(v)
            for k, v in annotations.items()
            if isinstance(k, str)
        }
        return normalized

    @field_validator("name")
    @classmethod
    def _rule_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value


__all__ = [
    "AlertSeverity",
    "AlertStatus",
    "Alert",
    "AlertRule",
]
