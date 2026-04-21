"""Alert deduplication within a short window."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sre_agent.alerts_identity import IdentitySource, build_incident_identity
from sre_agent.models.alert import Alert

LOGGER = logging.getLogger(__name__)


@dataclass
class _Entry:
    incident_key: str
    fingerprint: str
    starts_at: str | None
    identity_source: IdentitySource
    session_id: str
    first_seen: datetime
    last_seen: datetime
    count: int = 1


class AlertDeduplicator:
    def __init__(self, dedup_window: int = 300) -> None:
        self.dedup_window = dedup_window
        self._seen: dict[str, _Entry] = {}

    def incident_key(self, alert: Alert) -> str:
        return build_incident_identity(alert).incident_key

    def check_and_register(self, alert: Alert, session_id: str) -> tuple[bool, str | None, str]:
        self._cleanup_expired()
        identity = build_incident_identity(alert)
        incident_key = identity.incident_key
        now = datetime.now(UTC)
        if incident_key in self._seen:
            entry = self._seen[incident_key]
            entry.count += 1
            entry.last_seen = now
            return True, entry.session_id, incident_key
        if identity.identity_source == "fallback_hash":
            LOGGER.warning(
                "alert dedup fallback hash used: alert_name=%s fingerprint=%s starts_at=%s incident_key=%s",
                alert.alert_name,
                identity.fingerprint,
                identity.starts_at,
                incident_key,
            )
        self._seen[incident_key] = _Entry(
            incident_key=incident_key,
            fingerprint=identity.fingerprint,
            starts_at=identity.starts_at,
            identity_source=identity.identity_source,
            session_id=session_id,
            first_seen=now,
            last_seen=now,
        )
        return False, None, incident_key

    def bind_session(self, incident_key: str, session_id: str) -> None:
        entry = self._seen.get(incident_key)
        if entry is None:
            return
        entry.session_id = session_id
        entry.last_seen = datetime.now(UTC)

    def _cleanup_expired(self) -> None:
        now = datetime.now(UTC)
        expired = [
            key
            for key, entry in self._seen.items()
            if (now - entry.last_seen).total_seconds() > self.dedup_window
        ]
        for key in expired:
            del self._seen[key]
