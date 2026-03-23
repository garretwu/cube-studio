"""Time-window alert correlation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sre_agent.models.alert import Alert


class AlertCorrelator:
    def __init__(self, window_seconds: int = 300) -> None:
        self.window_seconds = window_seconds

    def correlate(self, alerts: list[Alert]) -> list[list[Alert]]:
        if not alerts:
            return []
        buckets: dict[tuple[str, str], list[Alert]] = defaultdict(list)
        for alert in alerts:
            namespace = alert.labels.get("namespace", "")
            service = alert.labels.get("service", alert.labels.get("job", alert.alert_name))
            buckets[(namespace, service)].append(alert)

        groups: list[list[Alert]] = []
        for bucket in buckets.values():
            ordered = sorted(bucket, key=lambda alert: alert.starts_at)
            current: list[Alert] = [ordered[0]]
            for alert in ordered[1:]:
                delta = (alert.starts_at - current[-1].starts_at).total_seconds()
                if delta <= self.window_seconds:
                    current.append(alert)
                else:
                    groups.append(current)
                    current = [alert]
            groups.append(current)
        return groups

    def correlate_now(self, alert: Alert) -> list[Alert]:
        _ = datetime.now(UTC)
        return [alert]
