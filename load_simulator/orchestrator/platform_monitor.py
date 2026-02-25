"""PlatformMonitor — polls Cube Studio API for platform-level counts."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PlatformSnapshot:
    """Point-in-time platform resource counts."""

    timestamp: float
    active_notebooks: int = 0
    active_pipelines: int = 0
    inference_services: int = 0


class PlatformMonitor:
    """Periodically polls the Cube Studio API for active resource counts.

    Usage::

        monitor = PlatformMonitor(channel=cube_channel)
        task = asyncio.create_task(monitor.start(duration_seconds=60))
        # ... run load ...
        monitor.stop()
        await task
        print(monitor.aggregate())
    """

    def __init__(self, channel: Any, interval_seconds: float = 30.0) -> None:
        self._channel = channel
        self._interval = interval_seconds
        self._snapshots: list[PlatformSnapshot] = []
        self._stop_event = asyncio.Event()

    @property
    def snapshots(self) -> list[PlatformSnapshot]:
        return list(self._snapshots)

    async def start(self, duration_seconds: float) -> None:
        """Collect platform metrics until *duration_seconds* elapses or stop() is called."""
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline and not self._stop_event.is_set():
            snapshot = await self._collect()
            self._snapshots.append(snapshot)
            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(self._interval, max(0.0, remaining)))

    def stop(self) -> None:
        """Signal the monitor to stop."""
        self._stop_event.set()

    async def _collect(self) -> PlatformSnapshot:
        """Query the Cube Studio API for current resource counts."""
        active_notebooks = 0
        active_pipelines = 0
        inference_services = 0

        try:
            nb_resp = await self._channel.list_notebooks()
            if isinstance(nb_resp, dict):
                items = nb_resp.get("data", nb_resp.get("result", []))
                if isinstance(items, list):
                    active_notebooks = len(items)
                elif isinstance(items, dict):
                    active_notebooks = items.get("count", 0)
            elif isinstance(nb_resp, list):
                active_notebooks = len(nb_resp)
        except Exception:
            pass

        try:
            pipe_resp = await self._channel.list_pipelines()
            active_pipelines = _extract_count(pipe_resp)
        except Exception:
            pass

        try:
            svc_resp = await self._channel.list_inference_services()
            inference_services = _extract_count(svc_resp)
        except Exception:
            pass

        return PlatformSnapshot(
            timestamp=time.time(),
            active_notebooks=active_notebooks,
            active_pipelines=active_pipelines,
            inference_services=inference_services,
        )

    def aggregate(self) -> dict[str, Any]:
        """Return the latest snapshot counts (or zeros if no snapshots)."""
        if not self._snapshots:
            return {}
        last = self._snapshots[-1]
        return {
            "platform_active_notebooks": last.active_notebooks,
            "platform_active_pipelines": last.active_pipelines,
            "platform_inference_services": last.inference_services,
        }


def _extract_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    data = payload.get("data", payload.get("result", []))
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("count", "total", "total_count"):
            value = data.get(key)
            if isinstance(value, int):
                return value
    return 0
