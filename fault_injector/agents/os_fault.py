from __future__ import annotations

from channel.ssh import SSHChannel
from fault_injector.agents.base import BaseAgent
from fault_injector.orchestrator.session import ActionResult, FaultStep


class OSFaultAgent(BaseAgent):
    """Runs OS level fault commands over SSH."""

    def __init__(self, channel: SSHChannel) -> None:
        self.channel = channel

    def execute(self, step: FaultStep, timeout: int) -> ActionResult:
        result = self.channel.execute(step.host, step.inject_command, timeout=timeout)
        return ActionResult(
            host=step.host.name,
            inject_command=step.inject_command,
            rollback_command=step.rollback_command,
            success=result.success,
        )

    def rollback(self, step: FaultStep, timeout: int) -> ActionResult:
        result = self.channel.execute(step.host, step.rollback_command, timeout=timeout)
        return ActionResult(
            host=step.host.name,
            inject_command="",
            rollback_command=step.rollback_command,
            success=result.success,
        )
