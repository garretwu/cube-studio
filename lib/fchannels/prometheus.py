"""
Prometheus channel.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from lib.fchannels.base import BaseChannel
from fault_injector.config.schema import ChannelResult


class PrometheusChannel(BaseChannel):
    """Prometheus HTTP API queries (read-only)."""

    def __init__(self, base_url: str, dry_run: bool = False, timeout: int = 30):
        super().__init__(dry_run=dry_run, wal=None, guard=None)
        self.base_url = base_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self.last_baseline_stats: dict[str, dict[str, Any]] = {}

    async def _client_get(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self._client

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "query_instant":
            value = await self.query_instant(params["promql"])
            return ChannelResult(success=True, output=str(value))
        if action == "query_range":
            result = await self.query_range(
                params["promql"],
                params["start"],
                params["end"],
                params.get("step", "15s"),
            )
            return ChannelResult(success=True, output=str(result))
        return ChannelResult(success=False, error=f"Unknown action: {action}")

    async def query_instant(self, promql: str) -> float:
        if self.dry_run:
            return 0.0
        client = await self._client_get()
        resp = await client.get("/api/v1/query", params={"query": promql})
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("data", {}).get("result", [])
        if not result:
            return 0.0
        return float(result[0]["value"][1])

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> list[tuple[float, float]]:
        if self.dry_run:
            return []
        client = await self._client_get()
        resp = await client.get(
            "/api/v1/query_range",
            params={
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("data", {}).get("result", [])
        if not result:
            return []
        return [(float(ts), float(val)) for ts, val in result[0].get("values", [])]

    async def collect_baseline(
        self,
        queries: dict[str, str],
        duration: int = 120,
        interval: int = 15,
    ) -> dict[str, list[float]]:
        if self.dry_run:
            self.last_baseline_stats = {
                name: {
                    "sample_count": 0,
                    "error_count": 0,
                    "error_ratio": 0.0,
                    "zero_ratio": 0.0,
                    "last_error": "",
                }
                for name in queries
            }
            return {name: [] for name in queries}
        out: dict[str, list[float]] = {name: [] for name in queries}
        stats: dict[str, dict[str, Any]] = {
            name: {
                "sample_count": 0,
                "error_count": 0,
                "error_ratio": 0.0,
                "zero_ratio": 0.0,
                "last_error": "",
            }
            for name in queries
        }
        end_ts = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < end_ts:
            for name, promql in queries.items():
                try:
                    value = await self.query_instant(promql)
                    out[name].append(value)
                except Exception as exc:
                    stats[name]["error_count"] += 1
                    stats[name]["last_error"] = str(exc)
                    out[name].append(0.0)
                stats[name]["sample_count"] += 1
            await asyncio.sleep(interval)
        for name, values in out.items():
            sample_count = int(stats[name]["sample_count"])
            if sample_count > 0:
                zero_count = sum(1 for v in values if abs(v) <= 1e-12)
                stats[name]["error_ratio"] = float(stats[name]["error_count"]) / float(sample_count)
                stats[name]["zero_ratio"] = float(zero_count) / float(sample_count)
        self.last_baseline_stats = stats
        return out

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
