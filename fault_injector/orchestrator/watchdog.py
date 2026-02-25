from __future__ import annotations

from fault_injector.orchestrator.session import ActionResult


class ExecutionWatchdog:
    """Simple watchdog to summarize run health."""

    def all_successful(self, reports: list[ActionResult]) -> bool:
        return all(item.success for item in reports)
