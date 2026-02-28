"""
Agent abstractions for fault injector orchestration.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fault_injector.scenarios.base import FaultContext

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of an agent operation."""

    success: bool
    agent_name: str = ""
    operation: str = ""
    scenario_name: str = ""
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "agent_name": self.agent_name,
            "operation": self.operation,
            "scenario_name": self.scenario_name,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class BaseAgent(ABC):
    """
    Base contract for all fault-injection agents.

    Agents are orchestration adaptors that execute scenario logic through channels.
    """

    def __init__(self, name: str, layer: str, channels: dict[str, Any] | None = None):
        self.name = name
        self.layer = layer
        self.channels = channels or {}

    @property
    def agent_name(self) -> str:
        return self.name

    @property
    def agent_layer(self) -> str:
        return self.layer

    @abstractmethod
    async def inject(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """Inject a scenario fault."""

    @abstractmethod
    async def recover(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """Recover a scenario fault."""

    @abstractmethod
    async def verify(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        """Verify scenario recovery state."""

    @abstractmethod
    def status(self) -> dict[str, Any]:
        """Return current in-memory execution status."""

    def get_channel(self, channel_name: str) -> Any:
        return self.channels.get(channel_name)

    def log_operation(self, operation: str, details: str = "") -> None:
        logger.info("[%s] %s: %s", self.name, operation, details)

