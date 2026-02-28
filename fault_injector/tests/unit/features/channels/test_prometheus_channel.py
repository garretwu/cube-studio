from __future__ import annotations

from datetime import datetime

import pytest

from lib.channels.prometheus import PrometheusChannel


@pytest.mark.asyncio
async def test_should_not_call_http_when_query_instant_in_dry_run(monkeypatch):
    channel = PrometheusChannel(base_url="http://localhost:9090", dry_run=True)

    async def _boom():
        raise AssertionError("HTTP client should not be created in dry-run")

    monkeypatch.setattr(channel, "_client_get", _boom)

    value = await channel.query_instant("up")
    assert value == 0.0


@pytest.mark.asyncio
async def test_should_not_call_http_when_query_range_in_dry_run(monkeypatch):
    channel = PrometheusChannel(base_url="http://localhost:9090", dry_run=True)

    async def _boom():
        raise AssertionError("HTTP client should not be created in dry-run")

    monkeypatch.setattr(channel, "_client_get", _boom)

    values = await channel.query_range("up", datetime.now(), datetime.now())
    assert values == []


@pytest.mark.asyncio
async def test_should_return_empty_samples_without_wait_when_collect_baseline_in_dry_run():
    channel = PrometheusChannel(base_url="http://localhost:9090", dry_run=True)
    data = await channel.collect_baseline({"up": "up"}, duration=120, interval=15)

    assert data == {"up": []}
