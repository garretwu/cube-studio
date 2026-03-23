"""Production backend interface stub for PostgreSQL/Qdrant memory storage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.memory import ConfigBaseline, IncidentRecord, LearnedPattern


@dataclass
class MemoryStorePG:
    aidc_id: str
    pg_dsn: str
    qdrant_url: str

    async def connect(self) -> None:
        raise NotImplementedError("MemoryStorePG is a production interface stub in this phase")

    async def close(self) -> None:
        return None

    async def record_incident(self, session: DiagnosisSession) -> str:
        raise NotImplementedError("MemoryStorePG is deferred to the production phase")

    async def save_incident(self, record: IncidentRecord) -> None:
        raise NotImplementedError("MemoryStorePG is deferred to the production phase")

    async def search_similar(self, symptoms: dict[str, Any] | list[str] | str, top_k: int = 3) -> list[IncidentRecord]:
        raise NotImplementedError("MemoryStorePG is deferred to the production phase")

    async def get_known_patterns(
        self,
        *,
        min_occurrence: int = 3,
        min_effective_confidence: float = 0.7,
    ) -> list[LearnedPattern]:
        _ = min_occurrence, min_effective_confidence
        raise NotImplementedError("MemoryStorePG is deferred to the production phase")

    async def get_config_baseline(self, aidc_id: str | None = None) -> ConfigBaseline | None:
        raise NotImplementedError("MemoryStorePG is deferred to the production phase")
