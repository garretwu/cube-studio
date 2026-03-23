"""Production-phase heartbeat and leader election interfaces."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


@dataclass(frozen=True)
class HAConfig:
    enabled: bool = False
    heartbeat_interval: int = 5
    heartbeat_timeout: int = 15
    redis_url: str = "redis://redis:6379/1"
    shared_storage: str | None = None
    lock_key: str = "sre:leader"

    @property
    def lease_seconds(self) -> int:
        return self.heartbeat_timeout

    @property
    def heartbeat_interval_seconds(self) -> int:
        return self.heartbeat_interval

    @property
    def failover_threshold(self) -> int:
        return max(1, self.heartbeat_timeout // max(1, self.heartbeat_interval))


@dataclass(frozen=True)
class LeaderLease:
    holder_id: str
    acquired_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(seconds=15))

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class RedisLikeClient(Protocol):
    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None) -> Any: ...
    async def get(self, key: str) -> Any: ...
    async def delete(self, key: str) -> Any: ...


class RedisLeaderElector:
    """Redis SETNX-based leader election interface for the production phase."""

    def __init__(self, node_id: str, client: RedisLikeClient | None = None, config: HAConfig | None = None) -> None:
        self.node_id = node_id
        self.client = client
        self.config = config or HAConfig()

    async def acquire(self) -> LeaderLease:
        if self.client is None:
            raise NotImplementedError("Redis-backed leader election is a production-phase interface stub")
        ok = await self.client.set(self.config.lock_key, self.node_id, nx=True, ex=self.config.lease_seconds)
        if not ok:
            raise RuntimeError("leader lease already held")
        now = datetime.now(UTC)
        return LeaderLease(holder_id=self.node_id, acquired_at=now, expires_at=now + timedelta(seconds=self.config.lease_seconds))

    async def renew(self) -> LeaderLease:
        if self.client is None:
            raise NotImplementedError("Redis-backed leader election is a production-phase interface stub")
        await self.client.set(self.config.lock_key, self.node_id, ex=self.config.lease_seconds)
        now = datetime.now(UTC)
        return LeaderLease(holder_id=self.node_id, acquired_at=now, expires_at=now + timedelta(seconds=self.config.lease_seconds))

    async def release(self) -> None:
        if self.client is None:
            raise NotImplementedError("Redis-backed leader election is a production-phase interface stub")
        await self.client.delete(self.config.lock_key)
