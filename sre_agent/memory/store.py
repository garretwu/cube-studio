"""Demo/POC memory store using sqlite plus semantic lookup."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sre_agent.memory.incident import build_incident_record
from sre_agent.memory.pattern import merge_pattern_from_incident
from sre_agent.models.diagnosis import DiagnosisSession
from sre_agent.models.memory import ConfigBaseline, IncidentRecord, LearnedPattern

try:
    import aiosqlite  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import aiosqlite

try:
    import chromadb  # type: ignore
except ImportError:  # pragma: no cover - exercised in local env fallback
    from sre_agent._compat import chromadb


@dataclass(frozen=True)
class StorageUsage:
    total_mb: float
    quota_mb: float


class MemoryStore:
    """Incident/pattern/config memory backed by sqlite and vector-style search."""

    def __init__(self, aidc_id: str, db_dir: str = "./memory") -> None:
        self.aidc_id = aidc_id
        self.db_dir = db_dir
        self.db_path = ":memory:" if db_dir == ":memory:" else str(Path(db_dir) / f"{aidc_id}.db")
        self._db: Any | None = None
        if db_dir == ":memory:":
            self.vector_index = chromadb.EphemeralClient()
        else:
            Path(db_dir).mkdir(parents=True, exist_ok=True)
            self.vector_index = chromadb.PersistentClient(path=str(Path(db_dir) / f"{aidc_id}_vectors"))
        self.incidents_collection = self.vector_index.get_or_create_collection("incidents")

    async def connect(self) -> None:
        if self.db_path != ":memory:":
            os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = sqlite3.Row
        await self._init_tables()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _init_tables(self) -> None:
        assert self._db is not None
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                aidc_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incident_archive (
                incident_id TEXT PRIMARY KEY,
                aidc_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS patterns (
                pattern_id TEXT PRIMARY KEY,
                aidc_id TEXT NOT NULL,
                symptom_signature TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS config_baselines (
                aidc_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL
            );
            """
        )
        await self._db.commit()

    async def record_incident(self, session: DiagnosisSession) -> str:
        record = build_incident_record(session, self.aidc_id)
        await self.save_incident(record)
        return record.incident_id

    async def save_incident(self, record: IncidentRecord) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT OR REPLACE INTO incidents(incident_id, aidc_id, timestamp, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                record.incident_id,
                record.aidc_id,
                record.timestamp.isoformat(),
                record.model_dump_json(),
            ),
        )
        await self._db.commit()
        await asyncio.to_thread(
            self.incidents_collection.upsert,
            documents=[f"{record.root_cause} | {' '.join(record.symptoms)}"],
            metadatas=[
                {
                    "incident_id": record.incident_id,
                    "aidc_id": record.aidc_id,
                    "root_cause": record.root_cause,
                    "timestamp": record.timestamp.isoformat(),
                }
            ],
            ids=[record.incident_id],
        )
        await self._update_patterns(record)

    async def get_incident(self, incident_id: str) -> IncidentRecord | None:
        assert self._db is not None
        row = await (await self._db.execute("SELECT payload_json FROM incidents WHERE incident_id = ?", (incident_id,))).fetchone()
        if row is None:
            return None
        return IncidentRecord.model_validate_json(row[0])

    async def list_incidents(self, last: int = 10) -> list[IncidentRecord]:
        assert self._db is not None
        rows = await (
            await self._db.execute(
                "SELECT payload_json FROM incidents WHERE aidc_id = ? ORDER BY timestamp DESC LIMIT ?",
                (self.aidc_id, last),
            )
        ).fetchall()
        return [IncidentRecord.model_validate_json(row[0]) for row in rows]

    async def search_similar(self, symptoms: dict[str, Any] | list[str] | str, top_k: int = 3) -> list[IncidentRecord]:
        if isinstance(symptoms, dict):
            query = " ".join(f"{key}={value}" for key, value in sorted(symptoms.items()))
        elif isinstance(symptoms, list):
            query = " ".join(symptoms)
        else:
            query = symptoms
        results = await asyncio.to_thread(self.incidents_collection.query, query_texts=[query], n_results=top_k)
        incident_ids = results.get("ids", [[]])[0]
        incidents: list[IncidentRecord] = []
        for incident_id in incident_ids:
            record = await self.get_incident(incident_id)
            if record is not None:
                incidents.append(record)
        return incidents

    async def save_pattern(self, pattern: LearnedPattern) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO patterns(pattern_id, aidc_id, symptom_signature, payload_json) VALUES (?, ?, ?, ?)",
            (
                pattern.pattern_id,
                pattern.aidc_id,
                json.dumps(pattern.symptom_signature, sort_keys=True),
                pattern.model_dump_json(),
            ),
        )
        await self._db.commit()

    async def get_pattern_by_symptoms(self, symptoms: list[str]) -> LearnedPattern | None:
        assert self._db is not None
        signature = json.dumps(sorted(symptoms), sort_keys=True)
        row = await (
            await self._db.execute(
                "SELECT payload_json FROM patterns WHERE aidc_id = ? AND symptom_signature = ?",
                (self.aidc_id, signature),
            )
        ).fetchone()
        if row is None:
            return None
        return LearnedPattern.model_validate_json(row[0])

    async def get_all_patterns(self) -> list[LearnedPattern]:
        assert self._db is not None
        rows = await (await self._db.execute("SELECT payload_json FROM patterns WHERE aidc_id = ?", (self.aidc_id,))).fetchall()
        return [LearnedPattern.model_validate_json(row[0]) for row in rows]

    async def get_known_patterns(self) -> list[LearnedPattern]:
        now = datetime.now(UTC)
        return [pattern for pattern in await self.get_all_patterns() if pattern.should_suggest(now)]

    async def _update_patterns(self, record: IncidentRecord) -> None:
        existing = await self.get_pattern_by_symptoms(record.symptoms)
        merged = merge_pattern_from_incident(existing, record)
        await self.save_pattern(merged)

    async def save_config_baseline(self, baseline: ConfigBaseline) -> None:
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO config_baselines(aidc_id, payload_json) VALUES (?, ?)",
            (baseline.aidc_id, baseline.model_dump_json()),
        )
        await self._db.commit()

    async def get_config_baseline(self, aidc_id: str | None = None) -> ConfigBaseline | None:
        target = aidc_id or self.aidc_id
        assert self._db is not None
        row = await (await self._db.execute("SELECT payload_json FROM config_baselines WHERE aidc_id = ?", (target,))).fetchone()
        if row is None:
            return None
        return ConfigBaseline.model_validate_json(row[0])

    async def archive_incidents_before(self, cutoff: datetime) -> int:
        assert self._db is not None
        rows = await (
            await self._db.execute(
                "SELECT incident_id, aidc_id, timestamp, payload_json FROM incidents WHERE aidc_id = ? AND timestamp < ?",
                (self.aidc_id, cutoff.isoformat()),
            )
        ).fetchall()
        if not rows:
            return 0
        await self._db.executemany(
            "INSERT OR REPLACE INTO incident_archive(incident_id, aidc_id, timestamp, payload_json) VALUES (?, ?, ?, ?)",
            [(row[0], row[1], row[2], row[3]) for row in rows],
        )
        await self._db.execute(
            "DELETE FROM incidents WHERE aidc_id = ? AND timestamp < ?",
            (self.aidc_id, cutoff.isoformat()),
        )
        await self._db.commit()
        await self.cleanup_vectors(before=cutoff)
        return len(rows)

    async def cleanup_vectors(self, before: datetime) -> None:
        existing = await asyncio.to_thread(self.incidents_collection.get)
        ids = []
        for doc_id, metadata in zip(existing.get("ids", []), existing.get("metadatas", []), strict=False):
            timestamp = metadata.get("timestamp")
            if timestamp and datetime.fromisoformat(timestamp) < before:
                ids.append(doc_id)
        if ids:
            await asyncio.to_thread(self.incidents_collection.delete, ids=ids)

    async def get_storage_usage(self, quota_mb: float = 1024.0) -> StorageUsage:
        if self.db_path == ":memory:" or not os.path.exists(self.db_path):
            total_mb = 0.0
        else:
            total_mb = os.path.getsize(self.db_path) / (1024 * 1024)
        return StorageUsage(total_mb=total_mb, quota_mb=quota_mb)
