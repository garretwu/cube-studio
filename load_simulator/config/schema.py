from pydantic import BaseModel, Field
from typing import Literal, Optional


class LoadProfile(BaseModel):
    type: Literal["constant", "stepped", "spike"] = "constant"
    rps: float = 1.0
    duration_seconds: int = 120


class InferenceConfig(BaseModel):
    enabled: bool = True
    endpoint: str = "http://localhost:8000/v1/chat/completions"
    model: str = "deepseek-r1"
    load_profile: LoadProfile = Field(default_factory=LoadProfile)
    max_tokens: int = 512
    concurrency: int = 4


class PipelineConfig(BaseModel):
    enabled: bool = False
    cube_studio_url: str = "http://localhost:80"
    pipeline_id: Optional[str] = None


class FineTuneConfig(BaseModel):
    enabled: bool = False
    llamafactory_url: str = "http://localhost:7860"


class NotebookConfig(BaseModel):
    enabled: bool = False
    jupyter_url: str = "http://localhost:8888"
    token: str = ""


class LLMConfig(BaseModel):
    base_url: str = "http://localhost:8000/v1"
    api_key: str = "sk-placeholder"
    model: str = "minimax-2.1"


class LoadSimulatorConfig(BaseModel):
    session_id: Optional[str] = None
    output_dir: str = "./reports"
    inference: InferenceConfig = Field(default_factory=InferenceConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    finetune: FineTuneConfig = Field(default_factory=FineTuneConfig)
    notebook: NotebookConfig = Field(default_factory=NotebookConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
