from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from load_simulator.channels.inference import InferenceChannel
from load_simulator.channels.notebook import NotebookChannel


class InferenceChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_completion(self) -> None:
        """Test InferenceChannel with async httpx."""
        ch = InferenceChannel(endpoint="http://x/v1/chat/completions", model="m")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "1",
            "usage": {"completion_tokens": 10, "total_time_seconds": 2.0}
        }

        async def mock_post(url, **kwargs):
            return mock_response

        ch._client.post = mock_post

        result = await ch.chat_completion([{"role": "user", "content": "hi"}], max_tokens=20, stream=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 200)
        self.assertAlmostEqual(result.ttft_seconds or 0, 0.2)
        await ch.close()

    async def test_chat_completion_with_metrics(self) -> None:
        """Test that metrics are recorded when collector is provided."""
        from load_simulator.metrics.collector import MetricCollector

        collector = MetricCollector()
        ch = InferenceChannel(
            endpoint="http://x/v1/chat/completions",
            model="m",
            metrics_collector=collector,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "1", "usage": {}}

        async def mock_post(url, **kwargs):
            return mock_response

        ch._client.post = mock_post

        result = await ch.chat_completion([{"role": "user", "content": "hi"}])
        self.assertTrue(result.ok)

        # Check metrics were recorded
        samples = collector.get_samples("inference_requests_total")
        self.assertEqual(len(samples), 1)
        await ch.close()

    async def test_chat_completion_error_metrics(self) -> None:
        """Test that error metrics are recorded on failure."""
        from load_simulator.metrics.collector import MetricCollector

        collector = MetricCollector()
        ch = InferenceChannel(
            endpoint="http://x/v1/chat/completions",
            model="m",
            metrics_collector=collector,
        )

        async def mock_post(url, **kwargs):
            raise RuntimeError("connection error")

        ch._client.post = mock_post

        result = await ch.chat_completion([{"role": "user", "content": "hi"}])
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

        # Check error metrics were recorded
        samples = collector.get_samples("inference_errors_total")
        self.assertEqual(len(samples), 1)
        await ch.close()

    async def test_connection_pool_config(self) -> None:
        """Test that connection pool is configured correctly."""
        ch = InferenceChannel(
            endpoint="http://x/v1/chat/completions",
            model="m",
            max_connections=100,
            max_keepalive_connections=50,
        )
        pool = ch._client._transport._pool
        self.assertEqual(pool._max_connections, 100)
        self.assertEqual(pool._max_keepalive_connections, 50)
        await ch.close()

    async def test_non_200_response_returns_ok_false(self) -> None:
        """Test that non-200 HTTP responses set ok=False."""
        ch = InferenceChannel(endpoint="http://x/v1/chat/completions", model="m")

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.json.return_value = {"error": "rate limited"}

        async def mock_post(url, **kwargs):
            return mock_response

        ch._client.post = mock_post

        result = await ch.chat_completion([{"role": "user", "content": "hi"}])
        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 429)
        self.assertIsNotNone(result.error)
        await ch.close()

    async def test_non_200_records_error_metrics(self) -> None:
        """Test that non-200 responses record error metrics when collector is provided."""
        from load_simulator.metrics.collector import MetricCollector

        collector = MetricCollector()
        ch = InferenceChannel(
            endpoint="http://x/v1/chat/completions",
            model="m",
            metrics_collector=collector,
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "internal"}

        async def mock_post(url, **kwargs):
            return mock_response

        ch._client.post = mock_post

        result = await ch.chat_completion([{"role": "user", "content": "hi"}])
        self.assertFalse(result.ok)

        # Check that error metric was recorded (not success)
        success_samples = collector.get_samples("inference_requests_total")
        error_samples = collector.get_samples("inference_errors_total")
        self.assertEqual(len(success_samples), 0)
        self.assertEqual(len(error_samples), 1)
        await ch.close()


def _mock_httpx_response(status_code: int = 200, json_data: dict | list | None = None) -> MagicMock:
    """Create a mock httpx.Response with raise_for_status support."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _fake_ws_connect(reply_status: str = "ok", raise_error: Exception | None = None):
    """Return an async context manager mock that simulates websockets.connect."""
    mock_ws = AsyncMock()

    if raise_error is not None:
        mock_ws.send = AsyncMock(side_effect=raise_error)
    else:
        # recv returns an execute_reply whose parent_header.msg_id matches the request
        async def _recv():
            # Read the msg_id from the sent message
            sent = mock_ws.send.call_args[0][0]
            msg_id = json.loads(sent)["header"]["msg_id"]
            return json.dumps({
                "header": {"msg_type": "execute_reply"},
                "parent_header": {"msg_id": msg_id},
                "msg_type": "execute_reply",
                "content": {"status": reply_status},
            })
        mock_ws.recv = _recv

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_ws)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class NotebookChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_base_url_trailing_slash(self) -> None:
        """Ensure base_url always ends with / for correct relative path resolution."""
        ch = NotebookChannel(base_url="http://host/notebook/jupyter/name")
        self.assertEqual(ch.base_url, "http://host/notebook/jupyter/name/")
        await ch.close()

        ch2 = NotebookChannel(base_url="http://host/notebook/jupyter/name/")
        self.assertEqual(ch2.base_url, "http://host/notebook/jupyter/name/")
        await ch2.close()

    async def test_create_kernel(self) -> None:
        """Test that create_kernel issues POST api/kernels (relative path)."""
        ch = NotebookChannel(base_url="http://nb")
        expected = {"id": "k1", "name": "python3"}
        ch._client.post = AsyncMock(return_value=_mock_httpx_response(201, expected))

        result = await ch.create_kernel("python3")
        self.assertEqual(result, expected)

        ch._client.post.assert_awaited_once()
        call_args = ch._client.post.call_args
        self.assertEqual(call_args[0][0], "api/kernels")
        await ch.close()

    async def test_create_kernel_preserves_url_prefix(self) -> None:
        """Test that API paths are appended to (not replacing) the base_url path."""
        ch = NotebookChannel(base_url="http://host:8080/notebook/jupyter/my-nb")
        self.assertIn("/notebook/jupyter/my-nb/", str(ch._client.base_url))
        await ch.close()

    async def test_list_kernels(self) -> None:
        """Test that list_kernels returns a list."""
        ch = NotebookChannel(base_url="http://nb")
        data = [{"id": "k1"}, {"id": "k2"}]
        ch._client.get = AsyncMock(return_value=_mock_httpx_response(200, data))

        kernels = await ch.list_kernels()
        self.assertEqual(len(kernels), 2)
        self.assertEqual(kernels[0]["id"], "k1")
        await ch.close()

    async def test_delete_kernel(self) -> None:
        """Test kernel deletion uses relative path."""
        ch = NotebookChannel(base_url="http://nb")
        ch._client.delete = AsyncMock(return_value=_mock_httpx_response(204, {}))

        await ch.delete_kernel("kernel-123")
        ch._client.delete.assert_awaited_once()
        call_args = ch._client.delete.call_args
        self.assertEqual(call_args[0][0], "api/kernels/kernel-123")
        await ch.close()

    @patch("load_simulator.channels.notebook.websockets.connect")
    async def test_execute_code_via_websocket(self, mock_connect: MagicMock) -> None:
        """execute_code sends execute_request over WS and returns execute_reply."""
        mock_connect.return_value = _fake_ws_connect(reply_status="ok")
        ch = NotebookChannel(base_url="http://nb", username="admin")

        result = await ch.execute_code("k1", "print(1)")
        self.assertEqual(result["status"], "ok")
        self.assertIn("msg_id", result)

        # Verify WS URL construction
        ws_url = mock_connect.call_args[0][0]
        self.assertTrue(ws_url.startswith("ws://"))
        self.assertIn("/api/kernels/k1/channels", ws_url)
        await ch.close()

    @patch("load_simulator.channels.notebook.websockets.connect")
    async def test_execute_code_ws_url_with_token(self, mock_connect: MagicMock) -> None:
        """When token is set, WS URL includes ?token= query param."""
        mock_connect.return_value = _fake_ws_connect(reply_status="ok")
        ch = NotebookChannel(base_url="http://nb", token="my-tok")

        await ch.execute_code("k1", "x=1")

        ws_url = mock_connect.call_args[0][0]
        self.assertIn("?token=my-tok", ws_url)
        await ch.close()

    @patch("load_simulator.channels.notebook.websockets.connect")
    async def test_execute_code_preserves_path_prefix(self, mock_connect: MagicMock) -> None:
        """WS URL preserves the base_url path prefix."""
        mock_connect.return_value = _fake_ws_connect(reply_status="ok")
        ch = NotebookChannel(base_url="http://host/notebook/jupyter/my-nb")

        await ch.execute_code("k1", "1+1")

        ws_url = mock_connect.call_args[0][0]
        self.assertIn("/notebook/jupyter/my-nb/api/kernels/k1/channels", ws_url)
        await ch.close()

    @patch("load_simulator.channels.notebook.websockets.connect")
    async def test_execute_code_raises_on_ws_error(self, mock_connect: MagicMock) -> None:
        """WebSocket connection failure propagates to caller."""
        mock_connect.side_effect = OSError("connection refused")
        ch = NotebookChannel(base_url="http://nb")

        with self.assertRaises(OSError):
            await ch.execute_code("k1", "print(1)")
        await ch.close()

    async def test_ws_url_construction(self) -> None:
        """Test _ws_url builds correct WebSocket URLs."""
        ch = NotebookChannel(base_url="http://host:8080/notebook/jupyter/nb1")
        url = ch._ws_url("kern-abc")
        self.assertEqual(url, "ws://host:8080/notebook/jupyter/nb1/api/kernels/kern-abc/channels")

        ch2 = NotebookChannel(base_url="https://host/prefix", token="tok")
        url2 = ch2._ws_url("k1")
        self.assertEqual(url2, "wss://host/prefix/api/kernels/k1/channels?token=tok")
        await ch.close()
        await ch2.close()

    async def test_create_kernel_with_username(self) -> None:
        """Test that username is used in headers when provided."""
        ch = NotebookChannel(base_url="http://nb", username="admin")
        ch._client.post = AsyncMock(return_value=_mock_httpx_response(201, {"id": "k1"}))

        await ch.create_kernel("python3")
        call_kwargs = ch._client.post.call_args[1]
        headers = call_kwargs["headers"]
        self.assertEqual(headers["Authorization"], "admin")
        self.assertEqual(headers["Cookie"], "myapp_username=admin")
        await ch.close()

    async def test_create_kernel_with_token(self) -> None:
        """Test that token is used in headers when provided."""
        ch = NotebookChannel(base_url="http://nb", token="jupyter-token-123")
        ch._client.post = AsyncMock(return_value=_mock_httpx_response(201, {"id": "k1"}))

        await ch.create_kernel("python3")
        call_kwargs = ch._client.post.call_args[1]
        headers = call_kwargs["headers"]
        self.assertEqual(headers["Authorization"], "token jupyter-token-123")
        await ch.close()

    async def test_headers_priority(self) -> None:
        """Test that token takes priority over username."""
        ch = NotebookChannel(base_url="http://nb", token="my-token", username="admin")
        headers = ch._headers()

        self.assertEqual(headers["Authorization"], "token my-token")
        self.assertNotIn("Cookie", headers)
        await ch.close()

    async def test_close(self) -> None:
        """Test that close() shuts down the httpx client."""
        ch = NotebookChannel(base_url="http://nb")
        ch._client.aclose = AsyncMock()
        await ch.close()
        ch._client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
