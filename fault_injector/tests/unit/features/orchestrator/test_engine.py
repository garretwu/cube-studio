from __future__ import annotations

import pytest

from fault_injector.config.schema import FaultInjectorConfig, SSHConfig, TargetNodeConfig, ScenarioConfig
from fault_injector.orchestrator.engine import FaultOrchestrator


@pytest.mark.asyncio
async def test_engine_run_sequential_dry_run(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={"baseline_duration": 0, "post_recovery_duration": 0},
        inventory={
            "nodes": [
                TargetNodeConfig(
                    name="node-1",
                    ssh=SSHConfig(host="127.0.0.1", user="root"),
                    interface="eth0",
                )
            ]
        },
        scenarios={
            "network_jitter": ScenarioConfig(
                name="network_jitter",
                enabled=True,
                target_nodes=["node-1"],
                params={"duration": 0, "interface": "eth0", "delay_ms": 5},
            )
        },
    )
    cfg.global_.session_dir = str(tmp_path)
    cfg.orchestrator.observe_interval = 1
    cfg.monitor.baseline_duration = 0

    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
    session = await orchestrator.run()

    assert session.status.value == "completed"
    assert "network_jitter" in session.scenario_results


@pytest.mark.asyncio
async def test_engine_should_use_monitor_prometheus_url_and_dry_run_channel(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={
            "prometheus_url": "http://prometheus.example:9090",
            "baseline_duration": 0,
            "post_recovery_duration": 0,
        },
        inventory={
            "nodes": [
                TargetNodeConfig(
                    name="node-1",
                    ssh=SSHConfig(host="127.0.0.1", user="root"),
                    interface="eth0",
                )
            ]
        },
        scenarios={
            "network_jitter": ScenarioConfig(
                name="network_jitter",
                enabled=True,
                target_nodes=["node-1"],
                params={"duration": 0, "interface": "eth0", "delay_ms": 5},
            )
        },
    )
    cfg.global_.session_dir = str(tmp_path)
    cfg.orchestrator.observe_interval = 1

    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
    session = await orchestrator.run()

    assert session.status.value == "completed"
    assert orchestrator.prometheus is not None
    assert orchestrator.prometheus.base_url == "http://prometheus.example:9090"
    assert orchestrator.prometheus.dry_run is True
