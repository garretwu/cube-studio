from __future__ import annotations

import unittest
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
