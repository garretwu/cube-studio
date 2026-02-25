"""Preflight result evaluation helpers."""
from __future__ import annotations

from typing import Any


def failed_checks(preflight: dict[str, dict[str, Any]] | None) -> list[str]:
    """Return names of checks that explicitly failed (`ok is False`)."""
    if not preflight:
        return []
    return [name for name, item in preflight.items() if item.get("ok") is False]


def has_blocking_failure(preflight: dict[str, dict[str, Any]] | None) -> bool:
    """Whether preflight contains at least one blocking failure."""
    return len(failed_checks(preflight)) > 0
