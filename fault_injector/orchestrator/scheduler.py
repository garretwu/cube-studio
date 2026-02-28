"""
Scheduler for scenario sequencing and combined timed phases.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from fault_injector.config.schema import CombinedPhaseConfig, FaultInjectorConfig, ScenarioConfig


@dataclass
class ScheduledEvent:
    """Normalized scheduled event consumed by the orchestrator engine."""

    time_offset: int
    action: str
    targets: list[str]
    phase_name: str


def parse_time_offset(value: str | int | float) -> int:
    """Parse relative time values like 60, '60s', '2m', '1h'."""
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip().lower()
    if text.endswith("ms"):
        return int(float(text[:-2]) / 1000.0)
    if text.endswith("s"):
        return int(float(text[:-1]))
    if text.endswith("m"):
        return int(float(text[:-1]) * 60)
    if text.endswith("h"):
        return int(float(text[:-1]) * 3600)
    return int(float(text))


class ScenarioScheduler:
    """Builds deterministic event plans for the orchestration engine."""

    def __init__(self, config: FaultInjectorConfig):
        self.config = config

    def has_combined_plan(self) -> bool:
        combined = self.config.combined_scenario
        return combined is not None and bool(combined.phases)

    def build_events(self) -> list[ScheduledEvent]:
        if self.has_combined_plan():
            return self._build_combined_events(self.config.combined_scenario.phases)
        return self._build_sequential_events(self._enabled_scenarios())

    def _enabled_scenarios(self) -> list[ScenarioConfig]:
        out: list[ScenarioConfig] = []
        for _, sc in self.config.scenarios.items():
            if sc.enabled:
                out.append(sc)
        return out

    def _build_combined_events(self, phases: Iterable[CombinedPhaseConfig]) -> list[ScheduledEvent]:
        events: list[ScheduledEvent] = []
        for idx, phase in enumerate(phases):
            offset = parse_time_offset(phase.time)
            if phase.inject:
                events.append(
                    ScheduledEvent(
                        time_offset=offset,
                        action="inject",
                        targets=list(phase.inject),
                        phase_name=f"combined_inject_{idx}",
                    )
                )
            if phase.recover:
                events.append(
                    ScheduledEvent(
                        time_offset=offset,
                        action="recover",
                        targets=list(phase.recover),
                        phase_name=f"combined_recover_{idx}",
                    )
                )
        events.sort(key=lambda item: item.time_offset)
        return events

    def _build_sequential_events(self, scenarios: list[ScenarioConfig]) -> list[ScheduledEvent]:
        events: list[ScheduledEvent] = []
        current = 0
        for idx, scenario in enumerate(scenarios):
            duration = int(scenario.params.get("duration", 60))
            events.append(
                ScheduledEvent(
                    time_offset=current,
                    action="inject",
                    targets=[scenario.name],
                    phase_name=f"sequential_inject_{idx}",
                )
            )
            current += duration
            events.append(
                ScheduledEvent(
                    time_offset=current,
                    action="recover",
                    targets=[scenario.name],
                    phase_name=f"sequential_recover_{idx}",
                )
            )
        return events

