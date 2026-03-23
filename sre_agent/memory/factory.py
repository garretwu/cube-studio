"""Memory store factory respecting Demo/Prod boundaries."""
from __future__ import annotations

from sre_agent.memory.store import MemoryStore
from sre_agent.memory.store_pg import MemoryStorePG


def create_memory_store(
    aidc_id: str,
    *,
    db_dir: str = "./memory",
    mode: str = "demo",
    pg_dsn: str | None = None,
    qdrant_url: str | None = None,
):
    if mode == "prod":
        if not pg_dsn or not qdrant_url:
            raise ValueError("pg_dsn and qdrant_url are required for prod mode")
        return MemoryStorePG(aidc_id=aidc_id, pg_dsn=pg_dsn, qdrant_url=qdrant_url)
    return MemoryStore(aidc_id=aidc_id, db_dir=db_dir)
