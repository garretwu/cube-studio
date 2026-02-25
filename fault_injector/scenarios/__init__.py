"""场景子包"""
from fault_injector.scenarios.base import BaseScenario, FaultContext
from fault_injector.scenarios.registry import (
    SCENARIO_REGISTRY,
    register_scenario,
    get_scenario,
    list_scenarios,
    list_scenarios_by_layer,
    get_scenario_info,
)

# 从 rdma_anomaly 导入 (RC-2 + F-1~F-6)
from fault_injector.scenarios.rdma_anomaly import (
    NetworkJitterScenario,
    PFCDeadlockScenario,
    ECNMisconfigurationScenario,
    RDMALoadImbalanceScenario,
    RDMALinkFlapScenario,
    RoCEMTUMismatchScenario,
    RDMAQoSDowngradeScenario,
)

# 从 vllm_latency 导入 (RC-1, RC-3~RC-6)
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,
    StorageIOInterferenceScenario,
    PlatformCascadeScenario,
    OSResourcePressureScenario,
    ThermalThrottlingScenario,
)

__all__ = [
    # Base classes
    "BaseScenario",
    "FaultContext",
    # Registry
    "SCENARIO_REGISTRY",
    "register_scenario",
    "get_scenario",
    "list_scenarios",
    "list_scenarios_by_layer",
    "get_scenario_info",
    # vLLM Latency Scenarios (RC-1~RC-6)
    "GPUContentionScenario",
    "NetworkJitterScenario",
    "StorageIOInterferenceScenario",
    "PlatformCascadeScenario",
    "OSResourcePressureScenario",
    "ThermalThrottlingScenario",
    # RDMA Anomaly Scenarios (F-1~F-6)
    "PFCDeadlockScenario",
    "ECNMisconfigurationScenario",
    "RDMALoadImbalanceScenario",
    "RDMALinkFlapScenario",
    "RoCEMTUMismatchScenario",
    "RDMAQoSDowngradeScenario",
]