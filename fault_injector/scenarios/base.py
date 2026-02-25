"""
BaseScenario — 故障场景基类

定义故障场景的标准生命周期接口。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from fault_injector.config.schema import (
    InjectResult,
    RecoverResult,
    ScenarioResult,
)
from fault_injector.channels.ssh import SSHChannel
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.safety.guard import SafetyGuard

if TYPE_CHECKING:
    from fault_injector.channels.redfish import RedfishChannel
    from fault_injector.channels.switch import SwitchChannel
    from fault_injector.channels.kubernetes import K8sChannel
    from fault_injector.channels.prometheus import PrometheusChannel

logger = logging.getLogger(__name__)


@dataclass
class FaultContext:
    """
    故障注入上下文 — 传递给场景的执行环境。
    
    包含场景执行所需的所有 Channel 和上下文信息。
    """
    ssh: SSHChannel
    rollback: RollbackJournal
    guard: SafetyGuard
    target_node: str
    params: dict[str, Any]
    fault_id: str
    redfish: "RedfishChannel | None" = None
    switch: "SwitchChannel | None" = None
    k8s: "K8sChannel | None" = None
    prometheus: "PrometheusChannel | None" = None
    session_id: str = ""
    interface: str = "eth0"


class BaseScenario(ABC):
    """
    故障场景基类。
    
    每个故障场景必须实现以下生命周期方法：
    - inject(): 注入故障
    - recover(): 恢复故障
    - verify(): 验证恢复成功
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """场景名称（用于注册和 CLI）"""
        pass
    
    @property
    def description(self) -> str:
        """场景描述"""
        return ""
    
    @property
    def layer(self) -> str:
        """故障层级 (hardware / os / platform / service)"""
        return "os"
    
    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        """
        注入故障。
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            InjectResult: 注入结果
        """
        pass
    
    async def recover(self, ctx: FaultContext) -> RecoverResult:
        """
        恢复故障。
        
        默认实现：由 WAL 自动处理恢复。
        子类可重写以实现额外清理逻辑。
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            RecoverResult: 恢复结果
        """
        return RecoverResult(success=True, fault_id=ctx.fault_id)
    
    async def verify(self, ctx: FaultContext) -> bool:
        """
        验证恢复是否成功。
        
        Args:
            ctx: 故障注入上下文
            
        Returns:
            bool: 恢复是否成功
        """
        return True
    
    def monitor_queries(self) -> dict[str, str]:
        """
        返回监控期间需要查询的 PromQL 映射。
        
        Returns:
            dict: {metric_name: promql}
        """
        return {}
    
    def generate_fault_id(self, ctx: FaultContext) -> str:
        """生成故障 ID"""
        return f"{self.name}_{ctx.target_node}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
