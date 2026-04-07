"""Shared alert filtering helpers for temporary alert suppressions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypeVar

from sre_agent.models.alert import Alert

DEFAULT_BLOCKED_ALERT_NAMES: tuple[str, ...] = ()


def normalize_alert_name(value: str | None) -> str:
    return str(value or "").strip().lower()


def build_blocked_alert_name_set(values: Iterable[str] | None) -> set[str]:
    source = DEFAULT_BLOCKED_ALERT_NAMES if values is None else values
    normalized = {normalize_alert_name(item) for item in source}
    return {item for item in normalized if item}


def is_blocked_alert(
    alert: Alert | Mapping[str, Any],
    *,
    blocked_names: set[str],
) -> bool:
    if not blocked_names:
        return False
    candidates = _extract_alert_name_candidates(alert)
    return any(candidate in blocked_names for candidate in candidates)


T = TypeVar("T", bound=Alert)


def filter_blocked_alerts(alerts: Iterable[T], *, blocked_names: set[str]) -> list[T]:
    if not blocked_names:
        return list(alerts)
    return [
        alert
        for alert in alerts
        if not is_blocked_alert(alert, blocked_names=blocked_names)
    ]


def _extract_alert_name_candidates(alert: Alert | Mapping[str, Any]) -> list[str]:
    if isinstance(alert, Alert):
        candidates = [
            normalize_alert_name(alert.alert_name),
            normalize_alert_name(alert.labels.get("alertname")),
        ]
        return [item for item in candidates if item]

    labels = alert.get("labels")
    label_alert_name = ""
    if isinstance(labels, Mapping):
        label_alert_name = normalize_alert_name(str(labels.get("alertname", "")))
    candidates = [
        normalize_alert_name(str(alert.get("alert_name", ""))),
        label_alert_name,
    ]
    return [item for item in candidates if item]


__all__ = [
    "DEFAULT_BLOCKED_ALERT_NAMES",
    "build_blocked_alert_name_set",
    "filter_blocked_alerts",
    "is_blocked_alert",
    "normalize_alert_name",
]
