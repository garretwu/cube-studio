"""Inference endpoint channel."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from load_simulator.metrics.collector import MetricCollector


@dataclass
class InferenceResult:
    ok: bool
    status_code: int
    latency_seconds: float
    body: dict[str, Any]
    ttft_seconds: float | None = None
    error: str | None = None


class InferenceChannel:
    """Direct client for `/v1/chat/completions` with async httpx and connection pool."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        timeout: int = 60,
        max_connections: int = 200,
        max_keepalive_connections: int = 200,
        metrics_collector: MetricCollector | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.metrics_collector = metrics_collector
        self._client = httpx.AsyncClient(
            base_url=endpoint,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
            ),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 256,
        stream: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> InferenceResult:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if extra:
            body.update(extra)
        start = time.monotonic()
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json=body,
            )
            latency = time.monotonic() - start
            payload = response.json()
            ok = response.status_code < 400
            ttft = self._estimate_ttft(payload) if stream and ok else None

            # Record metrics
            if self.metrics_collector:
                await self.metrics_collector.record_async(
                    "inference_latency_ms",
                    latency * 1000,
                )
                if ok:
                    await self.metrics_collector.record_async(
                        "inference_requests_total",
                        1.0,
                    )
                else:
                    await self.metrics_collector.record_async(
                        "inference_errors_total",
                        1.0,
                    )

            return InferenceResult(
                ok=ok,
                status_code=response.status_code,
                latency_seconds=latency,
                body=payload,
                ttft_seconds=ttft,
                error=f"HTTP {response.status_code}" if not ok else None,
            )
        except Exception as exc:  # noqa: BLE001
            latency = time.monotonic() - start

            # Record error metrics
            if self.metrics_collector:
                await self.metrics_collector.record_async(
                    "inference_errors_total",
                    1.0,
                )

            return InferenceResult(
                ok=False,
                status_code=500,
                latency_seconds=latency,
                body={},
                error=str(exc),
            )

    def _estimate_ttft(self, payload: dict[str, Any]) -> float | None:
        usage = payload.get("usage", {})
        completion_tokens = usage.get("completion_tokens")
        if isinstance(completion_tokens, (int, float)) and completion_tokens > 0:
            total = usage.get("total_time_seconds")
            if isinstance(total, (int, float)) and total > 0:
                return float(total) / float(completion_tokens)
        return None
