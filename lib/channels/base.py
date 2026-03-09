"""Shared base channel abstractions."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class SafetyViolationError(RuntimeError):
    """Raised when a forbidden action is requested."""


@dataclass
class ChannelResult:
    success: bool
    data: Any = None           # CubeStudioChannel / K8sChannel (load_simulator)
    output: str = ""           # SSH/Redfish/Switch/IPMI (fault_injector)
    error: str = ""            # both
    dry_run: bool = False      # both
    metadata: dict[str, Any] = field(default_factory=dict)  # load_simulator
    duration_ms: int = 0       # fault_injector


class BaseChannel:
    """Base channel implementing safety checks, dry-run, and WAL behavior.

    Merged from load_simulator (lib/channels) and fault_injector (lib/fchannels).
    NOT an ABC so that load_simulator subclasses (which don't override _execute_impl
    as abstract) continue to work.
    """

    def __init__(
        self,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
    ) -> None:
        self.dry_run = dry_run
        self.wal = wal
        self.guard = guard

    async def execute(
        self,
        action: str,
        params: dict[str, Any],
        *,
        recovery_action: str | None = None,
        recovery_params: dict[str, Any] | None = None,
        fault_id: str | None = None,
        target: str = "",
    ) -> ChannelResult:
        start_time = time.monotonic()

        # 1. Safety guard check (fault_injector path)
        if self.guard:
            try:
                self._check_safety(action, params)
            except SafetyViolationError as e:
                logger.error("Safety check failed: %s", e)
                return ChannelResult(
                    success=False,
                    error=str(e),
                    dry_run=self.dry_run,
                )

        # 2. Forbidden-action check (load_simulator path)
        if self._is_forbidden(action, params):
            raise SafetyViolationError(f"{action} is forbidden")

        # 3. WAL record (before execution)
        if recovery_action and self.wal is not None:
            record = getattr(self.wal, "record", None)
            if callable(record):
                if fault_id:
                    # fchannels-style structured WAL record
                    record(
                        fault_id=fault_id,
                        channel=self.channel_name,
                        target=target,
                        inject_action=action,
                        inject_params=params,
                        recover_action=recovery_action,
                        recover_params=recovery_params or {},
                    )
                else:
                    # channels-style simple WAL record
                    record(
                        action=action,
                        recovery_action=recovery_action,
                        recovery_params=recovery_params or {},
                    )

        # 4. Dry-run shortcut
        if self.dry_run:
            logger.info("[DRY-RUN] %s.%s: %s", self.channel_name, action, params)
            return ChannelResult(
                success=True,
                dry_run=True,
                data={"action": action, "params": params},
                output="[DRY-RUN] 操作未实际执行",
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

        # 5. Actual execution
        try:
            result = await self._execute_impl(action, params)
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            return result
        except Exception as e:
            logger.error("Execution failed: %s", e)
            return ChannelResult(
                success=False,
                error=str(e),
                dry_run=False,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )

    def _is_forbidden(self, action: str, params: dict[str, Any]) -> bool:
        """Hook for load_simulator-style forbidden checks."""
        _ = action, params
        return False

    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        """Hook for fault_injector-style safety guard checks (subclass override)."""
        pass

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        raise NotImplementedError

    @property
    def channel_name(self) -> str:
        return self.__class__.__name__.replace("Channel", "").lower()
