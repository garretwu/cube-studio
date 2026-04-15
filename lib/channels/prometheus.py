"""Prometheus query channel (unified for load-simulator and fault-injector)."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Any

from .base import BaseChannel, ChannelResult


class PrometheusChannel(BaseChannel):
    def __init__(
        self,
        base_url: str = "",
        timeout: int = 15,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
        *,
        _use_httpx: bool = False,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_baseline_stats: dict[str, dict[str, Any]] = {}
        # httpx client for fault_injector path
        self._use_httpx = _use_httpx
        self._client: Any | None = None

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "query_instant":
            value = await self.query_instant(params["promql"])
            return ChannelResult(success=True, data=value, output=str(value))
        if action == "query_range":
            value = await self.query_range(
                params["promql"],
                params["start"],
                params["end"],
                params.get("step", "15s"),
            )
            return ChannelResult(success=True, data=value, output=str(value))
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def query_instant(self, promql: str) -> float:
        if self.dry_run:
            return 0.0
        query = urllib.parse.quote(promql, safe="")
        url = f"{self.base_url}/api/v1/query?query={query}"
        data = self._http_get_json(url)
        result = data.get("data", {}).get("result", [])
        if not result:
            return None
        value = result[0].get("value", [0, "0"])[1]
        return float(value)

    async def query_range(
        self,
        promql: str,
        start: dt.datetime,
        end: dt.datetime,
        step: str = "15s",
    ) -> list[tuple[float, float]]:
        if self.dry_run:
            return []
        query = urllib.parse.quote(promql, safe="")
        url = (
            f"{self.base_url}/api/v1/query_range?query={query}"
            f"&start={start.timestamp()}&end={end.timestamp()}&step={step}"
        )
        data = self._http_get_json(url)
        result = data.get("data", {}).get("result", [])
        if not result:
            return []
        values = result[0].get("values", [])
        return [(float(ts), float(val)) for ts, val in values]

    async def collect_baseline(
        self,
        queries: dict[str, str],
        duration: int = 120,
        sample_interval: int = 15,
        interval: int | None = None,
    ) -> dict[str, list[float]]:
        # Accept both parameter names for compatibility
        actual_interval = interval if interval is not None else sample_interval

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

        out: dict[str, list[float]] = {k: [] for k in queries}
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
                    if value is None:
                        value = 0.0
                    out[name].append(value)
                except Exception as exc:
                    stats[name]["error_count"] += 1
                    stats[name]["last_error"] = str(exc)
                    out[name].append(0.0)
                stats[name]["sample_count"] += 1
            await asyncio.sleep(actual_interval)

        for name, values in out.items():
            sample_count = int(stats[name]["sample_count"])
            if sample_count > 0:
                zero_count = sum(1 for v in values if abs(v) <= 1e-12)
                stats[name]["error_ratio"] = float(stats[name]["error_count"]) / float(sample_count)
                stats[name]["zero_ratio"] = float(zero_count) / float(sample_count)

        self.last_baseline_stats = stats
        return out

    async def compare_to_baseline(
        self,
        current: dict[str, float],
        baseline: dict[str, list[float]],
        threshold: float = 0.1,
    ) -> dict[str, dict[str, float | bool]]:
        report: dict[str, dict[str, float | bool]] = {}
        for key, cur in current.items():
            base_values = baseline.get(key, [])
            base_mean = (sum(base_values) / len(base_values)) if base_values else 0.0
            if base_mean == 0:
                deviation = 0.0 if cur == 0 else 1.0
            else:
                deviation = abs(cur - base_mean) / abs(base_mean)
            report[key] = {
                "current": cur,
                "baseline_mean": base_mean,
                "deviation_ratio": deviation,
                "exceeds_threshold": deviation > threshold,
            }
        return report

    def _http_get_json(self, url: str) -> dict[str, Any]:
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text or "{}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


async def _sleep(seconds: int) -> None:
    await asyncio.sleep(seconds)
