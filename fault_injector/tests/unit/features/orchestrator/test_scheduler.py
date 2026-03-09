from __future__ import annotations

from fault_injector.config.schema import (
    CombinedPhaseConfig,
    CombinedScenarioConfig,
    FaultInjectorConfig,
    ScenarioConfig,
)
from fault_injector.orchestrator.scheduler import ScenarioScheduler, parse_time_offset


def test_parse_time_offset_variants():
    assert parse_time_offset(12) == 12
    assert parse_time_offset("10s") == 10
    assert parse_time_offset("2m") == 120
    assert parse_time_offset("1h") == 3600


def test_scheduler_builds_sequential_events():
    cfg = FaultInjectorConfig(
        scenarios={
            "network_jitter": ScenarioConfig(name="network_jitter", enabled=True, params={"duration": 5}),
            "storage_io_interference": ScenarioConfig(
                name="storage_io_interference",
                enabled=True,
                params={"duration": 3},
            ),
        }
    )
    events = ScenarioScheduler(cfg).build_events()
    assert [e.action for e in events] == ["inject", "recover", "inject", "recover"]
    assert events[1].time_offset == 5
    assert events[3].time_offset == 8


def test_scheduler_builds_combined_events():
    cfg = FaultInjectorConfig(
        combined_scenario=CombinedScenarioConfig(
            name="combined",
            phases=[
                CombinedPhaseConfig(time="0s", inject=["network_jitter"]),
                CombinedPhaseConfig(time="60s", inject=["platform_cascade"]),
                CombinedPhaseConfig(time="120s", recover=["platform_cascade", "network_jitter"]),
            ],
        )
    )
    scheduler = ScenarioScheduler(cfg)
    assert scheduler.has_combined_plan() is True
    events = scheduler.build_events()
    assert len(events) == 3
    assert events[0].time_offset == 0
    assert events[2].action == "recover"
