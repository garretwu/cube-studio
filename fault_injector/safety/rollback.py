"""
WAL rollback journal for recoverable fault injection operations.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fault_injector.config.schema import RecoverResult, RollbackEntry, RollbackEntryStatus

logger = logging.getLogger(__name__)


class RollbackJournal:
    """Append-only JSONL journal for rollback metadata."""

    def __init__(self, journal_path: Path):
        self.journal_path = Path(journal_path)
        self.entries: list[RollbackEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            with open(self.journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.entries.append(RollbackEntry(**json.loads(line)))
                    except Exception as exc:
                        logger.warning("Skipping invalid rollback record: %s", exc)
        except Exception as exc:
            logger.error("Failed to load rollback journal: %s", exc)

    def record(
        self,
        fault_id: str,
        channel: str,
        target: str,
        inject_action: str,
        inject_params: dict[str, Any],
        recover_action: str,
        recover_params: dict[str, Any] | None = None,
    ) -> RollbackEntry:
        entry = RollbackEntry(
            fault_id=fault_id,
            channel=channel,
            target=target,
            inject_action=inject_action,
            inject_params=inject_params or {},
            recover_action=recover_action,
            recover_params=recover_params or {},
            status=RollbackEntryStatus.ACTIVE,
        )
        self.entries.append(entry)
        self._append_to_disk(entry)
        return entry

    def _append_to_disk(self, entry: RollbackEntry) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _rewrite_journal(self) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.journal_path, "w", encoding="utf-8") as f:
            for entry in self.entries:
                f.write(entry.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def get_active_faults(self) -> list[RollbackEntry]:
        return [e for e in self.entries if e.status == RollbackEntryStatus.ACTIVE]

    def get_entry(self, fault_id: str) -> RollbackEntry | None:
        for entry in self.entries:
            if entry.fault_id == fault_id:
                return entry
        return None

    def update_status(self, fault_id: str, status: RollbackEntryStatus) -> None:
        for entry in self.entries:
            if entry.fault_id == fault_id:
                entry.status = status
                break
        self._rewrite_journal()

    def mark_recovered(self, fault_id: str) -> None:
        self.update_status(fault_id, RollbackEntryStatus.RECOVERED)

    def mark_failed(self, fault_id: str) -> None:
        self.update_status(fault_id, RollbackEntryStatus.FAILED)

    def count_active(self) -> int:
        return len(self.get_active_faults())

    def clear_completed(self) -> int:
        original = len(self.entries)
        self.entries = [e for e in self.entries if e.status == RollbackEntryStatus.ACTIVE]
        cleared = original - len(self.entries)
        if cleared > 0:
            self._rewrite_journal()
        return cleared

    async def recover_fault(self, fault_id: str) -> RecoverResult:
        entry = self.get_entry(fault_id)
        if entry is None:
            return RecoverResult(success=False, fault_id=fault_id, error="Rollback entry not found")
        if entry.status != RollbackEntryStatus.ACTIVE:
            return RecoverResult(success=True, fault_id=fault_id)
        # Executor-specific recovery can be added later; current behavior is safe status transition.
        self.mark_recovered(fault_id)
        return RecoverResult(success=True, fault_id=fault_id)

    async def recover_all(self) -> list[RecoverResult]:
        results: list[RecoverResult] = []
        for entry in reversed(self.entries):
            if entry.status != RollbackEntryStatus.ACTIVE:
                continue
            results.append(await self.recover_fault(entry.fault_id))
        return results

