"""Minimal config loader for CLI flows."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    aidc_id: str = "aidc-demo"
    cube_studio_url: str | None = None
    prometheus_url: str | None = None
    alertmanager_url: str | None = None
    log_level: str = "INFO"
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ]
    )
    cors_allow_methods: list[str] = Field(default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    cors_allow_headers: list[str] = Field(default_factory=lambda: ["Authorization", "Content-Type", "x-trace-id"])
    cors_expose_headers: list[str] = Field(default_factory=lambda: ["x-trace-id"])


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    jwt_secret_env: str = "JWT_SECRET"
    jwt_algorithm: str = "HS256"
    audience: str = "sre-agent"
    token_expire_seconds: int = 3600


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    guardrails_config_dir: str = "./sre_agent/guardrails"
    langgraph_checkpoint_db: str = "./data/checkpoints/sre_agent.db"


class SwitchPortDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str
    connected_to: str | None = None
    status: str = "up"
    speed_gbps: int | None = None


class SwitchDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str
    type: str | None = None
    status: str = "online"
    ports: list[SwitchPortDiscoveryConfig] = Field(default_factory=list)


class OntologyDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    switches: list[SwitchDiscoveryConfig] = Field(default_factory=list)


class OntologyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    db_path: str = "./data/ontology.db"
    discovery: OntologyDiscoveryConfig = Field(default_factory=OntologyDiscoveryConfig)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    db_dir: str = "./data/memory"
    vector_index_dir: str = "./data/memory_vectors"
    pattern_min_occurrences: int = 3
    pattern_min_confidence: float = 0.7


class KnowledgeSourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    category: str


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    persist_dir: str = "./data/knowledge_db"
    embedding_model: str = "text-embedding-3-small"
    sources: list[KnowledgeSourceConfig] = Field(default_factory=list)


class HAConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    heartbeat_interval: int = 5
    heartbeat_timeout: int = 15
    redis_url: str = "redis://redis:6379/1"
    shared_storage: str | None = None


class SLOConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    window_hours: int = 24
    diagnosis_success_threshold: float = 0.85
    false_fix_threshold: float = 0.05
    llm_success_threshold: float = 0.99
    auto_recovery_hours: int = 1


class DataLifecycleConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    hot_retention_days: int = 90
    warm_retention_days: int = 365
    cleanup_schedule: str = "0 3 * * *"


class RemediationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    wal_dir: str = "./data/wal"
    approval_timeout: int = 300
    default_policy: str = "human_confirm"
    dry_run: bool = False
    max_concurrent_remediations: int = 2


class LoopOrchestratorConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_candidates: int = 3
    cooldown_seconds: int = 30
    verification_window: int = 120
    enable_re_diagnosis: bool = True
    max_re_diagnosis_rounds: int = 1


class NATProfilingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    output_dir: str = "./data/nat_profiles"


class NATEvaluationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    dataset: str = "./sre_agent/nat/eval_dataset.jsonl"


class NATConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    workflow_config: str = "./sre_agent/nat/workflow.yml"
    profiling: NATProfilingConfig = Field(default_factory=NATProfilingConfig)
    evaluation: NATEvaluationConfig = Field(default_factory=NATEvaluationConfig)


class SREAgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    auth: AuthConfig = Field(default_factory=AuthConfig)
    agent: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    ontology: OntologyConfig = Field(default_factory=OntologyConfig)
    knowledge_base: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    remediation: RemediationConfig = Field(default_factory=RemediationConfig)
    loop_orchestrator: LoopOrchestratorConfig = Field(default_factory=LoopOrchestratorConfig)
    ha: HAConfig = Field(default_factory=HAConfig)
    slo: SLOConfig = Field(default_factory=SLOConfig)
    data_lifecycle: DataLifecycleConfig = Field(default_factory=DataLifecycleConfig)
    nat: NATConfig = Field(default_factory=NATConfig)


def load_config(path: str | Path) -> SREAgentConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return SREAgentConfig.model_validate(raw)
