from __future__ import annotations

from fault_injector.orchestrator.session import FaultStep


class StepScheduler:
    """Pure scheduling utility; orchestrator controls invocation."""

    def schedule(self, steps: list[FaultStep]) -> list[FaultStep]:
        return list(steps)
