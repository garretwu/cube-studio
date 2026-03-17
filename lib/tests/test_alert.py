from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime
from typing import Any

import pytest

from lib.channels.alert import AlertChannel
from lib.tests._real_backends import PrometheusHttpBackend
from lib.tests._real_test_support import (
    allow_write_ops,
    build_auth_headers,
    get_http_retry_count,
    get_http_timeout_sec,
    require_env,
    require_real_tests,
)
from sre_agent.models.alert import Alert, AlertSeverity


def _alert_payload(name: str, severity: str = "warning", status: str = "firing") -> dict[str, Any]:
    return {
        "labels": {
            "alertname": name,
            "severity": severity,
            "instance": "gpu-1-1",
        },
        "annotations": {
            "summary": f"{name} summary",
            "description": f"{name} description",
        },
        "startsAt": datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC).isoformat(),
        "endsAt": datetime(2026, 3, 17, 12, 5, 0, tzinfo=UTC).isoformat(),
        "fingerprint": f"fp-{name}",
        "status": {"state": status},
    }


class _FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeAlertClient:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.post_calls: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False
        self.alerts_payload: list[dict[str, Any]] = [
            _alert_payload("NodeDown", severity="warning"),
            _alert_payload("GPUOverheat", severity="critical"),
        ]
        self.silence_response: dict[str, str] = {"silenceID": "sil-001"}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> _FakeResponse:
        self.get_calls.append((path, params))
        if path == "/-/healthy":
            return _FakeResponse({"status": "ok"}, status_code=200)
        if path == "/api/v2/alerts":
            return _FakeResponse(self.alerts_payload, status_code=200)
        return _FakeResponse({"error": "not found"}, status_code=404)

    async def post(self, path: str, json: dict[str, Any] | None = None) -> _FakeResponse:
        self.post_calls.append((path, json))
        if path == "/api/v2/silences":
            return _FakeResponse(self.silence_response, status_code=200)
        return _FakeResponse({"error": "not found"}, status_code=404)

    async def aclose(self) -> None:
        self.closed = True


class _FakeMetricsBackend:
    def __init__(self) -> None:
        self.query_range_calls: list[dict[str, Any]] = []

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "1m",
    ) -> dict[str, Any]:
        self.query_range_calls.append(
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            }
        )
        return {
            "data": {
                "result": [
                    {
                        "metric": {
                            "alertname": "NodeDown",
                            "instance": "gpu-1-1",
                            "severity": "critical",
                        },
                        "values": [
                            ["1710676800", "1"],
                            ["1710676860", "0"],
                        ],
                    }
                ]
            }
        }


class TestAlertChannelUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_connect_disconnect_health(self) -> None:
        client = _FakeAlertClient()
        metrics = _FakeMetricsBackend()
        channel = AlertChannel(client=client, metrics_backend=metrics)
        self.assertTrue(await channel.connect())

        health = await channel.health_check()
        self.assertTrue(health["connected"])
        self.assertTrue(health["client"])
        self.assertTrue(health["alertmanager"])
        self.assertTrue(health["metrics"])
        self.assertTrue(await channel.disconnect())

    async def test_unit_get_active_alerts_typed(self) -> None:
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=_FakeMetricsBackend())
        await channel.connect()

        alerts = await channel.get_active_alerts()
        self.assertEqual(len(alerts), 2)
        self.assertIsInstance(alerts[0], Alert)
        self.assertEqual(alerts[1].severity, AlertSeverity.CRITICAL)

    async def test_unit_get_origin_metrics_requires_alert_name(self) -> None:
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=_FakeMetricsBackend())
        await channel.connect()

        with self.assertRaises(RuntimeError):
            await channel.get_origin_metrics("", lookback="30m")

    async def test_unit_silence_alert_invalid_duration(self) -> None:
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=_FakeMetricsBackend())
        await channel.connect()
        with self.assertRaises(RuntimeError):
            await channel.silence_alert("NodeDown", "bad-duration", "maintenance")

    async def test_unit_unknown_action(self) -> None:
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=_FakeMetricsBackend())
        result = await channel.execute("unknown_action", {})
        self.assertFalse(result.success)
        self.assertIn("unknown action", result.error)


class TestAlertChannelIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_get_active_alerts_filter_params(self) -> None:
        client = _FakeAlertClient()
        channel = AlertChannel(client=client, metrics_backend=_FakeMetricsBackend())
        await channel.connect()

        await channel.get_active_alerts({"severity": "critical", "instance": "gpu-1-1"})
        self.assertEqual(client.get_calls[0][0], "/api/v2/alerts")
        params = client.get_calls[0][1] or {}
        filters = params.get("filter") or []
        self.assertIn('severity="critical"', filters)
        self.assertIn('instance="gpu-1-1"', filters)

    async def test_integration_get_origin_metrics_query_shape(self) -> None:
        metrics = _FakeMetricsBackend()
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=metrics)
        await channel.connect()

        points = await channel.get_origin_metrics("NodeDown", lookback="6h", metric_name="ALERTS", labels={"severity": "critical"})
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]["metric"], "ALERTS")

        call = metrics.query_range_calls[0]
        self.assertIn('ALERTS{', call["query"])
        self.assertIn('alertname="NodeDown"', call["query"])
        self.assertIn('severity="critical"', call["query"])
        self.assertEqual(call["step"], "1m")

    async def test_integration_get_alert_history_uses_origin_metrics(self) -> None:
        metrics = _FakeMetricsBackend()
        channel = AlertChannel(client=_FakeAlertClient(), metrics_backend=metrics)
        await channel.connect()

        history = await channel.get_alert_history("NodeDown", lookback="6h")
        self.assertGreaterEqual(len(history), 1)
        self.assertEqual(history[0].source, "prometheus-origin-metrics")
        self.assertEqual(history[0].status.value, "resolved")
        self.assertEqual(len(metrics.query_range_calls), 1)

    async def test_integration_silence_alert_payload(self) -> None:
        client = _FakeAlertClient()
        channel = AlertChannel(client=client, metrics_backend=_FakeMetricsBackend())
        await channel.connect()

        silence_id = await channel.silence_alert("NodeDown", "2h30m", "silence during remediation")
        self.assertEqual(silence_id, "sil-001")
        path, payload = client.post_calls[0]
        self.assertEqual(path, "/api/v2/silences")
        self.assertEqual(payload["matchers"][0]["value"], "NodeDown")
        self.assertEqual(payload["comment"], "silence during remediation")


class TestAlertChannelE2E(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_alert_origin_metrics_then_silence_flow(self) -> None:
        client = _FakeAlertClient()
        metrics = _FakeMetricsBackend()
        channel = AlertChannel(client=client, metrics_backend=metrics)

        await channel.connect()
        active_alerts = await channel.get_active_alerts({"severity": "critical"})
        origin_metrics = await channel.get_origin_metrics("NodeDown", lookback="1h")
        silence_id = await channel.silence_alert("NodeDown", "1h", "automated remediation in progress")
        health = await channel.health_check()
        await channel.disconnect()

        self.assertTrue(len(active_alerts) >= 1)
        self.assertTrue(len(origin_metrics) >= 1)
        self.assertEqual(silence_id, "sil-001")
        self.assertTrue(health["alertmanager"])
        self.assertTrue(health["metrics"])
        self.assertTrue(client.closed is False)  # external injected client is not auto-closed


if __name__ == "__main__":
    unittest.main()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.real
class TestAlertChannelReal:
    def test_real_alert_readonly_flow(self) -> None:
        require_real_tests()
        env = require_env(
            "SRE_ALERTMANAGER_URL",
            "SRE_PROMETHEUS_URL",
            "SRE_TEST_ALERT_NAME",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        try:
            import httpx
        except Exception as exc:  # pragma: no cover - runtime dependency
            pytest.skip(f"httpx is required for real alert tests: {exc}")

        alert_client = httpx.AsyncClient(
            base_url=env["SRE_ALERTMANAGER_URL"].rstrip("/"),
            timeout=timeout_sec,
            headers=build_auth_headers("ALERTMANAGER"),
        )
        metrics_backend = PrometheusHttpBackend(
            base_url=env["SRE_PROMETHEUS_URL"],
            timeout=timeout_sec,
            retries=retry_count,
            headers=build_auth_headers("PROMETHEUS"),
        )
        channel = AlertChannel(client=alert_client, metrics_backend=metrics_backend)
        alert_name = env["SRE_TEST_ALERT_NAME"]
        lookback = os.getenv("SRE_TEST_ALERT_LOOKBACK", "6h")

        try:
            assert _run(channel.connect()) is True
            health = _run(channel.health_check())
            assert health.get("connected") is True
            assert health.get("alertmanager") is True
            assert health.get("metrics") is True

            active = _run(channel.get_active_alerts({"alertname": alert_name}))
            assert len(active) > 0, "get_active_alerts returned no data for configured test alert"

            origin = _run(channel.get_origin_metrics(alert_name, lookback=lookback))
            assert len(origin) > 0, "get_origin_metrics returned no time-series points"

            history = _run(channel.get_alert_history(alert_name, lookback=lookback))
            assert len(history) > 0, "get_alert_history returned no records"
        finally:
            _run(channel.disconnect())
            _run(alert_client.aclose())
            _run(metrics_backend.aclose())

    def test_real_silence_alert_write_guard(self) -> None:
        require_real_tests()
        if not allow_write_ops():
            pytest.skip("write operations are disabled (SRE_ALLOW_WRITE_OPS=0)")

        env = require_env(
            "SRE_ALERTMANAGER_URL",
            "SRE_PROMETHEUS_URL",
            "SRE_TEST_ALERT_NAME",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        try:
            import httpx
        except Exception as exc:  # pragma: no cover - runtime dependency
            pytest.skip(f"httpx is required for real alert tests: {exc}")

        alert_client = httpx.AsyncClient(
            base_url=env["SRE_ALERTMANAGER_URL"].rstrip("/"),
            timeout=timeout_sec,
            headers=build_auth_headers("ALERTMANAGER"),
        )
        metrics_backend = PrometheusHttpBackend(
            base_url=env["SRE_PROMETHEUS_URL"],
            timeout=timeout_sec,
            retries=retry_count,
            headers=build_auth_headers("PROMETHEUS"),
        )
        channel = AlertChannel(client=alert_client, metrics_backend=metrics_backend)

        try:
            _run(channel.connect())
            silence_id = _run(
                channel.silence_alert(
                    env["SRE_TEST_ALERT_NAME"],
                    duration=os.getenv("SRE_TEST_SILENCE_DURATION", "10m"),
                    comment=os.getenv("SRE_TEST_SILENCE_COMMENT", "real-test temporary silence"),
                )
            )
            assert isinstance(silence_id, str)
            assert len(silence_id.strip()) > 0
        finally:
            _run(channel.disconnect())
            _run(alert_client.aclose())
            _run(metrics_backend.aclose())
