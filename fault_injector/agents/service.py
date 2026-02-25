from __future__ import annotations

from fault_injector.agents.base import BaseAgent


class ServiceAgent(BaseAgent):
    """Reserved for service-level fault execution."""

    def execute(self, step, timeout):
        raise NotImplementedError("ServiceAgent is not implemented yet")

    def rollback(self, step, timeout):
        raise NotImplementedError("ServiceAgent is not implemented yet")
