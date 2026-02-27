"""Prometheus query channel."""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Any

from .base import BaseChannel, ChannelResult


class PrometheusChannel(BaseChannel):
    def __init__(self, *, base_url: str, timeout: int = 15, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run, wal=None)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "query_instant":
            value = await self.query_instant(params["promql"])
            return ChannelResult(success=True, data=value)
        if action == "query_range":
            value = await self.query_range(
                params["promql"],
                params["start"],
                params["end"],
                params.get("step", "15s"),
            )
            return ChannelResult(success=True, data=value)
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def query_instant(self, promql: str) -> float:
        query = urllib.parse.quote(promql, safe="")
        url = f"{self.base_url}/api/v1/query?query={query}"
        data = self._http_get_json(url)
        result = data.get("data", {}).get("result", [])
        if not result:
            return 0.0
        value = result[0].get("value", [0, "0"])[1]
        return float(value)

    async def query_range(
        self,
        promql: str,
        start: dt.datetime,
        end: dt.datetime,
        step: str = "15s",
    ) -> list[tuple[float, float]]:
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
    ) -> dict[str, list[float]]:
        rounds = max(1, duration // max(1, sample_interval))
        out: dict[str, list[float]] = {k: [] for k in queries}
        for _ in range(rounds):
            for name, promql in queries.items():
                out[name].append(await self.query_instant(promql))
            await _sleep(sample_interval)
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


async def _sleep(seconds: int) -> None:
    import asyncio

    await asyncio.sleep(seconds)
