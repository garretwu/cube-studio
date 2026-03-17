from __future__ import annotations

import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from lib.channels.base import SafetyViolationError
from lib.channels.log import LogChannel
from lib.tests._real_backends import LokiHttpBackend
from lib.tests._real_test_support import (
    build_auth_headers,
    get_http_retry_count,
    get_http_timeout_sec,
    require_env,
    require_real_tests,
)


class _FakeLokiBackend:
    def __init__(self) -> None:
        self.query_calls: list[dict[str, Any]] = []
        self.query_range_calls: list[dict[str, Any]] = []

    async def query(self, query: str, limit: int = 100, direction: str = "backward") -> dict[str, Any]:
        self.query_calls.append({"query": query, "limit": limit, "direction": direction})
        return self._build_payload(query)

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
        limit: int = 1000,
        direction: str = "backward",
    ) -> dict[str, Any]:
        self.query_range_calls.append(
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit,
                "direction": direction,
            }
        )
        return self._build_payload(query)

    @staticmethod
    def _build_payload(query: str) -> dict[str, Any]:
        if 'namespace="default",pod="pod-a"' in query:
            return {
                "data": {
                    "result": [
                        {
                            "stream": {"namespace": "default", "pod": "pod-a"},
                            "values": [
                                ["1710676800", "INFO startup complete"],
                                ["1710676801", "ERROR GPU timeout"],
                                ["1710676802", "WARN retrying"],
                            ],
                        }
                    ]
                }
            }
        if '|~ "ERROR"' in query and 'namespace="default"' in query:
            return {
                "data": {
                    "result": [
                        {
                            "stream": {"namespace": "default", "pod": "pod-a", "app": "vllm"},
                            "values": [["1710676801", "ERROR GPU timeout"]],
                        },
                        {
                            "stream": {"namespace": "default", "pod": "pod-b", "app": "vllm"},
                            "values": [["1710676803", "ERROR network drop"]],
                        },
                    ]
                }
            }
        if 'filename="/var/log/syslog"' in query:
            return {
                "data": {
                    "result": [
                        {
                            "stream": {"node": "node-1", "filename": "/var/log/syslog"},
                            "values": [
                                ["1710676804", "system line-1"],
                                ["1710676805", "system line-2"],
                            ],
                        }
                    ]
                }
            }
        if 'source="dmesg"' in query:
            return {
                "data": {
                    "result": [
                        {
                            "stream": {"node": "node-1", "source": "dmesg"},
                            "values": [
                                ["1710676806", "kernel: timeout on mlx5"],
                                ["1710676807", "kernel: recovered"],
                            ],
                        }
                    ]
                }
            }
        return {"data": {"result": []}}


class TestLogChannelUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_connect_disconnect_health(self) -> None:
        channel = LogChannel(loki=_FakeLokiBackend())
        self.assertTrue(await channel.connect())
        status = await channel.health_check()
        self.assertTrue(status["connected"])
        self.assertTrue(status["loki"])
        self.assertTrue(status["queryable"])
        self.assertTrue(await channel.disconnect())

    async def test_unit_reject_non_whitelist_log_path(self) -> None:
        channel = LogChannel(loki=_FakeLokiBackend())
        with self.assertRaises(SafetyViolationError):
            await channel.read_system_log("node-1", log_path="/tmp/custom.log")

    async def test_unit_reject_long_dmesg_filter(self) -> None:
        channel = LogChannel(loki=_FakeLokiBackend())
        with self.assertRaises(SafetyViolationError):
            await channel.read_dmesg("node-1", filter_str="x" * 101)

    async def test_unit_reject_bad_pod_selector(self) -> None:
        channel = LogChannel(loki=_FakeLokiBackend())
        with self.assertRaises(RuntimeError):
            await channel.search_pod_logs("ERROR", "default", pod_selector="app:vllm")

    async def test_unit_unknown_action(self) -> None:
        channel = LogChannel(loki=_FakeLokiBackend())
        result = await channel.execute("unknown_action", {})
        self.assertFalse(result.success)
        self.assertIn("unknown action", result.error)


class TestLogChannelIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_read_pod_logs_maps_to_loki_range_query(self) -> None:
        loki = _FakeLokiBackend()
        channel = LogChannel(loki=loki)

        logs = await channel.read_pod_logs("pod-a", "default", tail=2, since="5m")
        self.assertEqual(logs, ["INFO startup complete", "ERROR GPU timeout"])
        self.assertEqual(len(loki.query_range_calls), 1)

        call = loki.query_range_calls[0]
        self.assertIn('namespace="default"', call["query"])
        self.assertIn('pod="pod-a"', call["query"])
        self.assertEqual(call["limit"], 2)
        self.assertEqual(call["step"], "30s")

    async def test_integration_search_pod_logs_maps_pattern_and_selector(self) -> None:
        loki = _FakeLokiBackend()
        channel = LogChannel(loki=loki)

        matches = await channel.search_pod_logs("ERROR", "default", pod_selector="app=vllm")
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["pod"], "pod-a")

        query = loki.query_range_calls[0]["query"]
        self.assertIn('namespace="default"', query)
        self.assertIn('app="vllm"', query)
        self.assertIn('|~ "ERROR"', query)

    async def test_integration_read_system_log_uses_loki_not_ssh(self) -> None:
        loki = _FakeLokiBackend()
        channel = LogChannel(loki=loki)

        lines = await channel.read_system_log("node-1", log_path="/var/log/syslog", tail=2)
        self.assertEqual(lines, ["system line-1", "system line-2"])
        self.assertEqual(len(loki.query_calls), 1)
        query = loki.query_calls[0]["query"]
        self.assertIn('job="system"', query)
        self.assertIn('filename="/var/log/syslog"', query)

    async def test_integration_query_logs_normalizes_stream_entries(self) -> None:
        loki = _FakeLokiBackend()
        channel = LogChannel(loki=loki)

        start = datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC)
        end = start + timedelta(minutes=5)
        rows = await channel.query_logs('{namespace="default",pod="pod-a"}', start=start, end=end, limit=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["line"], "ERROR GPU timeout")


class TestLogChannelE2E(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_mocked_log_triage_flow(self) -> None:
        loki = _FakeLokiBackend()
        channel = LogChannel(loki=loki)
        await channel.connect()

        pod_errors = await channel.search_pod_logs("ERROR", "default", pod_selector="app=vllm")
        syslog_lines = await channel.read_system_log("node-1", log_path="/var/log/syslog", tail=20)
        dmesg_lines = await channel.read_dmesg("node-1", filter_str="timeout")
        raw_rows = await channel.query_logs('{namespace="default",pod="pod-a"}', limit=2)
        health = await channel.health_check()

        self.assertGreaterEqual(len(pod_errors), 1)
        self.assertGreaterEqual(len(syslog_lines), 1)
        self.assertGreaterEqual(len(dmesg_lines), 1)
        self.assertEqual(len(raw_rows), 2)
        self.assertTrue(health["connected"])
        self.assertTrue(await channel.disconnect())


if __name__ == "__main__":
    unittest.main()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.real
class TestLogChannelReal:
    def test_real_logchannel_readonly_flow(self) -> None:
        require_real_tests()
        env = require_env(
            "SRE_LOKI_URL",
            "SRE_TEST_NAMESPACE",
            "SRE_TEST_POD",
            "SRE_TEST_NODE",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        namespace = env["SRE_TEST_NAMESPACE"]
        pod = env["SRE_TEST_POD"]
        node = env["SRE_TEST_NODE"]
        pod_selector = os.getenv("SRE_TEST_POD_SELECTOR", f"pod={pod}")
        pattern = os.getenv("SRE_TEST_LOG_PATTERN", "ERROR")
        logql = os.getenv("SRE_TEST_LOGQL", f'{{namespace="{namespace}",pod="{pod}"}}')
        log_path = os.getenv("SRE_TEST_LOG_PATH", "/var/log/syslog")

        loki = LokiHttpBackend(
            base_url=env["SRE_LOKI_URL"],
            timeout=timeout_sec,
            retries=retry_count,
            headers=build_auth_headers("LOKI"),
        )
        channel = LogChannel(loki=loki)

        try:
            assert _run(channel.connect()) is True
            health = _run(channel.health_check())
            assert health.get("connected") is True
            assert health.get("loki") is True

            rows = _run(channel.query_logs(logql, limit=50))
            assert len(rows) > 0, "query_logs returned no rows for selector-based real test"

            pod_logs = _run(channel.read_pod_logs(pod, namespace, tail=50, since="15m"))
            assert len(pod_logs) > 0, "read_pod_logs returned no logs for configured test pod"

            matches = _run(channel.search_pod_logs(pattern, namespace, pod_selector=pod_selector))
            assert len(matches) > 0, "search_pod_logs returned no matches for selector-based real test"

            syslog_lines = _run(channel.read_system_log(node, log_path=log_path, tail=20))
            assert isinstance(syslog_lines, list)

            dmesg_lines = _run(channel.read_dmesg(node, filter_str=os.getenv("SRE_TEST_DMESG_FILTER", "error")))
            assert isinstance(dmesg_lines, list)
        finally:
            _run(channel.disconnect())
            _run(loki.aclose())
