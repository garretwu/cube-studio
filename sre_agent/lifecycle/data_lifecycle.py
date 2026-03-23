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
    cleanup_schedule: str = "0 3 * * *"
    warning_threshold_ratio: float = 0.8

    async def run_lifecycle(self) -> dict[str, float | int]:
        now = datetime.now(UTC)
        hot_cutoff = now - timedelta(days=self.hot_retention_days)
        archived = await self.memory.archive_incidents_before(hot_cutoff)
        await self.memory.cleanup_vectors(before=hot_cutoff)
        inactivated = 0
        for pattern in await self.memory.get_all_patterns():
            if pattern.status == "inactive":
                continue
            if pattern.effective_confidence(now) >= 0.1:
                continue
            await self.memory.save_pattern(pattern.model_copy(update={"status": "inactive"}))
            inactivated += 1
        usage = await self.memory.get_storage_usage()
        return {
            "archived_incidents": archived,
            "inactive_patterns": inactivated,
            "storage_total_mb": usage.total_mb,
            "storage_quota_mb": usage.quota_mb,
            "storage_warning": int(usage.total_mb > usage.quota_mb * self.warning_threshold_ratio),
        }
