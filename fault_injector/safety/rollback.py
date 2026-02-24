"""
WAL (Write-Ahead Log) 回滚日志

核心安全机制：任何故障注入操作，其恢复命令在注入**之前**持久化到磁盘。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fault_injector.config.schema import (
    RollbackEntry,
    RollbackEntryStatus,
    RecoverResult,
)

logger = logging.getLogger(__name__)


class RollbackJournal:
    """
    WAL 回滚日志 — 故障注入系统的核心安全机制。
    
    核心不变量：
    - 任何故障注入操作，其恢复命令在注入**之前**写入日志
    - 使用 JSONL 格式 (append-only)，即使崩溃也能恢复
    - 每次 append 后立即 fsync 确保持久化
    
    崩溃恢复语义：
    - WAL 写入前崩溃 → 注入未执行，无需恢复
    - WAL 写入后、注入前崩溃 → 恢复操作已记录，--resume 可安全清理
    - 注入后崩溃 → WAL 已记录，--resume 恢复所有 active 条目
    """
    
    def __init__(self, journal_path: Path):
        """
        初始化回滚日志。
        
        Args:
            journal_path: 日志文件路径 (JSONL 格式)
        """
        self.journal_path = Path(journal_path)
        self.entries: list[RollbackEntry] = []
        self._load()
    
    def _load(self) -> None:
        """从磁盘加载现有日志条目"""
        if not self.journal_path.exists():
            return
        
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entry = RollbackEntry(**data)
                        self.entries.append(entry)
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning(f"无法解析日志条目: {line[:100]}... 错误: {e}")
        except Exception as e:
            logger.error(f"加载回滚日志失败: {e}")
    
    def record(
        self,
        fault_id: str,
        channel: str,
        target: str,
        inject_action: str,
        inject_params: dict[str, Any],
        recover_action: str,
        recover_params: dict[str, Any] | None = None,
    ) -> RollbackEntry:
        """
        写前记录：在故障注入之前调用，立即 fsync 到磁盘。
        
        Args:
            fault_id: 故障唯一标识
            channel: 执行通道 (ssh / redfish / k8s / switch)
            target: 目标节点/设备
            inject_action: 注入操作描述
            inject_params: 注入参数
            recover_action: 恢复操作
            recover_params: 恢复参数
            
        Returns:
            RollbackEntry: 创建的日志条目
        """
        entry = RollbackEntry(
            fault_id=fault_id,
            channel=channel,
            target=target,
            inject_action=inject_action,
            inject_params=inject_params or {},
            recover_action=recover_action,
            recover_params=recover_params or {},
            status=RollbackEntryStatus.ACTIVE,
        )
        
        self.entries.append(entry)
        self._append_to_disk(entry)
        
        logger.info(f"记录回滚条目: {fault_id} -> {recover_action}")
        return entry
    
    def _append_to_disk(self, entry: RollbackEntry) -> None:
        """将条目追加到磁盘 (fsync)"""
        # 确保目录存在
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 序列化并写入
        line = entry.model_dump_json() + "\n"
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    
    def get_active_faults(self) -> list[RollbackEntry]:
        """获取所有活跃故障"""
        return [e for e in self.entries if e.status == RollbackEntryStatus.ACTIVE]
    
    def update_status(self, fault_id: str, status: RollbackEntryStatus) -> None:
        """更新条目状态并重写日志"""
        for entry in self.entries:
            if entry.fault_id == fault_id:
                entry.status = status
                break
        self._rewrite_journal()
    
    def _rewrite_journal(self) -> None:
        """重写整个日志文件（状态更新后）"""
        if not self.journal_path.parent.exists():
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(entry.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())
    
    def mark_recovered(self, fault_id: str) -> None:
        """标记故障已恢复"""
        self.update_status(fault_id, RollbackEntryStatus.RECOVERED)
    
    def mark_failed(self, fault_id: str) -> None:
        """标记恢复失败"""
        self.update_status(fault_id, RollbackEntryStatus.FAILED)
    
    def get_entry(self, fault_id: str) -> RollbackEntry | None:
        """根据 fault_id 获取条目"""
        for entry in self.entries:
            if entry.fault_id == fault_id:
                return entry
        return None
    
    def count_active(self) -> int:
        """统计活跃故障数量"""
        return len(self.get_active_faults())
    
    def clear_completed(self) -> int:
        """清除已完成（recovered/failed）的条目，返回清除数量"""
        original_len = len(self.entries)
        self.entries = [
            e for e in self.entries 
            if e.status == RollbackEntryStatus.ACTIVE
        ]
        cleared = original_len - len(self.entries)
        if cleared > 0:
            self._rewrite_journal()
            logger.info(f"清除了 {cleared} 个已完成的回滚条目")
        return cleared