"""
Configuration loader for fault injector YAML files.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from fault_injector.config.schema import (
    CombinedPhaseConfig,
    CombinedScenarioConfig,
    FaultInjectorConfig,
    GlobalConfig,
    IPMIConfig,
    LoadSimulatorConfig,
    MonitorConfig,
    OrchestratorConfig,
    RedfishConfig,
    SSHConfig,
    SafetyConfig,
    ScenarioConfig,
    SwitchConfig,
    TargetNodeConfig,
)


def load_config(path: str) -> FaultInjectorConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    config = _parse_config(raw)
    config.config_hash = _compute_hash(config_path)
    return config


def _parse_config(raw: dict[str, Any]) -> FaultInjectorConfig:
    global_raw = raw.get("global", {})
    safety_raw = global_raw.get("safety", {})
    safety = SafetyConfig(
        require_confirmation=safety_raw.get("require_confirmation", True),
        auto_recover_timeout=safety_raw.get("auto_recover_timeout", 600),
        dry_run=safety_raw.get("dry_run", False),
        max_concurrent_faults=safety_raw.get("max_concurrent_faults", 3),
        excluded_nodes=safety_raw.get("excluded_nodes", []),
    )
    global_config = GlobalConfig(
        session_dir=global_raw.get("session_dir", "./fault-reports/sessions/"),
        log_level=global_raw.get("log_level", "INFO"),
        safety=safety,
    )

    orchestrator_raw = raw.get("orchestrator", {})
    orchestrator = OrchestratorConfig(
        max_parallel_agents=orchestrator_raw.get("max_parallel_agents", 1),
        observe_interval=orchestrator_raw.get("observe_interval", 15),
        session_dir=orchestrator_raw.get(
            "session_dir",
            global_config.session_dir,
        ),
    )

    monitor_raw = raw.get("monitor", {})
    monitor = MonitorConfig(
        enabled=monitor_raw.get("enabled", True),
        prometheus_url=monitor_raw.get("prometheus_url", "http://10.11.4.3:31260"),
        baseline_duration=monitor_raw.get("baseline_duration", 60),
        post_recovery_duration=monitor_raw.get("post_recovery_duration", 60),
        baseline_queries=monitor_raw.get("baseline_queries", {}),
        baseline_require_non_zero=monitor_raw.get("baseline_require_non_zero", []),
        baseline_min_samples=monitor_raw.get("baseline_min_samples", 3),
        baseline_max_error_ratio=monitor_raw.get("baseline_max_error_ratio", 0.3),
        baseline_strict=monitor_raw.get("baseline_strict", False),
    )

    inventory_raw = raw.get("inventory", {})
    inventory: dict[str, list[TargetNodeConfig]] = {}
    for group_name, nodes in inventory_raw.items():
        if not isinstance(nodes, list):
            continue
        group_nodes: list[TargetNodeConfig] = []
        for node_raw in nodes:
            if not isinstance(node_raw, dict):
                continue
            ssh_raw = node_raw.get("ssh", {})
            ssh = SSHConfig(
                host=ssh_raw.get("host", ""),
                port=ssh_raw.get("port", 22),
                user=ssh_raw.get("user", "root"),
                key_file=ssh_raw.get("key_file"),
                password=ssh_raw.get("password"),
                timeout=ssh_raw.get("timeout", 30),
                use_sudo=ssh_raw.get("use_sudo", True),
            )
            redfish_cfg = None
            redfish_raw = node_raw.get("redfish")
            if isinstance(redfish_raw, dict):
                redfish_cfg = RedfishConfig(
                    bmc_host=redfish_raw.get("bmc_host", ""),
                    username=redfish_raw.get("username"),
                    password=redfish_raw.get("password"),
                    token=redfish_raw.get("token"),
                    verify_tls=redfish_raw.get("verify_tls", True),
                    timeout=redfish_raw.get("timeout", 30),
                )
            ipmi_cfg = None
            ipmi_raw = node_raw.get("ipmi")
            if isinstance(ipmi_raw, dict):
                ipmi_cfg = IPMIConfig(
                    host=ipmi_raw.get("host", ""),
                    username=ipmi_raw.get("username"),
                    password=ipmi_raw.get("password"),
                    interface=ipmi_raw.get("interface", "lanplus"),
                    port=ipmi_raw.get("port", 623),
                    tool_path=ipmi_raw.get("tool_path", "ipmitool"),
                    cipher_suite=ipmi_raw.get("cipher_suite"),
                    timeout=ipmi_raw.get("timeout", 30),
                )
            group_nodes.append(
                TargetNodeConfig(
                    name=node_raw.get("name", ""),
                    ssh=ssh,
                    redfish=redfish_cfg,
                    ipmi=ipmi_cfg,
                    interface=node_raw.get("interface", "eth0"),
                    roles=node_raw.get("roles", []),
                )
            )
        inventory[group_name] = group_nodes

    scenarios_raw = raw.get("scenarios", {})
    scenarios: dict[str, ScenarioConfig] = {}
    for scenario_name, scenario_raw in scenarios_raw.items():
        if not isinstance(scenario_raw, dict):
            continue
        params = scenario_raw.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # Support both:
        # 1) scenarios.<name>.params.load_simulator
        # 2) scenarios.<name>.load_simulator
        load_sim_raw = params.get("load_simulator")
        if load_sim_raw is None:
            load_sim_raw = scenario_raw.get("load_simulator")
        if isinstance(load_sim_raw, dict):
            params["load_simulator"] = LoadSimulatorConfig(**load_sim_raw).model_dump()

        scenarios[scenario_name] = ScenarioConfig(
            name=scenario_raw.get("name", scenario_name),
            enabled=scenario_raw.get("enabled", True),
            target_nodes=scenario_raw.get("target_nodes", []),
            params=params,
        )

    switches_raw = raw.get("switches", {})
    switches: dict[str, SwitchConfig] = {}
    if isinstance(switches_raw, dict):
        for switch_name, switch_raw in switches_raw.items():
            if not isinstance(switch_raw, dict):
                continue
            switches[switch_name] = SwitchConfig(
                host=switch_raw.get("host", ""),
                port=switch_raw.get("port", 830),
                username=switch_raw.get("username"),
                user=switch_raw.get("user"),
                password=switch_raw.get("password"),
                timeout=switch_raw.get("timeout", 30),
                type=switch_raw.get("type"),
                description=switch_raw.get("description"),
            )

    combined = None
    combined_raw = raw.get("combined_scenario")
    if isinstance(combined_raw, dict):
        phases: list[CombinedPhaseConfig] = []
        for phase_raw in combined_raw.get("phases", []):
            if not isinstance(phase_raw, dict):
                continue
            phases.append(
                CombinedPhaseConfig(
                    time=phase_raw.get("time", "0s"),
                    inject=phase_raw.get("inject", []),
                    recover=phase_raw.get("recover", []),
                )
            )
        combined = CombinedScenarioConfig(
            name=combined_raw.get("name", ""),
            phases=phases,
        )

    return FaultInjectorConfig(
        global_=global_config,
        orchestrator=orchestrator,
        monitor=monitor,
        inventory=inventory,
        switches=switches,
        scenarios=scenarios,
        combined_scenario=combined,
    )


def _compute_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()[:16]
