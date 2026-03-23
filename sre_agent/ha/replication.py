"""Production-phase state replication interfaces."""
from __future__ import annotations

from typing import Any


class StateReplicator:
    """Placeholder interface for state replication to shared storage/pubsub."""

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError("State replication is deferred to the production phase")

    async def replay(self, topic: str, last_event_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError("State replication is deferred to the production phase")
