"""Alert incident identity helpers.

Primary identity strategy:
- fingerprint + normalized starts_at (UTC ISO)

Fallback strategy:
- stable hash of fingerprint + alert_name + canonical labels
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sre_agent.models.alert import Alert

IdentitySource = Literal["fingerprint_starts_at", "fallback_hash"]


@dataclass(frozen=True)
class IncidentIdentity:
    incident_key: str
    fingerprint: str
    starts_at: str | None
    identity_source: IdentitySource


def normalize_starts_at(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_incident_key(alert: Alert) -> str:
    return build_incident_identity(alert).incident_key


def build_incident_identity(alert: Alert) -> IncidentIdentity:
    starts_at = normalize_starts_at(alert.starts_at)
    fingerprint = str(alert.fingerprint or "").strip()
    if fingerprint and starts_at:
        return IncidentIdentity(
            incident_key=f"fpst:{fingerprint}|{starts_at}",
            fingerprint=fingerprint,
            starts_at=starts_at,
            identity_source="fingerprint_starts_at",
        )

    canonical_labels = "|".join(f"{key}={value}" for key, value in sorted(alert.labels.items()))
    raw = f"{fingerprint}|{alert.alert_name}|{canonical_labels}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return IncidentIdentity(
        incident_key=f"fb:{digest}",
        fingerprint=fingerprint,
        starts_at=starts_at,
        identity_source="fallback_hash",
    )


__all__ = [
    "IdentitySource",
    "IncidentIdentity",
    "build_incident_identity",
    "build_incident_key",
    "normalize_starts_at",
]
