"""
Pydantic v2 配置模型

定义 Fault Injector 的完整配置 Schema。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# SSH 和节点配置
# ============================================================================

class SSHConfig(BaseModel):
    """SSH 连接配置"""
    host: str
    port: int = 22
    user: str = "root"
    key_file: str | None = None
    password: str | None = None
    timeout: int = 30


class TargetNodeConfig(BaseModel):
    """目标节点配置"""
    name: str
    ssh: SSHConfig
    interface: str = "eth0"  # 网络接口
    roles: list[str] = []


# ============================================================================
# 安全配置
# ============================================================================

class SafetyConfig(BaseModel):
    """安全配置"""
    require_confirmation: bool = True
    auto_recover_timeout: int = 600
    dry_run: bool = False
    max_concurrent_faults: int = 3
    excluded_nodes: list[str] = []


class GlobalConfig(BaseModel):
    """全局配置"""
    session_dir: str = "./fault-reports/sessions/"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


# ============================================================================
# 场景参数配置
# ============================================================================

class NetworkJitterParams(BaseModel):
    """网络延迟场景参数 (RC-2)"""
    delay_ms: int = 50              # 基础延迟 (毫秒)
    jitter_ms: int = 100            # 抖动范围 (毫秒)
    distribution: Literal["normal", "pareto", "paretonormal"] = "pareto"
    loss_pct: float = 0             # 丢包率 (%)
    duration: int = 300             # 持续时间 (秒)
    interface: str = "eth0"         # 网络接口


class CPUStressParams(BaseModel):
    """CPU 压力场景参数"""
    workers: int = 64
    load_percent: int = 95
    duration: int = 300


class MemoryPressureParams(BaseModel):
    """内存压力场景参数"""
    vm_bytes_percent: int = 85
    duration: int = 300


class ScenarioConfig(BaseModel):
    """场景配置"""
    name: str
    enabled: bool = True
    target_nodes: list[str] = []
    params: dict[str, Any] = {}


# ============================================================================
# Session 状态
# ============================================================================

class SessionStatus(str, Enum):
    """Session 状态"""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


class SessionPhase(str, Enum):
    """Session 阶段"""
    INIT = "init"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"


class Session(BaseModel):
    """Session 状态模型"""
    session_id: str
    config_hash: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[str] = []
    rollback_journal_path: str = ""
    events: list[dict] = []

    class Config:
        use_enum_values = True


# ============================================================================
# 执行结果
# ============================================================================

class ChannelResult(BaseModel):
    """Channel 执行结果"""
    success: bool
    output: str = ""
    error: str = ""
    dry_run: bool = False
    duration_ms: int = 0


class InjectResult(BaseModel):
    """故障注入结果"""
    success: bool
    fault_id: str = ""
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class RecoverResult(BaseModel):
    """故障恢复结果"""
    success: bool
    fault_id: str = ""
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ScenarioResult(BaseModel):
    """场景执行结果"""
    scenario_name: str
    success: bool
    inject_time: datetime | None = None
    recover_time: datetime | None = None
    verification_passed: bool | None = None
    error: str | None = None
    metrics: dict[str, Any] = {}


# ============================================================================
# 回滚日志
# ============================================================================

class RollbackEntryStatus(str, Enum):
    """回滚条目状态"""
    ACTIVE = "active"
    RECOVERED = "recovered"
    FAILED = "failed"


class RollbackEntry(BaseModel):
    """回滚日志条目"""
    fault_id: str
    injected_at: datetime = Field(default_factory=datetime.now)
    channel: str                       # ssh / redfish / k8s / switch
    target: str                        # 目标节点/设备
    inject_action: str                 # 注入操作描述
    inject_params: dict = {}           # 注入参数
    recover_action: str                # 恢复操作
    recover_params: dict = {}          # 恢复参数
    status: RollbackEntryStatus = RollbackEntryStatus.ACTIVE

    class Config:
        use_enum_values = True


# ============================================================================
# 主配置
# ============================================================================

class FaultInjectorConfig(BaseModel):
    """Fault Injector 完整配置"""
    global_: GlobalConfig = Field(default_factory=GlobalConfig)
    inventory: dict[str, list[TargetNodeConfig]] = {}
    scenarios: dict[str, ScenarioConfig] = {}
    config_hash: str = ""