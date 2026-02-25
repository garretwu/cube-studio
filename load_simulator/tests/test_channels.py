from __future__ import annotations

import datetime as dt
import unittest

from load_simulator.channels.base import BaseChannel, ChannelResult, SafetyViolationError
from load_simulator.channels.cube_studio import CubeStudioChannel, build_auth_header
from load_simulator.channels.inference import InferenceChannel
from load_simulator.channels.kubernetes import K8sChannel
from load_simulator.channels.notebook import NotebookChannel
from load_simulator.channels.prometheus import PrometheusChannel


class _Wal:
    def __init__(self) -> None:
        self.records = []

    def record(self, **kwargs) -> None:
        self.records.append(kwargs)


class _DummyChannel(BaseChannel):
    def __init__(self, dry_run: bool = False, forbidden: bool = False, wal=None) -> None:
        super().__init__(dry_run=dry_run, wal=wal)
        self.forbidden = forbidden

    def _is_forbidden(self, action, params):  # noqa: ANN001, ANN201
        _ = action, params
        return self.forbidden

    async def _execute_impl(self, action, params):  # noqa: ANN001, ANN201
        return ChannelResult(success=True, data={"action": action, "params": params})


class BaseChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run(self) -> None:
        ch = _DummyChannel(dry_run=True)
        result = await ch.execute("x", {"a": 1})
        self.assertTrue(result.success)
        self.assertTrue(result.dry_run)

    async def test_forbidden(self) -> None:
        ch = _DummyChannel(forbidden=True)
        with self.assertRaises(SafetyViolationError):
            await ch.execute("danger", {})

    async def test_wal_record(self) -> None:
        wal = _Wal()
        ch = _DummyChannel(wal=wal)
        await ch.execute("change", {"x": 1}, recovery_action="rollback", recovery_params={"x": 0})
        self.assertEqual(len(wal.records), 1)
        self.assertEqual(wal.records[0]["recovery_action"], "rollback")


class CubeStudioChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_auth_header_username(self) -> None:
        self.assertEqual(build_auth_header(method="username", username="admin"), "admin")

    async def test_build_auth_header_jwt_shape(self) -> None:
        token = build_auth_header(method="jwt", username="alice", jwt_secret="secret")
        self.assertEqual(len(token.split(".")), 3)

    async def test_retry_and_path_methods(self) -> None:
        class _TestChannel(CubeStudioChannel):
            def __init__(self) -> None:
                super().__init__(base_url="http://x", retry_count=1)
                self.calls = 0
                self.last = None

            def _http_request_sync(self, method, path, json_body=None):  # noqa: ANN001, ANN201
                self.calls += 1
                self.last = (method, path, json_body)
                if self.calls == 1:
                    raise RuntimeError("boom")
                return {"ok": True}

        ch = _TestChannel()
        result = await ch.create_pipeline({"name": "p"})
        self.assertEqual(result["ok"], True)
        self.assertEqual(ch.last[1], "/pipeline_modelview/api/")
        self.assertEqual(ch.calls, 2)

    async def test_dry_run_uses_base_execute_path(self) -> None:
        class _DryRunChannel(CubeStudioChannel):
            def __init__(self) -> None:
                super().__init__(base_url="http://x", dry_run=True)
                self.calls = 0

            def _http_request_sync(self, method, path, json_body=None):  # noqa: ANN001, ANN201
                _ = method, path, json_body
                self.calls += 1
                return {"ok": True}

        ch = _DryRunChannel()
        result = await ch.create_pipeline({"name": "p"})
        self.assertEqual(ch.calls, 0)
        self.assertEqual(result.get("action"), "create_pipeline")

    async def test_forbidden_when_path_invalid(self) -> None:
        ch = CubeStudioChannel(base_url="http://x")
        with self.assertRaises(SafetyViolationError):
            await ch.execute("x", {"method": "GET", "path": "missing-leading-slash"})


class PrometheusChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_parse(self) -> None:
        class _TestProm(PrometheusChannel):
            def _http_get_json(self, url: str):  # noqa: ANN201
                if "query_range" in url:
                    return {"data": {"result": [{"values": [[1.0, "2"], [2.0, "3"]]}]}}
                return {"data": {"result": [{"value": [1.0, "5.5"]}]}}

        ch = _TestProm(base_url="http://prom")
        self.assertAlmostEqual(await ch.query_instant("up"), 5.5)
        values = await ch.query_range("up", dt.datetime.fromtimestamp(1), dt.datetime.fromtimestamp(2))
        self.assertEqual(values, [(1.0, 2.0), (2.0, 3.0)])


class InferenceChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completion(self) -> None:
        class _TestInference(InferenceChannel):
            def _post_json(self, body):  # noqa: ANN001, ANN201
                self.last = body
                return {"id": "1", "usage": {"completion_tokens": 10, "total_time_seconds": 2.0}}

        ch = _TestInference(endpoint="http://x/v1/chat/completions", model="m")
        result = await ch.chat_completion([{"role": "user", "content": "hi"}], max_tokens=20, stream=True)
        self.assertTrue(result.ok)
        self.assertEqual(ch.last["model"], "m")
        self.assertEqual(ch.last["max_tokens"], 20)
        self.assertAlmostEqual(result.ttft_seconds or 0, 0.2)


class NotebookChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_notebook_methods(self) -> None:
        class _TestNotebook(NotebookChannel):
            def _request_json(self, method, path, body):  # noqa: ANN001, ANN201
                self.last = (method, path, body)
                if method == "GET":
                    return [{"id": "k1"}]
                return {"ok": True}

        ch = _TestNotebook(base_url="http://nb")
        await ch.create_kernel()
        self.assertEqual(ch.last[1], "/api/kernels")
        kernels = await ch.list_kernels()
        self.assertEqual(kernels[0]["id"], "k1")


class K8sChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_count(self) -> None:
        class _Client:
            def list_pods(self, namespace, label_selector=None):  # noqa: ANN001, ANN201
                _ = namespace, label_selector
                return [
                    {"status": {"phase": "Pending"}},
                    {"status": {"phase": "Running"}},
                    {"status": {"phase": "Pending"}},
                ]

            def get_pod(self, namespace, pod_name):  # noqa: ANN001, ANN201
                _ = namespace, pod_name
                return {"status": {"phase": "Running"}}

        ch = K8sChannel(client=_Client())
        count = await ch.count_pending_pods("ns")
        self.assertEqual(count, 2)
        status = await ch.get_pod_status("ns", "pod-1")
        self.assertEqual(status, "Running")


    async def test_count_oomkilled_pods(self) -> None:
        class _OOMClient:
            def list_pods(self, namespace, label_selector=None):  # noqa: ANN001, ANN201
                _ = namespace, label_selector
                return [
                    # Pod with current state OOMKilled
                    {"status": {"phase": "Running", "containerStatuses": [
                        {"state": {"terminated": {"reason": "OOMKilled"}}, "lastState": {}}
                    ]}},
                    # Pod with lastState OOMKilled
                    {"status": {"phase": "Running", "containerStatuses": [
                        {"state": {"running": {}}, "lastState": {"terminated": {"reason": "OOMKilled"}}}
                    ]}},
                    # Healthy pod
                    {"status": {"phase": "Running", "containerStatuses": [
                        {"state": {"running": {}}, "lastState": {}}
                    ]}},
                    # Pod with no containerStatuses
                    {"status": {"phase": "Pending"}},
                ]

        ch = K8sChannel(client=_OOMClient())
        count = await ch.count_oomkilled_pods("ns")
        self.assertEqual(count, 2)

    async def test_count_oomkilled_pods_none(self) -> None:
        class _HealthyClient:
            def list_pods(self, namespace, label_selector=None):  # noqa: ANN001, ANN201
                _ = namespace, label_selector
                return [
                    {"status": {"phase": "Running", "containerStatuses": [
                        {"state": {"running": {}}, "lastState": {}}
                    ]}},
                ]

        ch = K8sChannel(client=_HealthyClient())
        count = await ch.count_oomkilled_pods("ns")
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
