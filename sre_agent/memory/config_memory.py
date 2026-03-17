"""POC config-memory facade for AIDC-specific baselines and thresholds."""
from __future__ import annotations

from dataclasses import dataclass

from sre_agent.models.memory import ConfigBaseline
from sre_agent.memory.store import MemoryStore


@dataclass
class ConfigMemory:
    store: MemoryStore

    async def get(self, aidc_id: str | None = None) -> ConfigBaseline | None:
        return await self.store.get_config_baseline(aidc_id=aidc_id)

    async def save(self, baseline: ConfigBaseline) -> None:
        await self.store.save_config_baseline(baseline)
