from __future__ import annotations

from fault_injector.config import InjectorConfig
from fault_injector.orchestrator.session import FaultStep


class VLLMLatencyScenario:
    """Placeholder scenario for future vLLM latency fault design."""

    name = "vllm_latency"

    def build_steps(self, config: InjectorConfig) -> list[FaultStep]:
        return []
