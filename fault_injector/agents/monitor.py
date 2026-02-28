"""
Monitoring agent for baseline and observation windows.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fault_injector.agents.base import AgentResult, BaseAgent
from fault_injector.scenarios.base import FaultContext


class MonitorAgent(BaseAgent):
    """Read-only monitor agent backed by Prometheus channel."""

    def __init__(self, channels: dict[str, Any] | None = None):
        super().__init__(name="monitor_agent", layer="monitor", channels=channels)

    async def inject(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        return AgentResult(
            success=True,
            agent_name=self.name,
            operation="inject",
            scenario_name=scenario_name,
            metadata={"noop": True},
        )

    async def recover(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        return AgentResult(
            success=True,
            agent_name=self.name,
            operation="recover",
            scenario_name=scenario_name,
            metadata={"noop": True},
        )

    async def verify(self, scenario_name: str, ctx: FaultContext) -> AgentResult:
        return AgentResult(
            success=True,
            agent_name=self.name,
            operation="verify",
            scenario_name=scenario_name,
            metadata={"noop": True},
        )

    def status(self) -> dict[str, Any]:
        return {
            "agent_name": self.name,
            "layer": self.layer,
            "ready": self.get_channel("prometheus") is not None,
        }

    async def collect_baseline(
        self,
        queries: dict[str, str],
        duration: int,
        interval: int = 15,
    ) -> dict[str, list[float]]:
        channel = self.get_channel("prometheus")
        if channel is None:
            return {}
        return await channel.collect_baseline(queries, duration=duration, interval=interval)

    async def observe(
        self,
        queries: dict[str, str],
        duration: int,
        interval: int = 15,
    ) -> dict[str, list[float]]:
        channel = self.get_channel("prometheus")
        if channel is None:
            return {}
        return await channel.collect_baseline(queries, duration=duration, interval=interval)

    async def point_in_time(self, queries: dict[str, str]) -> dict[str, float]:
        channel = self.get_channel("prometheus")
        if channel is None:
            return {}
        values: dict[str, float] = {}
        for key, promql in queries.items():
            try:
                values[key] = float(await channel.query_instant(promql))
            except Exception:
                values[key] = 0.0
        values["collected_at"] = datetime.now().timestamp()
        return values

