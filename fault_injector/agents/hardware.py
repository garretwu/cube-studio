from __future__ import annotations

from fault_injector.agents.base import BaseAgent


class HardwareAgent(BaseAgent):
    """Reserved for hardware-fault execution."""

    def execute(self, step, timeout):
        raise NotImplementedError("HardwareAgent is not implemented yet")

    def rollback(self, step, timeout):
        raise NotImplementedError("HardwareAgent is not implemented yet")
