from __future__ import annotations

from fault_injector.agents.base import BaseAgent


class PlatformAgent(BaseAgent):
    """Reserved for cloud/platform fault execution."""

    def execute(self, step, timeout):
        raise NotImplementedError("PlatformAgent is not implemented yet")

    def rollback(self, step, timeout):
        raise NotImplementedError("PlatformAgent is not implemented yet")
