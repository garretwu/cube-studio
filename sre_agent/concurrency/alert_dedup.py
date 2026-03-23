"""Alert deduplication within a short window."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sre_agent.models.alert import Alert


@dataclass
class _Entry:
    fingerprint: str
    session_id: str
    first_seen: datetime
    count: int = 1


class AlertDeduplicator:
    def __init__(self, dedup_window: int = 300) -> None:
        self.dedup_window = dedup_window
        self._seen: dict[str, _Entry] = {}

    def fingerprint(self, alert: Alert) -> str:
        raw = f"{alert.alert_name}|" + "|".join(f"{k}={v}" for k, v in sorted(alert.labels.items()))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def check_and_register(self, alert: Alert, session_id: str) -> tuple[bool, str | None]:
        self._cleanup_expired()
        fp = self.fingerprint(alert)
        if fp in self._seen:
            entry = self._seen[fp]
            entry.count += 1
            return True, entry.session_id
        self._seen[fp] = _Entry(fp, session_id, datetime.now(UTC))
        return False, None

    def _cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            key
            for key, entry in self._seen.items()
            if (now - entry.first_seen).total_seconds() > self.dedup_window
        ]
        for key in expired:
            del self._seen[key]
