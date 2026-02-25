"""Inference endpoint channel."""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class InferenceResult:
    ok: bool
    status_code: int
    latency_seconds: float
    body: dict[str, Any]
    ttft_seconds: float | None = None
    error: str | None = None


class InferenceChannel:
    """Direct client for `/v1/chat/completions`."""

    def __init__(self, *, endpoint: str, model: str, timeout: int = 60) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout

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
            payload = self._post_json(body)
            latency = time.monotonic() - start
            ttft = self._estimate_ttft(payload) if stream else None
            return InferenceResult(
                ok=True,
                status_code=200,
                latency_seconds=latency,
                body=payload,
                ttft_seconds=ttft,
            )
        except Exception as exc:  # noqa: BLE001
            return InferenceResult(
                ok=False,
                status_code=500,
                latency_seconds=time.monotonic() - start,
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

    def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=data,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text or "{}")
