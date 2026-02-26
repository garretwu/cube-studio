"""Shared base channel abstractions."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class SafetyViolationError(RuntimeError):
    """Raised when a forbidden action is requested."""


@dataclass
class ChannelResult:
    success: bool
    data: Any = None
    error: str | None = None
    dry_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseChannel:
    """Base channel implementing safety checks and dry-run behavior."""

    def __init__(self, dry_run: bool = False, wal: Any | None = None) -> None:
        self.dry_run = dry_run
        self.wal = wal

    async def execute(
        self,
        action: str,
        params: dict[str, Any],
        *,
        recovery_action: str | None = None,
        recovery_params: dict[str, Any] | None = None,
    ) -> ChannelResult:
        if self._is_forbidden(action, params):
            raise SafetyViolationError(f"{action} is forbidden")

        if recovery_action and self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                record(action=action, recovery_action=recovery_action, recovery_params=recovery_params or {})

        if self.dry_run:
            return ChannelResult(
                success=True,
                dry_run=True,
                data={"action": action, "params": params},
            )

        return await self._execute_impl(action, params)

    def _is_forbidden(self, action: str, params: dict[str, Any]) -> bool:
        _ = action, params
        return False

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        raise NotImplementedError
