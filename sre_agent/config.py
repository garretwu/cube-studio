"""Minimal config loader for CLI flows."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    aidc_id: str = "aidc-demo"


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


class SREAgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    ontology: OntologyConfig = Field(default_factory=OntologyConfig)
    knowledge_base: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    ha: HAConfig = Field(default_factory=HAConfig)
    slo: SLOConfig = Field(default_factory=SLOConfig)
    data_lifecycle: DataLifecycleConfig = Field(default_factory=DataLifecycleConfig)


def load_config(path: str | Path) -> SREAgentConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return SREAgentConfig.model_validate(raw)
