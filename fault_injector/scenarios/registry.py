"""
场景注册表

管理所有可用的故障场景。
"""
from __future__ import annotations

import logging
from typing import Type

from fault_injector.scenarios.base import BaseScenario
from fault_injector.scenarios.network_jitter import NetworkJitterScenario

logger = logging.getLogger(__name__)

# 场景注册表
SCENARIO_REGISTRY: dict[str, Type[BaseScenario]] = {
    # 必选场景：vLLM Latency
    "network_jitter": NetworkJitterScenario,
    
    # 以下场景将在后续 Sprint 实现
    # "gpu_contention": VLLMGpuContention,
    # "storage_io_interference": VLLMStorageIO,
    # "platform_cascade": VLLMPlatformCascade,
    # "os_resource_pressure": VLLMOSPressure,
    # "thermal_throttling": VLLMThermalThrottle,
    
    # 必选场景：RDMA
    # "pfc_deadlock": RDMAPfcDeadlock,
    # "ecn_misconfiguration": RDMAEcnMisconfig,
    # "rdma_load_imbalance": RDMALoadImbalance,
    # "rdma_link_flap": RDMALinkFlap,
    # "roce_mtu_mismatch": RoCEMtuMismatch,
    # "rdma_qos_downgrade": RDMAQosDowngrade,
}


def register_scenario(name: str, scenario_class: Type[BaseScenario]) -> None:
    """
    注册新场景。
    
    Args:
        name: 场景名称
        scenario_class: 场景类
    """
    if name in SCENARIO_REGISTRY:
        logger.warning(f"场景 '{name}' 已存在，将被覆盖")
    SCENARIO_REGISTRY[name] = scenario_class
    logger.info(f"注册场景: {name}")


def get_scenario(name: str) -> BaseScenario | None:
    """
    获取场景实例。
    
    Args:
        name: 场景名称
        
    Returns:
        BaseScenario: 场景实例，如果不存在返回 None
    """
    scenario_class = SCENARIO_REGISTRY.get(name)
    if scenario_class:
        return scenario_class()
    return None


def list_scenarios() -> list[dict[str, str]]:
    """
    列出所有可用场景。
    
    Returns:
        list: [{"name": ..., "description": ..., "layer": ...}, ...]
    """
    scenarios = []
    for name, scenario_class in SCENARIO_REGISTRY.items():
        instance = scenario_class()
        scenarios.append({
            "name": name,
            "description": instance.description,
            "layer": instance.layer,
        })
    return scenarios