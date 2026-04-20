"""Minimal config loader for CLI flows."""
from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sre_agent.alerts_filter import DEFAULT_BLOCKED_ALERT_NAMES


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    aidc_id: str = "aidc-demo"
    cube_studio_url: str | None = None
    prometheus_url: str | None = None
    alertmanager_url: str | None = None
    loki_url: str | None = None
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
    cors_expose_headers: list[str] = Field(default_factory=lambda: ["x-trace-id", "x-server-boot-id"])
    blocked_alert_names: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLOCKED_ALERT_NAMES)
    )
    auto_diagnose_alert_names: list[str] = Field(
        default_factory=lambda: []
    )
    auto_diagnose_delay_seconds: float = 10.0
    auto_diagnose_entity_correlation_count: int = 2


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    jwt_secret_env: str = "JWT_SECRET"
    jwt_algorithm: str = "HS256"
    audience: str = "sre-agent"
    token_expire_seconds: int = 3600


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    provider: str | None = None
    fallback_models: list[str] = Field(default_factory=list)


class AgentRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    guardrails_config_dir: str = "./sre_agent/guardrails"
    langgraph_checkpoint_db: str = "./data/checkpoints/sre_agent.db"
    reasoning_context_strategy: str = "state_rebuilt"
    reasoning_overflow_behavior: str = "fail"
    reasoning_input_target_tokens: int = 180000
    reasoning_model_family: str | None = None
    ttft_external_process_default_node: str | None = None
    reason_context_char_budget: int = 2400
    tool_message_char_limit: int = 1200
    reason_preserve_recent_messages: int = 6
    step_timeout_sec: float = 120.0
    total_timeout_sec: float = 600.0
    max_steps: int = 50


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

    mode: str = "static"
    auto_discovery: bool = True
    refresh_interval_seconds: int = 120
    unified_inventory_path: str | None = None
    live_inventory_path: str = "fault_injector/fault-injector-test.yaml"
    live_fallback_to_static: bool = True
    k8s_cluster_name: str = "lab-cluster"
    k8s_namespaces: list[str] = Field(default_factory=list)
    prometheus_targets: dict[str, str] = Field(default_factory=dict)
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

    provider: str = "local"
    persist_dir: str = "./data/knowledge_db"
    embedding_model: str = "text-embedding-3-small"
    base_url: str | None = None
    api_key: str | None = None
    dataset_id: str | None = None
    runbook_dataset_id: str | None = None
    timeout: float = 15.0
    retries: int = 2
    api_prefix: str = "/v1"
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
    session_store_dir: str = "./data/sessions"
    approval_timeout: int = 300
    default_policy: str = "human_confirm"
    dry_run: bool = False
    max_concurrent_remediations: int = 2
    execution_mode: str = "real"
    observation_seconds: int = 600
    observation_poll_seconds: int = 10
    execution_timeout_seconds: int = 900


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


class ToolChannelConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    required: bool = False


class ToolRuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: str = "degraded"
    core_required_channels: list[str] = Field(default_factory=lambda: ["ssh", "k8s", "prometheus"])
    channels: dict[str, ToolChannelConfig] = Field(default_factory=dict)


class SREAgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    auth: AuthConfig = Field(default_factory=AuthConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
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
    tool_runtime: ToolRuntimeConfig = Field(default_factory=ToolRuntimeConfig)


DEFAULT_OPENAI_COMPATIBLE_MODEL = "MiniMax-M2.7"
GLM_PROVIDER_KEY = "glm"
OPENAI_COMPATIBLE_PROVIDER_KEY = "openai_compatible"
GLM_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
GLM_DEFAULT_MODEL = "glm-5.1"
GLM_DEFAULT_FALLBACK_MODELS = ["glm-5-turbo"]


def _normalize_llm_provider(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"glm", "zhipu", "bigmodel"}:
        return GLM_PROVIDER_KEY
    return OPENAI_COMPATIBLE_PROVIDER_KEY


def _split_fallback_models(raw: str | None) -> list[str]:
    if not isinstance(raw, str):
        return []
    models: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        model = part.strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append(model)
    return models


def resolve_llm_runtime_settings(env: MutableMapping[str, str] | None = None) -> dict[str, object]:
    target_env = env if env is not None else os.environ
    api_key = str(target_env.get("SRE_OPENAI_API_KEY", "") or "").strip()
    api_key_source = "SRE_OPENAI_API_KEY"
    if not api_key:
        api_key = str(target_env.get("OPENAI_API_KEY", "") or "").strip()
        api_key_source = "OPENAI_API_KEY" if api_key else "none"

    provider = _normalize_llm_provider(str(target_env.get("SRE_LLM_PROVIDER", "") or ""))
    base_url = str(target_env.get("SRE_OPENAI_BASE_URL", "") or "").strip() or None
    model = (
        str(target_env.get("SRE_LLM_MODEL", "") or "").strip()
        or str(target_env.get("OPENAI_MODEL", "") or "").strip()
    )
    fallback_models = _split_fallback_models(str(target_env.get("SRE_LLM_FALLBACK_MODELS", "") or ""))

    if provider == GLM_PROVIDER_KEY:
        if base_url is None:
            base_url = GLM_DEFAULT_BASE_URL
        if not model:
            model = GLM_DEFAULT_MODEL
        if not fallback_models:
            fallback_models = list(GLM_DEFAULT_FALLBACK_MODELS)
    else:
        if not model:
            model = DEFAULT_OPENAI_COMPATIBLE_MODEL

    deduped_fallback_models: list[str] = []
    seen_models: set[str] = {model}
    for item in fallback_models:
        if item in seen_models:
            continue
        seen_models.add(item)
        deduped_fallback_models.append(item)

    return {
        "provider": provider,
        "api_key": api_key,
        "api_key_source": api_key_source,
        "base_url": base_url,
        "model": model,
        "fallback_models": deduped_fallback_models,
    }


def apply_llm_env_from_config(
    config: SREAgentConfig,
    env: MutableMapping[str, str] | None = None,
    *,
    only_if_missing: bool = True,
) -> dict[str, str]:
    target_env = env if env is not None else os.environ
    llm = config.llm
    candidates: dict[str, str] = {}
    if isinstance(llm.api_key, str) and llm.api_key.strip():
        candidates["SRE_OPENAI_API_KEY"] = llm.api_key.strip()
    provider = _normalize_llm_provider(llm.provider)
    candidates["SRE_LLM_PROVIDER"] = provider

    configured_base_url = llm.base_url.strip() if isinstance(llm.base_url, str) else ""
    configured_model = llm.model.strip() if isinstance(llm.model, str) else ""
    configured_fallback = [
        item.strip()
        for item in (llm.fallback_models or [])
        if isinstance(item, str) and item.strip()
    ]

    if configured_base_url:
        candidates["SRE_OPENAI_BASE_URL"] = configured_base_url
    if configured_model:
        candidates["SRE_LLM_MODEL"] = configured_model
    if configured_fallback:
        candidates["SRE_LLM_FALLBACK_MODELS"] = ",".join(configured_fallback)

    if provider == GLM_PROVIDER_KEY:
        candidates.setdefault("SRE_OPENAI_BASE_URL", GLM_DEFAULT_BASE_URL)
        candidates.setdefault("SRE_LLM_MODEL", GLM_DEFAULT_MODEL)
        candidates.setdefault("SRE_LLM_FALLBACK_MODELS", ",".join(GLM_DEFAULT_FALLBACK_MODELS))

    applied: dict[str, str] = {}
    for key, value in candidates.items():
        existing = str(target_env.get(key, "")).strip()
        if only_if_missing and existing:
            continue
        target_env[key] = value
        applied[key] = value
    return applied


def load_config(path: str | Path) -> SREAgentConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return SREAgentConfig.model_validate(raw)
