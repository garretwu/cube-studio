"""Scenario package exports."""

from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.scenarios.registry import (
    SCENARIO_REGISTRY,
    get_scenario,
    get_scenario_info,
    list_scenarios,
    list_scenarios_by_layer,
    register_scenario,
)

# RDMA anomaly scenarios (F-1~F-6)
from fault_injector.scenarios.rdma_anomaly import (
    ECNMisconfigurationScenario,
    PFCDeadlockScenario,
    RDMALinkFlapScenario,
    RDMALoadImbalanceScenario,
    RDMAQoSDowngradeScenario,
    RoCEMTUMismatchScenario,
)

# vLLM latency scenarios (RC-1~RC-6)
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,
    NetworkJitterScenario,
    OSResourcePressureScenario,
    PlatformCascadeScenario,
    StorageIOInterferenceScenario,
    ThermalThrottlingScenario,
)

__all__ = [
    "BaseScenario",
    "FaultContext",
    "SCENARIO_REGISTRY",
    "register_scenario",
    "get_scenario",
    "list_scenarios",
    "list_scenarios_by_layer",
    "get_scenario_info",
    "GPUContentionScenario",
    "NetworkJitterScenario",
    "StorageIOInterferenceScenario",
    "PlatformCascadeScenario",
    "OSResourcePressureScenario",
    "ThermalThrottlingScenario",
    "PFCDeadlockScenario",
    "ECNMisconfigurationScenario",
    "RDMALoadImbalanceScenario",
    "RDMALinkFlapScenario",
    "RoCEMTUMismatchScenario",
    "RDMAQoSDowngradeScenario",
]

