from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from fault_injector.orchestrator.session import FaultStep


@dataclass(slots=True)
class RollbackEntry:
    session_id: str
    host: str
    rollback_command: str
    executed: bool = False


class RollbackJournal:
    def __init__(self, wal_path: str) -> None:
        self.wal_path = Path(wal_path)
        self.wal_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: RollbackEntry) -> None:
        with self.wal_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def record_step(self, session_id: str, step: FaultStep) -> None:
        self.record(
            RollbackEntry(
                session_id=session_id,
                host=step.host.name,
                rollback_command=step.rollback_command,
            )
        )

    def load(self, session_id: str) -> list[RollbackEntry]:
        if not self.wal_path.exists():
            return []
        entries: list[RollbackEntry] = []
        for line in self.wal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data["session_id"] == session_id:
                entries.append(RollbackEntry(**data))
        return entries

    def remove_session(self, session_id: str) -> None:
        if not self.wal_path.exists():
            return
        retained: list[str] = []
        for line in self.wal_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if data.get("session_id") != session_id:
                retained.append(json.dumps(data, ensure_ascii=False))
        self.wal_path.write_text("\n".join(retained) + ("\n" if retained else ""), encoding="utf-8")
