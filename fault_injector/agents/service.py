"""Service layer fault agent."""
from __future__ import annotations

from fault_injector.agents.scenario_dispatch import ScenarioDispatchAgent


class ServiceFaultAgent(ScenarioDispatchAgent):
    def __init__(self, channels: dict | None = None):
        super().__init__(name="service_fault_agent", layer="service", channels=channels)
