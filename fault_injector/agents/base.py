"""
BaseAgent - Base class for fault injection agents.

All fault injection agents inherit from this class.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of an agent operation."""
    success: bool
    agent_name: str = ""
    operation: str = ""
    output: str = ""
    error: str = ""
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "agent_name": self.agent_name,
            "operation": self.operation,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class BaseAgent(ABC):
    """
    Base class for fault injection agents.
    
    Agents encapsulate fault injection logic for specific infrastructure
    layers (hardware, OS, platform, service).
    
    Lifecycle:
    1. inject() - Inject the fault
    2. verify() - Verify fault is active
    3. recover() - Recover from fault
    4. verify_recovery() - Verify recovery is complete
    """
    
    def __init__(
        self,
        name: str,
        layer: str,
        channels: Optional[dict[str, Any]] = None,
    ):
        """
        Initialize the agent.
        
        Args:
            name: Agent name
            layer: Infrastructure layer (hardware/os/platform/service)
            channels: Channel instances for communication
        """
        self.name = name
        self.layer = layer
        self.channels = channels or {}
    
    @property
    def agent_name(self) -> str:
        """Get agent name."""
        return self.name
    
    @property
    def agent_layer(self) -> str:
        """Get infrastructure layer."""
        return self.layer
    
    @abstractmethod
    async def inject(self, params: dict[str, Any]) -> AgentResult:
        """
        Inject a fault.
        
        Args:
            params: Fault injection parameters
            
        Returns:
            AgentResult: Result of the injection
        """
        pass
    
    @abstractmethod
    async def verify(self) -> AgentResult:
        """
        Verify fault is active.
        
        Returns:
            AgentResult: Verification result
        """
        pass
    
    @abstractmethod
    async def recover(self) -> AgentResult:
        """
        Recover from the fault.
        
        Returns:
            AgentResult: Recovery result
        """
        pass
    
    @abstractmethod
    async def verify_recovery(self) -> AgentResult:
        """
        Verify recovery is complete.
        
        Returns:
            AgentResult: Verification result
        """
        pass
    
    def get_channel(self, channel_name: str) -> Any:
        """Get a channel by name."""
        return self.channels.get(channel_name)
    
    def log_operation(self, operation: str, details: str = "") -> None:
        """Log an operation."""
        logger.info(f"[{self.name}] {operation}: {details}")