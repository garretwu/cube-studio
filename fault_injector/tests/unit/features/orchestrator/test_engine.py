from __future__ import annotations

import pytest

from fault_injector.config.schema import FaultInjectorConfig, SSHConfig, TargetNodeConfig, ScenarioConfig
from fault_injector.orchestrator.engine import FaultOrchestrator
from fault_injector.orchestrator.session import Session
from fault_injector.scenarios.base import BaseScenario
from fault_injector.scenarios.registry import SCENARIO_REGISTRY


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


@pytest.mark.asyncio
async def test_engine_should_disable_prometheus_when_monitor_disabled(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={
            "enabled": False,
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

    orchestrator = FaultOrchestrator(cfg, dry_run=False, session_dir=str(tmp_path))
    session = await orchestrator.run()

    assert session.status.value == "completed"
    assert orchestrator.prometheus is None


def test_engine_aggregate_queries_should_render_node_and_device_placeholders(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={"enabled": False, "baseline_duration": 0, "post_recovery_duration": 0},
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
            "gpu_contention": ScenarioConfig(
                name="gpu_contention",
                enabled=True,
                target_nodes=["node-1"],
                params={"duration": 0, "interface": "eth7"},
            ),
            "storage_io_interference": ScenarioConfig(
                name="storage_io_interference",
                enabled=True,
                target_nodes=["node-1"],
                params={"duration": 0, "interface": "nvme0n1"},
            ),
        },
    )
    cfg.global_.session_dir = str(tmp_path)
    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))

    queries = orchestrator._aggregate_monitor_queries()
    assert queries["gpu_contention:gpu_util"] == 'DCGM_FI_DEV_GPU_UTIL{node="node-1"}'
    assert queries["storage_io_interference:disk_io_util"] == 'node_disk_io_utilization_seconds{device="nvme0n1"}'


def test_engine_should_keep_raw_query_and_emit_warning_for_unresolved_placeholder(tmp_path):
    class _UnknownPlaceholderScenario(BaseScenario):
        @property
        def name(self) -> str:
            return "unknown_placeholder_scenario"

        @property
        def layer(self) -> str:
            return "os"

        async def inject(self, ctx):
            raise NotImplementedError

        def monitor_queries(self) -> dict[str, str]:
            return {
                "bad": 'metric_total{label="{unknown}"}',
                "ok": 'metric_total{label="{interface}"}',
            }

    SCENARIO_REGISTRY["unknown_placeholder_scenario"] = _UnknownPlaceholderScenario
    try:
        cfg = FaultInjectorConfig(
            monitor={"enabled": False, "baseline_duration": 0, "post_recovery_duration": 0},
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
                "unknown_placeholder_scenario": ScenarioConfig(
                    name="unknown_placeholder_scenario",
                    enabled=True,
                    target_nodes=["node-1"],
                    params={"interface": "eth9"},
                )
            },
        )
        cfg.global_.session_dir = str(tmp_path)
        orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
        orchestrator.session = Session.create(config_hash="", session_dir=str(tmp_path))

        queries = orchestrator._aggregate_monitor_queries()
        assert queries["unknown_placeholder_scenario:bad"] == 'metric_total{label="{unknown}"}'
        assert queries["unknown_placeholder_scenario:ok"] == 'metric_total{label="eth9"}'
        assert any(event["event"] == "monitor_query_render_warning" for event in orchestrator.session.events)
    finally:
        SCENARIO_REGISTRY.pop("unknown_placeholder_scenario", None)


@pytest.mark.asyncio
async def test_engine_should_record_load_simulator_events(tmp_path, monkeypatch):
    async def _fake_ls(*args, **kwargs):  # noqa: ANN002,ANN003
        _ = (args, kwargs)
        return {"exit_code": 0, "summary": {"session_id": "ls1"}}

    monkeypatch.setattr("fault_injector.scenarios.base._run_load_simulator", _fake_ls)

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
                params={
                    "duration": 0,
                    "interface": "eth0",
                    "delay_ms": 5,
                    "load_simulator": {
                        "enabled": True,
                        "config_path": "load_simulator/config/notebook-soak-only.yaml",
                        "only": ["inference"],
                        "timeout_seconds": 5,
                        "strict": True,
                    },
                },
            )
        },
    )
    cfg.global_.session_dir = str(tmp_path)
    cfg.orchestrator.observe_interval = 1
    cfg.monitor.baseline_duration = 0

    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
    session = await orchestrator.run()
    assert session.status.value == "completed"
    assert any(event["event"] == "load_simulator_run" for event in session.events)


def test_engine_aggregate_queries_should_use_configured_baseline_queries(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={
            "enabled": False,
            "baseline_duration": 0,
            "post_recovery_duration": 0,
            "baseline_queries": {
                "cpu_util_ratio": "avg(rate(node_cpu_seconds_total{mode!='idle'}[2m]))",
                "gpu_util": "avg(DCGM_FI_DEV_GPU_UTIL)",
            },
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
    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))

    queries = orchestrator._aggregate_monitor_queries()
    assert queries == {
        "cpu_util_ratio": "avg(rate(node_cpu_seconds_total{mode!='idle'}[2m]))",
        "gpu_util": "avg(DCGM_FI_DEV_GPU_UTIL)",
    }


def test_engine_default_baseline_queries_should_include_cpu_and_gpu_metrics(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={"enabled": False, "baseline_duration": 0, "post_recovery_duration": 0},
        scenarios={},
    )
    cfg.global_.session_dir = str(tmp_path)
    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))

    queries = orchestrator._default_monitor_queries()
    assert queries == {
        "cpu_util": "avg(1 - rate(node_cpu_seconds_total{mode='idle'}[1m]))",
        "memory_util": "avg(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))",
        "gpu_util": "avg(DCGM_FI_DEV_GPU_UTIL)",
        "gpu_memory_used_mb": "avg(DCGM_FI_DEV_FB_USED)",
        "gpu_power_watts": "avg(DCGM_FI_DEV_POWER_USAGE)",
    }


@pytest.mark.asyncio
async def test_engine_should_emit_baseline_quality_warning_for_required_non_zero_metric(tmp_path):
    cfg = FaultInjectorConfig(
        monitor={
            "baseline_duration": 0,
            "post_recovery_duration": 0,
            "baseline_queries": {"must_non_zero": "up"},
            "baseline_require_non_zero": ["must_non_zero"],
            "baseline_min_samples": 1,
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
    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
    session = await orchestrator.run()

    assert session.status.value == "completed"
    assert any(event["event"] == "baseline_quality_warning" for event in session.events)


@pytest.mark.asyncio
async def test_engine_should_use_full_observe_duration_for_monitor_collection(tmp_path, monkeypatch):
    captured: list[int] = []

    async def _fake_observe(self, queries, duration, interval=15):  # noqa: ANN001, ANN201
        _ = (self, queries, interval)
        captured.append(duration)
        return {"inference_p95": [0.0]}

    monkeypatch.setattr("fault_injector.agents.monitor.MonitorAgent.observe", _fake_observe)

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
                params={"duration": 7, "interface": "eth0", "delay_ms": 5},
            )
        },
    )
    cfg.global_.session_dir = str(tmp_path)
    cfg.orchestrator.observe_interval = 1
    orchestrator = FaultOrchestrator(cfg, dry_run=True, session_dir=str(tmp_path))
    session = await orchestrator.run()

    assert session.status.value == "completed"
    assert captured == [7]
