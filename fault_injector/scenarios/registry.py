"""
Scenario registry.

Provides lookup and listing utilities for all fault scenarios.
"""
from __future__ import annotations

import logging
from typing import Type

from fault_injector.scenarios.base import BaseScenario

# vLLM latency required scenarios (RC-1~RC-6)
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,          # RC-1
    NetworkJitterScenario,          # RC-2
    StorageIOInterferenceScenario,  # RC-3
    PlatformCascadeScenario,        # RC-4
    OSResourcePressureScenario,     # RC-5
    ThermalThrottlingScenario,      # RC-6
)

# RDMA required scenarios (F-1~F-6)
from fault_injector.scenarios.rdma_anomaly import (
    PFCDeadlockScenario,         # F-1
    ECNMisconfigurationScenario, # F-2
    RDMALoadImbalanceScenario,   # F-3
    RDMALinkFlapScenario,        # F-4
    RoCEMTUMismatchScenario,     # F-5
    RDMAQoSDowngradeScenario,    # F-6
)

logger = logging.getLogger(__name__)

SCENARIO_REGISTRY: dict[str, Type[BaseScenario]] = {
    # vLLM latency
    "gpu_contention": GPUContentionScenario,
    "network_jitter": NetworkJitterScenario,
    "storage_io_interference": StorageIOInterferenceScenario,
    "platform_cascade": PlatformCascadeScenario,
    "os_resource_pressure": OSResourcePressureScenario,
    "thermal_throttling": ThermalThrottlingScenario,

    # RDMA anomaly
    "pfc_deadlock": PFCDeadlockScenario,
    "ecn_misconfiguration": ECNMisconfigurationScenario,
    "rdma_load_imbalance": RDMALoadImbalanceScenario,
    "rdma_link_flap": RDMALinkFlapScenario,
    "roce_mtu_mismatch": RoCEMTUMismatchScenario,
    "rdma_qos_downgrade": RDMAQoSDowngradeScenario,
}


def register_scenario(name: str, scenario_class: Type[BaseScenario]) -> None:
    if name in SCENARIO_REGISTRY:
        logger.warning("Scenario '%s' already exists and will be overwritten", name)
    SCENARIO_REGISTRY[name] = scenario_class
    logger.info("Registered scenario: %s", name)


def get_scenario(name: str) -> BaseScenario | None:
    scenario_class = SCENARIO_REGISTRY.get(name)
    if scenario_class:
        return scenario_class()
    return None


def list_scenarios() -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    for name, scenario_class in SCENARIO_REGISTRY.items():
        instance = scenario_class()
        scenarios.append(
            {
                "name": name,
                "description": instance.description,
                "layer": instance.layer,
            }
        )
    return scenarios


def list_scenarios_by_layer(layer: str) -> list[dict[str, str]]:
    return [s for s in list_scenarios() if s["layer"] == layer]


def get_scenario_info(name: str) -> dict[str, str | list[str]] | None:
    scenario_class = SCENARIO_REGISTRY.get(name)
    if not scenario_class:
        return None

    instance = scenario_class()
    return {
        "name": instance.name,
        "description": instance.description,
        "layer": instance.layer,
        "monitor_queries": list(instance.monitor_queries().keys()),
    }
