from __future__ import annotations

from abc import ABC, abstractmethod

from fault_injector.orchestrator.session import ActionResult, FaultStep


class BaseAgent(ABC):
    """Agent executes prepared steps only."""

    @abstractmethod
    def execute(self, step: FaultStep, timeout: int) -> ActionResult:
        raise NotImplementedError

    @abstractmethod
    def rollback(self, step: FaultStep, timeout: int) -> ActionResult:
        raise NotImplementedError
