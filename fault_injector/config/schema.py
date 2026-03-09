"""
Pydantic schemas for fault injector configuration and runtime records.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class SSHConfig(BaseModel):
    host: str
    port: int = 22
    user: str = "root"
    key_file: str | None = None
    password: str | None = None
    timeout: int = 30
    use_sudo: bool = True


class RedfishConfig(BaseModel):
    bmc_host: str
    username: str | None = None
    password: str | None = None
    token: str | None = None
    verify_tls: bool = True
    timeout: int = 30


class IPMIConfig(BaseModel):
    host: str
    username: str | None = None
    password: str | None = None
    interface: str = "lanplus"
    port: int = 623
    tool_path: str = "ipmitool"
    cipher_suite: int | None = None
    timeout: int = 30


class TargetNodeConfig(BaseModel):
    name: str
    ssh: SSHConfig
    redfish: RedfishConfig | None = None
    ipmi: IPMIConfig | None = None
    interface: str = "eth0"
    roles: list[str] = Field(default_factory=list)


class SwitchConfig(BaseModel):
    host: str
    port: int = 830
    username: str | None = None
    user: str | None = None
    password: str | None = None
    timeout: int = 30
    type: str | None = None
    description: str | None = None


class SafetyConfig(BaseModel):
    require_confirmation: bool = True
    auto_recover_timeout: int = 600
    dry_run: bool = False
    max_concurrent_faults: int = 3
    excluded_nodes: list[str] = Field(default_factory=list)


class GlobalConfig(BaseModel):
    session_dir: str = "./fault-reports/sessions/"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


class OrchestratorConfig(BaseModel):
    max_parallel_agents: int = 1
    observe_interval: int = 15
    session_dir: str = "./fault-reports/sessions/"


class MonitorConfig(BaseModel):
    enabled: bool = True
    prometheus_url: str = "http://10.11.4.3:31260"
    baseline_duration: int = 60
    post_recovery_duration: int = 60
    baseline_queries: dict[str, str] = Field(default_factory=dict)
    baseline_require_non_zero: list[str] = Field(default_factory=list)
    baseline_min_samples: int = 3
    baseline_max_error_ratio: float = 0.3
    baseline_strict: bool = False


class CombinedPhaseConfig(BaseModel):
    time: str | int | float = "0s"
    inject: list[str] = Field(default_factory=list)
    recover: list[str] = Field(default_factory=list)


class CombinedScenarioConfig(BaseModel):
    name: str = ""
    phases: list[CombinedPhaseConfig] = Field(default_factory=list)


class LoadSimulatorConfig(BaseModel):
    enabled: bool = False
    config_path: str = ""
    only: list[Literal["inference", "pipeline", "finetune", "notebook"]] = Field(default_factory=list)
    timeout_seconds: int = 900
    strict: bool = True


class ScenarioConfig(BaseModel):
    name: str
    enabled: bool = True
    target_nodes: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class NetworkJitterParams(BaseModel):
    delay_ms: int = 50
    jitter_ms: int = 100
    distribution: Literal["normal", "pareto", "paretonormal"] = "pareto"
    loss_pct: float = 0
    duration: int = 300
    interface: str = "eth0"


class CPUStressParams(BaseModel):
    workers: int = 64
    load_percent: int = 95
    duration: int = 300


class MemoryPressureParams(BaseModel):
    vm_bytes_percent: int = 85
    duration: int = 300


class SessionStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


class SessionPhase(str, Enum):
    INIT = "init"
    INJECT = "inject"
    OBSERVE = "observe"
    RECOVER = "recover"
    VERIFY = "verify"


class Session(BaseModel):
    session_id: str
    config_hash: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    status: SessionStatus = SessionStatus.RUNNING
    phase: SessionPhase = SessionPhase.INIT
    active_faults: list[str] = Field(default_factory=list)
    rollback_journal_path: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)

    class Config:
        use_enum_values = True


class ChannelResult(BaseModel):
    success: bool
    output: str = ""
    error: str = ""
    dry_run: bool = False
    duration_ms: int = 0


class InjectResult(BaseModel):
    success: bool
    fault_id: str = ""
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class RecoverResult(BaseModel):
    success: bool
    fault_id: str = ""
    error: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class ScenarioResult(BaseModel):
    scenario_name: str
    success: bool
    inject_time: datetime | None = None
    recover_time: datetime | None = None
    verification_passed: bool | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class RollbackEntryStatus(str, Enum):
    ACTIVE = "active"
    RECOVERED = "recovered"
    FAILED = "failed"


class RollbackEntry(BaseModel):
    fault_id: str
    injected_at: datetime = Field(default_factory=datetime.now)
    channel: str
    target: str
    inject_action: str
    inject_params: dict[str, Any] = Field(default_factory=dict)
    recover_action: str
    recover_params: dict[str, Any] = Field(default_factory=dict)
    status: RollbackEntryStatus = RollbackEntryStatus.ACTIVE

    class Config:
        use_enum_values = True


class FaultInjectorConfig(BaseModel):
    global_: GlobalConfig = Field(default_factory=GlobalConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    inventory: dict[str, list[TargetNodeConfig]] = Field(default_factory=dict)
    switches: dict[str, SwitchConfig] = Field(default_factory=dict)
    scenarios: dict[str, ScenarioConfig] = Field(default_factory=dict)
    combined_scenario: CombinedScenarioConfig | None = None
    config_hash: str = ""
