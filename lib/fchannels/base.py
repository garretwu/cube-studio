"""
BaseChannel — Channel 基类

统一 dry_run 和 WAL 集成。
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from fault_injector.config.schema import ChannelResult
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.safety.guard import SafetyGuard, SafetyViolationError

logger = logging.getLogger(__name__)


class BaseChannel(ABC):
    """
    Channel 基类 — 统一 dry_run 和 WAL 集成。
    
    所有 Channel（SSH、Redfish、K8s、Switch）都继承此类。
    
    执行流程：
    1. 禁止操作检查 (SafetyGuard)
    2. 如果有恢复操作，写入 WAL
    3. 如果 dry_run，仅日志记录
    4. 否则执行实际操作
    """
    
    def __init__(
        self,
        dry_run: bool = False,
        wal: RollbackJournal | None = None,
        guard: SafetyGuard | None = None,
    ):
        """
        初始化 Channel。
        
        Args:
            dry_run: 干运行模式（仅打印不执行）
            wal: 回滚日志
            guard: 安全守卫
        """
        self.dry_run = dry_run
        self.wal = wal
        self.guard = guard
    
    async def execute(
        self,
        action: str,
        params: dict[str, Any],
        recovery_action: str | None = None,
        recovery_params: dict[str, Any] | None = None,
        fault_id: str | None = None,
        target: str = "",
    ) -> ChannelResult:
        """
        执行操作（带安全检查和 WAL 记录）。
        
        Args:
            action: 操作类型
            params: 操作参数
            recovery_action: 恢复操作类型
            recovery_params: 恢复参数
            fault_id: 故障 ID
            target: 目标节点/设备
            
        Returns:
            ChannelResult: 执行结果
        """
        start_time = time.monotonic()
        
        # 1. 安全检查
        if self.guard:
            try:
                self._check_safety(action, params)
            except SafetyViolationError as e:
                logger.error(f"安全检查失败: {e}")
                return ChannelResult(
                    success=False,
                    error=str(e),
                    dry_run=self.dry_run,
                )
        
        # 2. WAL 记录（在执行之前）
        if recovery_action and self.wal and fault_id:
            self.wal.record(
                fault_id=fault_id,
                channel=self.channel_name,
                target=target,
                inject_action=action,
                inject_params=params,
                recover_action=recovery_action,
                recover_params=recovery_params or {},
            )
        
        # 3. dry_run 模式
        if self.dry_run:
            logger.info(f"[DRY-RUN] {self.channel_name}.{action}: {params}")
            return ChannelResult(
                success=True,
                output="[DRY-RUN] 操作未实际执行",
                dry_run=True,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
        
        # 4. 实际执行
        try:
            result = await self._execute_impl(action, params)
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            return result
        except Exception as e:
            logger.error(f"执行失败: {e}")
            return ChannelResult(
                success=False,
                error=str(e),
                dry_run=False,
                duration_ms=int((time.monotonic() - start_time) * 1000),
            )
    
    @abstractmethod
    async def _execute_impl(
        self, action: str, params: dict[str, Any]
    ) -> ChannelResult:
        """
        实际执行操作（子类实现）。
        
        Args:
            action: 操作类型
            params: 操作参数
            
        Returns:
            ChannelResult: 执行结果
        """
        pass
    
    def _check_safety(self, action: str, params: dict[str, Any]) -> None:
        """
        安全检查（子类可重写）。
        
        Args:
            action: 操作类型
            params: 操作参数
            
        Raises:
            SafetyViolationError: 安全违规
        """
        pass
    
    @property
    def channel_name(self) -> str:
        """获取 Channel 名称"""
        return self.__class__.__name__.replace("Channel", "").lower()