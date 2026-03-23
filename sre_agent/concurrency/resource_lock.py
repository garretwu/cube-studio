"""Resource-level lock for remediation coordination."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class ResourceLockedError(RuntimeError):
    def __init__(self, resource_id: str, message: str | None = None) -> None:
        self.resource_id = resource_id
        super().__init__(message or f"resource {resource_id} is locked")


class ResourceLock:
    """Demo-phase resource lock backed by asyncio.Lock."""

    def __init__(self, backend: str = "asyncio", lock_timeout: int = 600, redis_url: str | None = None) -> None:
        self.backend = backend
        self.lock_timeout = lock_timeout
        self.redis_url = redis_url
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def acquire(self, resource_id: str, holder: str = ""):
        if self.backend != "asyncio":
            raise NotImplementedError("redis resource lock is a later phase")
        lock = self._locks.setdefault(resource_id, asyncio.Lock())
        if lock.locked():
            raise ResourceLockedError(resource_id, f"resource {resource_id} is locked by another session")
        async with lock:
            yield {"resource_id": resource_id, "holder": holder}
