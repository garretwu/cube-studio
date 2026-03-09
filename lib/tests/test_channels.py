from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import MagicMock

from lib.channels.base import BaseChannel, ChannelResult, SafetyViolationError
from lib.channels.cube_studio import CubeStudioChannel, build_auth_header
from lib.channels.kubernetes import K8sChannel
from lib.channels.prometheus import PrometheusChannel


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


class _TestClient:
    pass


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
        """Test that async httpx is used with retry logic."""
        ch = CubeStudioChannel(base_url="http://x", retry_count=1)
        # Mock the client request method
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"ok": true}'
        mock_response.json.return_value = {"ok": True}

        call_count = 0

        async def mock_request(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            return mock_response

        ch._client.request = mock_request

        result = await ch.create_pipeline({"name": "p"})
        self.assertEqual(result["ok"], True)
        self.assertEqual(call_count, 2)
        await ch.close()

    async def test_dry_run_uses_base_execute_path(self) -> None:
        """Test that dry_run bypasses actual HTTP requests."""
        ch = CubeStudioChannel(base_url="http://x", dry_run=True)

        # In dry_run mode, execute() returns ChannelResult with dry_run=True
        # But _execute_request extracts the data dict from the ChannelResult
        result = await ch.create_pipeline({"name": "p"})

        # The result is the data dict extracted from ChannelResult
        self.assertEqual(result.get("action"), "create_pipeline")
        await ch.close()

    async def test_forbidden_when_path_invalid(self) -> None:
        ch = CubeStudioChannel(base_url="http://x")
        with self.assertRaises(SafetyViolationError):
            await ch.execute("x", {"method": "GET", "path": "missing-leading-slash"})

    async def test_get_service_status(self) -> None:
        """Test get_service_status method."""
        ch = CubeStudioChannel(base_url="http://x")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 1, "name": "test-service"}

        async def mock_request(method, url, **kwargs):
            return mock_response

        ch._client.request = mock_request

        result = await ch.get_service_status("test-service")
        self.assertEqual(result["name"], "test-service")
        await ch.close()

    async def test_get_service_status_escapes_quotes(self) -> None:
        """Test that service_name with special chars doesn't break JSON filter."""
        import json
        import urllib.parse

        ch = CubeStudioChannel(base_url="http://x")

        captured_urls: list[str] = []
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}

        async def mock_request(method, url, **kwargs):
            captured_urls.append(str(url))
            return mock_response

        ch._client.request = mock_request

        # Name with quotes that would break naive string interpolation
        await ch.get_service_status('svc"name')
        self.assertEqual(len(captured_urls), 1)
        # Extract _filters param and verify it's valid JSON
        url_str = captured_urls[0]
        filters_part = url_str.split("_filters=")[1]
        decoded = urllib.parse.unquote(filters_part)
        parsed = json.loads(decoded)
        self.assertEqual(parsed[0]["value"], 'svc"name')
        await ch.close()

    async def test_update_inference_service(self) -> None:
        """Test update_inference_service method."""
        ch = CubeStudioChannel(base_url="http://x")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        async def mock_request(method, url, **kwargs):
            return mock_response

        ch._client.request = mock_request

        result = await ch.update_inference_service("test-service", {"min_replicas": 2})
        self.assertEqual(result["ok"], True)
        await ch.close()


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

    async def test_delete_pod(self) -> None:
        """Test delete_pod with WAL and dry_run."""
        class _TestDeleteClient:
            def delete_pods(self, namespace, label_selector):
                return {"deleted": True, "namespace": namespace, "label": label_selector}

        wal = _Wal()
        ch = K8sChannel(client=_TestDeleteClient(), wal=wal)

        # Test dry_run
        result = await ch.delete_pod("app=test", "default", dry_run=True)
        self.assertTrue(result.get("dry_run"))
        self.assertEqual(len(wal.records), 0)  # No WAL in dry_run

    async def test_delete_pod_wal(self) -> None:
        """Test delete_pod records to WAL."""
        class _TestDeleteClient:
            def delete_pods(self, namespace, label_selector):
                return {"deleted": True}

        wal = _Wal()
        ch = K8sChannel(client=_TestDeleteClient(), wal=wal)

        await ch.delete_pod("app=test", "default")
        self.assertEqual(len(wal.records), 1)
        self.assertEqual(wal.records[0]["recovery_action"], "restart_pod")

    async def test_scale_deployment(self) -> None:
        """Test scale_deployment with WAL."""
        class _ScaleClient:
            def __init__(self):
                self.get_deployment_called = False

            def get_deployment(self, namespace, name):
                self.get_deployment_called = True
                return {"spec": {"replicas": 3}}

            def scale_deployment(self, namespace, name, replicas):
                return {"scaled": True, "replicas": replicas}

        wal = _Wal()
        client = _ScaleClient()
        ch = K8sChannel(client=client, wal=wal)

        # Test dry_run — must NOT call get_deployment
        result = await ch.scale_deployment("deploy", "default", 5, dry_run=True)
        self.assertTrue(result.get("dry_run"))
        self.assertFalse(client.get_deployment_called)

        # Test actual scaling
        result = await ch.scale_deployment("deploy", "default", 5)
        self.assertTrue(client.get_deployment_called)
        self.assertEqual(result.get("replicas"), 5)
        self.assertEqual(len(wal.records), 1)
        self.assertEqual(wal.records[0]["recovery_params"]["replicas"], 3)

    async def test_forbidden_operations(self) -> None:
        """Test that forbidden operations are correctly identified."""
        ch = K8sChannel(client=_TestClient())

        # Namespace deletion is forbidden
        self.assertTrue(ch._is_forbidden("delete", {"verb": "delete", "resource": "Namespace"}))

        # CRD deletion is forbidden
        self.assertTrue(ch._is_forbidden("delete", {"verb": "delete", "resource": "CRD"}))

        # Pod deletion is allowed
        self.assertFalse(ch._is_forbidden("delete", {"verb": "delete", "resource": "Pod"}))

    async def test_delete_crd_raises(self) -> None:
        """Test that delete_crd raises SafetyViolationError."""
        ch = K8sChannel(client=_TestClient())
        with self.assertRaises(SafetyViolationError):
            await ch.delete_crd("my-crd")

    async def test_label_selectors(self) -> None:
        """Test that LABEL_SELECTORS are defined."""
        ch = K8sChannel(client=_TestClient())

        self.assertIn("backend", ch.LABEL_SELECTORS)
        self.assertIn("worker", ch.LABEL_SELECTORS)
        self.assertIn("inference", ch.LABEL_SELECTORS)
        self.assertEqual(ch.LABEL_SELECTORS["backend"], "app=kubeflow-dashboard")


if __name__ == "__main__":
    unittest.main()
