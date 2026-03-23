"""Local rollback journal for remediation actions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WALRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fault_id: str
    plan_id: str
    step_id: int
    recover_action: str
    recover_params: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = "active"


class RollbackJournal:
    """Append-only JSONL rollback journal."""

    def __init__(self, journal_path: str | Path = "./data/wal/remediation.jsonl") -> None:
        self.journal_path = Path(journal_path)
        self.entries: list[WALRecord] = []
        self._load()

    def _load(self) -> None:
        if not self.journal_path.exists():
            return
        for line in self.journal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            self.entries.append(WALRecord.model_validate_json(line))

    def record(self, fault_id: str, plan_id: str, step_id: int, recover_action: str, recover_params: dict[str, Any] | None = None) -> WALRecord:
        entry = WALRecord(
            fault_id=fault_id,
            plan_id=plan_id,
            step_id=step_id,
            recover_action=recover_action,
            recover_params=recover_params or {},
        )
        self.entries.append(entry)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    async def recover_all(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        for entry in reversed(self.entries):
            if entry.status != "active":
                continue
            entry.status = "recovered"
            recovered.append(
                {
                    "fault_id": entry.fault_id,
                    "recover_action": entry.recover_action,
                    "recover_params": dict(entry.recover_params),
                }
            )
        self._rewrite()
        return recovered

    def _rewrite(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "w", encoding="utf-8") as handle:
            for entry in self.entries:
                handle.write(entry.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
