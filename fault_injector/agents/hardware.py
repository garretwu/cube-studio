"""Hardware layer fault agent."""
from __future__ import annotations

from fault_injector.agents.scenario_dispatch import ScenarioDispatchAgent


class HardwareFaultAgent(ScenarioDispatchAgent):
    def __init__(self, channels: dict | None = None):
        super().__init__(name="hardware_fault_agent", layer="hardware", channels=channels)
