"""
Watchdog - Auto-recovery watchdog for fault injection.

Implements automatic fault recovery after timeout.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class Watchdog:
    """
    Auto-recovery watchdog.
    
    Automatically triggers recovery after a specified timeout.
    Ensures faults are not left active indefinitely.
    """
    
    def __init__(
        self,
        timeout_seconds: int,
        recovery_callback: Callable[[], Any],
        name: str = "watchdog",
    ):
        """
        Initialize the watchdog.
        
        Args:
            timeout_seconds: Timeout in seconds before auto-recovery
            recovery_callback: Async callback to execute for recovery
            name: Watchdog name for logging
        """
        self.timeout_seconds = timeout_seconds
        self.recovery_callback = recovery_callback
        self.name = name
        self._task: Optional[asyncio.Task] = None
        self._cancelled = False
        self._started_at: Optional[float] = None
    
    async def start(self) -> None:
        """Start the watchdog timer."""
        if self._task is not None and not self._task.done():
            logger.warning(f"{self.name}: Watchdog already running")
            return
        
        self._cancelled = False
        self._started_at = asyncio.get_event_loop().time()
        self._task = asyncio.create_task(self._run())
        logger.info(f"{self.name}: Watchdog started (timeout={self.timeout_seconds}s)")
    
    async def _run(self) -> None:
        """Internal watchdog loop."""
        try:
            await asyncio.sleep(self.timeout_seconds)
            
            if not self._cancelled:
                logger.warning(
                    f"{self.name}: Timeout ({self.timeout_seconds}s) reached, "
                    "triggering auto-recovery"
                )
                await self.recovery_callback()
        except asyncio.CancelledError:
            logger.debug(f"{self.name}: Watchdog cancelled")
            raise
    
    def cancel(self) -> None:
        """Cancel the watchdog timer."""
        self._cancelled = True
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info(f"{self.name}: Watchdog cancelled")
    
    def reset(self) -> None:
        """Reset the watchdog timer (restart countdown)."""
        self.cancel()
        self._cancelled = False
        self._task = asyncio.create_task(self._run())
        logger.debug(f"{self.name}: Watchdog reset")
    
    @property
    def is_running(self) -> bool:
        """Check if the watchdog is running."""
        return self._task is not None and not self._task.done() and not self._cancelled
    
    @property
    def elapsed_seconds(self) -> Optional[float]:
        """Get elapsed time since watchdog started."""
        if self._started_at is None:
            return None
        return asyncio.get_event_loop().time() - self._started_at
    
    @property
    def remaining_seconds(self) -> Optional[float]:
        """Get remaining time before timeout."""
        elapsed = self.elapsed_seconds
        if elapsed is None:
            return None
        return max(0, self.timeout_seconds - elapsed)


class FaultWatchdog(Watchdog):
    """
    Specialized watchdog for fault injection sessions.
    
    Integrates with RollbackJournal for automatic recovery.
    """
    
    def __init__(
        self,
        timeout_seconds: int,
        rollback_journal: Any,
        session_id: str = "",
    ):
        """
        Initialize the fault watchdog.
        
        Args:
            timeout_seconds: Timeout in seconds
            rollback_journal: RollbackJournal instance for recovery
            session_id: Session ID for logging
        """
        self.rollback_journal = rollback_journal
        self.session_id = session_id
        
        super().__init__(
            timeout_seconds=timeout_seconds,
            recovery_callback=self._auto_recover,
            name=f"fault-watchdog-{session_id}" if session_id else "fault-watchdog",
        )
    
    async def _auto_recover(self) -> None:
        """Execute auto-recovery via rollback journal."""
        logger.warning(
            f"Session {self.session_id}: Auto-recovery triggered after "
            f"{self.timeout_seconds}s timeout"
        )
        
        try:
            results = await self.rollback_journal.recover_all()
            
            success_count = sum(1 for r in results if r.success)
            fail_count = len(results) - success_count
            
            logger.info(
                f"Session {self.session_id}: Auto-recovery completed "
                f"({success_count} succeeded, {fail_count} failed)"
            )
        except Exception as e:
            logger.error(f"Session {self.session_id}: Auto-recovery failed: {e}")