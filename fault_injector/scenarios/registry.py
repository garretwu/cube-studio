from __future__ import annotations

from typing import Protocol

from fault_injector.config import InjectorConfig
from fault_injector.orchestrator.session import FaultStep


class Scenario(Protocol):
    name: str

    def build_steps(self, config: InjectorConfig) -> list[FaultStep]:
        ...


class ScenarioRegistry:
    def __init__(self) -> None:
        self._scenarios: dict[str, Scenario] = {}

    def register(self, scenario: Scenario) -> None:
        self._scenarios[scenario.name] = scenario

    def get(self, name: str) -> Scenario:
        if name not in self._scenarios:
            raise KeyError(f"Unknown scenario: {name}")
        return self._scenarios[name]
