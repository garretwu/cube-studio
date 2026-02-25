from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictBaseModel(BaseModel):
    """Shared model settings for config schema."""

    model_config = ConfigDict(extra="ignore")


class LoadProfileConfig(_StrictBaseModel):
    type: Literal["constant", "stepped", "spike"] = "constant"
    rps: float = 1.0
    duration_seconds: int = 120


class InferenceConfig(_StrictBaseModel):
    enabled: bool = True
    endpoint: str = "http://localhost:8000/v1/chat/completions"
    model: str = "deepseek-r1"
    load_profile: LoadProfileConfig = Field(default_factory=LoadProfileConfig)
    max_tokens: int = 512
    concurrency: int = 4
    duration_seconds: int = 60
    prompt_pool_size: int = 100
    stream: bool = False


class PipelineConfig(_StrictBaseModel):
    enabled: bool = False
    cube_studio_url: str = "http://localhost:80"
    pipeline_id: Optional[str] = None
    concurrency: int = 2
    duration_seconds: int = 120


class FineTuneConfig(_StrictBaseModel):
    enabled: bool = False
    llama_factory_url: str = "http://localhost:7860"
    duration_seconds: int = 60

    @model_validator(mode="before")
    @classmethod
    def _compat_llamafactory_url(cls, value):
        if isinstance(value, dict) and "llama_factory_url" not in value and "llamafactory_url" in value:
            data = dict(value)
            data["llama_factory_url"] = data["llamafactory_url"]
            return data
        return value


class NotebookConfig(_StrictBaseModel):
    enabled: bool = False
    jupyter_url: str = "http://localhost:8888"
    token: str = ""
    duration_seconds: int = 60


class LLMConfig(_StrictBaseModel):
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "sk-placeholder"
    model: str = "minimax-2.1"


class PrometheusQueriesConfig(_StrictBaseModel):
    enabled: bool = False
    collection_interval_seconds: int = 15
    pod_cpu: str = 'sum(rate(container_cpu_usage_seconds_total{namespace=~"$NS"}[1m]))'
    pod_memory: str = 'sum(container_memory_working_set_bytes{namespace=~"$NS"})'
    gpu_utilization: str = "DCGM_FI_DEV_GPU_UTIL"
    gpu_memory: str = "DCGM_FI_DEV_MEM_COPY_UTIL"
    istio_qps: str = 'sum(rate(istio_requests_total{namespace=~"$NS"}[1m]))'


class AdaptiveRulesConfig(_StrictBaseModel):
    pause_error_rate: float = 0.05
    breaking_error_rate: float = 0.10
    p99_breaking_multiplier: float = 10.0
    gpu_mem_reduce_pct: float = 95.0
    cpu_pause_pct: float = 95.0


class GlobalConfig(_StrictBaseModel):
    cube_studio_url: str = "http://localhost"
    prometheus_url: str = "http://localhost:9090"
    project_id: int = 1
    auth_method: Literal["username", "jwt"] = "username"
    auth_username: str = "admin"
    jwt_password: str = ""


class ChannelRuntimeConfig(_StrictBaseModel):
    timeout: int = 30
    retry_count: int = 3
    retry_backoff: float = 1.0


class ChannelConfig(_StrictBaseModel):
    cube_studio: ChannelRuntimeConfig = Field(default_factory=ChannelRuntimeConfig)
    inference: ChannelRuntimeConfig = Field(default_factory=lambda: ChannelRuntimeConfig(timeout=60, retry_count=1))
    prometheus: ChannelRuntimeConfig = Field(default_factory=lambda: ChannelRuntimeConfig(timeout=15, retry_count=1))
    notebook: ChannelRuntimeConfig = Field(default_factory=lambda: ChannelRuntimeConfig(timeout=30, retry_count=1))


class SystemCapacityConfig(_StrictBaseModel):
    max_task_cpu: int = 50
    max_task_mem_gib: int = 100
    db_pool_size: int = 300
    db_max_overflow: int = 800


class LoadSimulatorConfig(_StrictBaseModel):
    session_id: Optional[str] = None
    output_dir: str = "./reports"
    mode: Literal["single", "mixed", "stress", "soak"] = "single"
    agents: list[Literal["inference", "pipeline", "finetune", "notebook"]] = Field(
        default_factory=lambda: ["inference"]
    )
    bottleneck_analysis: bool = True
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    finetune: FineTuneConfig = Field(default_factory=FineTuneConfig)
    notebook: NotebookConfig = Field(default_factory=NotebookConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    adaptive_rules: AdaptiveRulesConfig = Field(default_factory=AdaptiveRulesConfig)
    global_config: GlobalConfig = Field(default_factory=GlobalConfig)
    channels: ChannelConfig = Field(default_factory=ChannelConfig)
    system_capacity: SystemCapacityConfig = Field(default_factory=SystemCapacityConfig)
    prometheus_queries: PrometheusQueriesConfig = Field(default_factory=PrometheusQueriesConfig)
