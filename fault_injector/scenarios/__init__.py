"""场景子包"""
from fault_injector.scenarios.base import BaseScenario, ScenarioResult, FaultContext
from fault_injector.scenarios.network_jitter import NetworkJitterScenario
from fault_injector.scenarios.registry import SCENARIO_REGISTRY, register_scenario

__all__ = [
    "BaseScenario",
    "ScenarioResult",
    "FaultContext",
    "NetworkJitterScenario",
    "SCENARIO_REGISTRY",
    "register_scenario",
]