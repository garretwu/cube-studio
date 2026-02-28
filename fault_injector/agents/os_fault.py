"""OS layer fault agent."""
from __future__ import annotations

from fault_injector.agents.scenario_dispatch import ScenarioDispatchAgent


class OSFaultAgent(ScenarioDispatchAgent):
    def __init__(self, channels: dict | None = None):
        super().__init__(name="os_fault_agent", layer="os", channels=channels)
