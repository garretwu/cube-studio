"""
场景注册表

管理所有可用的故障场景。

场景分类：
1. vLLM 延迟必选场景 (RC-1~RC-6)
2. RDMA 必选场景 (F-1~F-6)
3. 硬件层扩展场景
4. OS 层扩展场景
5. 平台层扩展场景
6. 服务层扩展场景
"""
from __future__ import annotations

import logging
from typing import Type

from fault_injector.scenarios.base import BaseScenario

# vLLM 延迟场景 (RC-1, RC-3~RC-6)
from fault_injector.scenarios.vllm_latency import (
    GPUContentionScenario,       # RC-1
    StorageIOInterferenceScenario, # RC-3
    PlatformCascadeScenario,     # RC-4
    OSResourcePressureScenario,  # RC-5
    ThermalThrottlingScenario,   # RC-6
)

# RDMA 异常场景 (F-1~F-6 + RC-2)
from fault_injector.scenarios.rdma_anomaly import (
    NetworkJitterScenario,      # RC-2
    PFCDeadlockScenario,        # F-1
    ECNMisconfigurationScenario, # F-2
    RDMALoadImbalanceScenario,  # F-3
    RDMALinkFlapScenario,       # F-4
    RoCEMTUMismatchScenario,    # F-5
    RDMAQoSDowngradeScenario,   # F-6
)

# 硬件层扩展场景
from fault_injector.scenarios.hardware import (
    CPUStressScenario,
    GPURemovalScenario,
    ThermalThrottleHWScenario,
    NetworkDelayHWScenario,
)

# OS 层扩展场景
from fault_injector.scenarios.os_fault import (
    MemoryPressureScenario,
    DiskFullScenario,
    TimeSkewScenario,
)

# 平台层扩展场景
from fault_injector.scenarios.platform import (
    MySQLConnectionDropScenario,
    RedisUnavailableScenario,
    CeleryWorkerKillScenario,
    IstioGatewayKillScenario,
)

# 服务层扩展场景
from fault_injector.scenarios.service import (
    InferencePodKillScenario,
    PipelineWorkflowCancelScenario,
    NotebookPodKillScenario,
)

logger = logging.getLogger(__name__)

# 场景注册表
SCENARIO_REGISTRY: dict[str, Type[BaseScenario]] = {
    # ================================================================
    # vLLM 延迟场景 (RC-1~RC-6)
    # ================================================================
    "gpu_contention": GPUContentionScenario,           # RC-1: GPU 资源争抢
    "network_jitter": NetworkJitterScenario,           # RC-2: 网络延迟抖动
    "storage_io_interference": StorageIOInterferenceScenario,  # RC-3: 存储 I/O 干扰
    "platform_cascade": PlatformCascadeScenario,       # RC-4: 平台组件级联延迟
    "os_resource_pressure": OSResourcePressureScenario, # RC-5: OS 资源压力
    "thermal_throttling": ThermalThrottlingScenario,   # RC-6: 热降频
    
    # ================================================================
    # RDMA 异常场景 (F-1~F-6)
    # ================================================================
    "pfc_deadlock": PFCDeadlockScenario,               # F-1: PFC 死锁
    "ecn_misconfiguration": ECNMisconfigurationScenario, # F-2: ECN 标记阈值错配
    "rdma_load_imbalance": RDMALoadImbalanceScenario,  # F-3: 不均衡 RDMA 负载
    "rdma_link_flap": RDMALinkFlapScenario,            # F-4: RDMA 链路间歇性中断
    "roce_mtu_mismatch": RoCEMTUMismatchScenario,      # F-5: RoCE 网络 MTU 不一致
    "rdma_qos_downgrade": RDMAQoSDowngradeScenario,    # F-6: RDMA QoS 降级
    
    # ================================================================
    # 硬件层扩展场景
    # ================================================================
    "cpu_stress": CPUStressScenario,                   # CPU 压力
    "gpu_removal": GPURemovalScenario,                 # GPU 掉卡
    "thermal_throttle_hw": ThermalThrottleHWScenario,  # 热降频 (Redfish)
    "network_delay_hw": NetworkDelayHWScenario,        # 网络延迟 (交换机)
    
    # ================================================================
    # OS 层扩展场景
    # ================================================================
    "memory_pressure": MemoryPressureScenario,         # 内存压力
    "disk_full": DiskFullScenario,                     # 磁盘满
    "time_skew": TimeSkewScenario,                     # 时钟偏移
    
    # ================================================================
    # 平台层扩展场景
    # ================================================================
    "mysql_connection_drop": MySQLConnectionDropScenario,   # MySQL 连接中断
    "redis_unavailable": RedisUnavailableScenario,           # Redis 不可用
    "celery_worker_kill": CeleryWorkerKillScenario,          # Celery Worker 终止
    "istio_gateway_kill": IstioGatewayKillScenario,          # Istio Gateway 终止
    
    # ================================================================
    # 服务层扩展场景
    # ================================================================
    "inference_pod_kill": InferencePodKillScenario,          # 推理 Pod 终止
    "pipeline_workflow_cancel": PipelineWorkflowCancelScenario,  # Pipeline 取消
    "notebook_pod_kill": NotebookPodKillScenario,            # Notebook Pod 终止
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


def list_scenarios_by_layer(layer: str) -> list[dict[str, str]]:
    """
    按层级列出场景。
    
    Args:
        layer: 层级名称 (hardware / os / platform / service)
        
    Returns:
        list: 该层级的场景列表
    """
    return [s for s in list_scenarios() if s["layer"] == layer]


def get_scenario_info(name: str) -> dict[str, str | list[str]] | None:
    """
    获取场景详细信息。
    
    Args:
        name: 场景名称
        
    Returns:
        dict: 场景信息，如果不存在返回 None
    """
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