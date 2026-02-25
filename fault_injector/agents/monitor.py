from __future__ import annotations


class MonitorAgent:
    """Monitoring data collector entrypoint (read-only)."""

    def collect(self) -> dict[str, object]:
        return {}
