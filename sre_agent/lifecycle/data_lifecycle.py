"""Lifecycle/archive helpers for persisted memory data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sre_agent.memory.store import MemoryStore


@dataclass
class DataLifecycleManager:
    memory: MemoryStore
    hot_retention_days: int = 90
    warm_retention_days: int = 365

    async def run_lifecycle(self) -> dict[str, float | int]:
        now = datetime.now(UTC)
        hot_cutoff = now - timedelta(days=self.hot_retention_days)
        archived = await self.memory.archive_incidents_before(hot_cutoff)
        await self.memory.cleanup_vectors(before=hot_cutoff)
        usage = await self.memory.get_storage_usage()
        return {
            "archived_incidents": archived,
            "storage_total_mb": usage.total_mb,
            "storage_quota_mb": usage.quota_mb,
        }
