"""Platform layer fault agent."""
from __future__ import annotations

from fault_injector.agents.scenario_dispatch import ScenarioDispatchAgent


class PlatformFaultAgent(ScenarioDispatchAgent):
    def __init__(self, channels: dict | None = None):
        super().__init__(name="platform_fault_agent", layer="platform", channels=channels)
