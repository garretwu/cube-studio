from __future__ import annotations

import asyncio
import json
import os
import unittest
from typing import Any

import pytest

from lib.channels.knowledge import DifyKnowledgeStoreAdapter, KnowledgeBaseChannel
from lib.tests._real_test_support import (
    get_http_retry_count,
    get_http_timeout_sec,
    require_env,
    require_real_tests,
)


class _FakeKnowledgeStore:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.runbook_calls: list[str] = []

    async def search(self, query: str, *, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        self.search_calls.append({"query": query, "category": category, "top_k": top_k})
        return [
            {"content": "GPU utilization is high", "source": "gpu.md", "score": 0.92},
            {"content": "Check rogue processes", "source": "runbook.md", "score": 0.88},
        ][:top_k]

    def search_runbooks(self, symptom: str) -> list[dict[str, Any]]:
        self.runbook_calls.append(symptom)
        return [
            {
                "title": "GPU contention runbook",
                "steps": ["find process", "terminate process", "verify p95"],
                "source": "runbook/gpu_contention.md",
            }
        ]


class _FakeKnowledgeStoreNoRunbook:
    async def search(self, query: str, *, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        _ = category
        return [{"content": query, "source": "doc.md", "score": 0.5}][:top_k]


class _FakeDifyResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = int(status_code)
        self._payload = payload
        if text:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)
        self.content = self.text.encode("utf-8") if self.text else b""

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            try:
                import httpx
            except Exception as exc:  # pragma: no cover - runtime dependency
                raise RuntimeError(f"httpx is required to raise status error: {exc}") from exc
            req = httpx.Request("GET", "http://dify.local/test")
            resp = httpx.Response(self.status_code, request=req, text=self.text)
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=req, response=resp)


class _FakeDifyClient:
    def __init__(self, responses: list[_FakeDifyResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> _FakeDifyResponse:
        self.calls.append(
            {
                "method": method.upper(),
                "url": url,
                "params": params or {},
                "json": json,
            }
        )
        if not self.responses:
            raise RuntimeError("no fake response queued")
        return self.responses.pop(0)

    async def aclose(self) -> None:
        return


class TestKnowledgeChannelUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_connect_disconnect_health(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStore())
        self.assertTrue(await channel.connect())
        health = await channel.health_check()
        self.assertTrue(health["connected"])
        self.assertTrue(health["store"])
        self.assertTrue(health["searchable"])
        self.assertTrue(await channel.disconnect())

    async def test_unit_search_validation(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStore())
        with self.assertRaises(ValueError):
            await channel.search("", top_k=1)
        with self.assertRaises(ValueError):
            await channel.search("gpu", top_k=0)

    async def test_unit_search_runbook_validation(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStore())
        with self.assertRaises(ValueError):
            await channel.search_runbook("")

    async def test_unit_unknown_action(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStore())
        result = await channel.execute("unknown_action", {})
        self.assertFalse(result.success)
        self.assertIn("unknown action", result.error)


class TestKnowledgeChannelIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_search_passthrough(self) -> None:
        store = _FakeKnowledgeStore()
        channel = KnowledgeBaseChannel(store=store)
        await channel.connect()

        chunks = await channel.search("gpu timeout", category="hardware", top_k=1)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(store.search_calls[0]["query"], "gpu timeout")
        self.assertEqual(store.search_calls[0]["category"], "hardware")
        self.assertEqual(store.search_calls[0]["top_k"], 1)

    async def test_integration_search_runbook_passthrough(self) -> None:
        store = _FakeKnowledgeStore()
        channel = KnowledgeBaseChannel(store=store)
        await channel.connect()

        runbooks = await channel.search_runbook("latency spike")
        self.assertEqual(len(runbooks), 1)
        self.assertEqual(store.runbook_calls[0], "latency spike")

    async def test_integration_missing_runbook_method(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStoreNoRunbook())
        await channel.connect()
        with self.assertRaises(RuntimeError):
            await channel.search_runbook("gpu overheat")


class TestKnowledgeChannelE2E(unittest.IsolatedAsyncioTestCase):
    async def test_e2e_mocked_knowledge_retrieval_flow(self) -> None:
        store = _FakeKnowledgeStore()
        channel = KnowledgeBaseChannel(store=store)

        await channel.connect()
        chunks = await channel.search("vllm p95 high", category="runbook", top_k=2)
        runbooks = await channel.search_runbook("p95 high")
        health = await channel.health_check()
        await channel.disconnect()

        self.assertEqual(len(chunks), 2)
        self.assertEqual(runbooks[0]["title"], "GPU contention runbook")
        self.assertTrue(health["connected"])


class TestDifyKnowledgeStoreAdapterUnit(unittest.IsolatedAsyncioTestCase):
    async def test_unit_search_category_exact_match_and_retrieve_payload(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(
                    200,
                    payload={
                        "data": [
                            {"id": "dataset-a", "name": "hardware"},
                            {"id": "dataset-b", "name": "runbook"},
                        ]
                    },
                ),
                _FakeDifyResponse(
                    200,
                    payload={
                        "records": [
                            {
                                "title": "GPU timeout doc",
                                "score": 0.91,
                                "segment": {
                                    "id": "seg-1",
                                    "content": "Check GPU process and memory pressure",
                                },
                            }
                        ]
                    },
                ),
            ]
        )
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            default_retrieval_model={
                "search_method": "semantic_search",
                "score_threshold_enabled": True,
                "score_threshold": 0.4,
                "unknown_key": "ignored",
            },
            _client=fake,
        )
        rows = await adapter.search("gpu timeout", category="hardware", top_k=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dataset_id"], "dataset-a")
        self.assertEqual(rows[0]["source"], "GPU timeout doc")
        self.assertAlmostEqual(rows[0]["score"], 0.91)
        self.assertEqual(rows[0]["record_id"], "seg-1")

        self.assertEqual(fake.calls[0]["method"], "GET")
        self.assertEqual(fake.calls[0]["url"], "/v1/datasets")
        self.assertEqual(fake.calls[0]["params"]["keyword"], "hardware")

        self.assertEqual(fake.calls[1]["method"], "POST")
        self.assertEqual(fake.calls[1]["url"], "/v1/datasets/dataset-a/retrieve")
        payload = fake.calls[1]["json"] or {}
        retrieval_model = payload.get("retrieval_model") or {}
        self.assertEqual(payload.get("query"), "gpu timeout")
        self.assertEqual(retrieval_model.get("top_k"), 3)
        self.assertEqual(retrieval_model.get("search_method"), "semantic_search")
        self.assertNotIn("unknown_key", retrieval_model)

    async def test_unit_search_category_contains_then_fallback(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(200, payload={"data": [{"id": "dataset-c", "name": "hardware-runbook"}]}),
                _FakeDifyResponse(200, payload={"records": [{"segment": {"content": "hit-1"}}]}),
                _FakeDifyResponse(200, payload={"data": []}),
                _FakeDifyResponse(200, payload={"records": [{"segment": {"content": "hit-2"}}]}),
            ]
        )
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            _client=fake,
        )

        contains_rows = await adapter.search("q1", category="hard", top_k=1)
        self.assertEqual(contains_rows[0]["dataset_id"], "dataset-c")
        self.assertEqual(fake.calls[1]["url"], "/v1/datasets/dataset-c/retrieve")

        fallback_rows = await adapter.search("q2", category="missing", top_k=1)
        self.assertEqual(fallback_rows[0]["dataset_id"], "default-ds")
        self.assertEqual(fake.calls[3]["url"], "/v1/datasets/default-ds/retrieve")

    async def test_unit_search_runbooks_uses_dedicated_dataset(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(
                    200,
                    payload={"records": [{"segment": {"id": "seg-9", "content": "runbook text"}}]},
                )
            ]
        )
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            _client=fake,
        )

        rows = await adapter.search_runbooks("latency spike")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dataset_id"], "runbook-ds")
        self.assertEqual(fake.calls[0]["url"], "/v1/datasets/runbook-ds/retrieve")

    async def test_unit_error_mapping(self) -> None:
        fake = _FakeDifyClient([_FakeDifyResponse(401, payload={"message": "unauthorized"})])
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            retries=1,
            _client=fake,
        )

        with self.assertRaisesRegex(RuntimeError, "dify auth failed"):
            await adapter.list_datasets()

        adapter._client = _FakeDifyClient([_FakeDifyResponse(404, payload={"message": "not found"})])
        with self.assertRaisesRegex(RuntimeError, "dify dataset not found: runbook-ds"):
            await adapter.search_runbooks("symptom")

        adapter._client = _FakeDifyClient([_FakeDifyResponse(500, payload={"message": "boom"})])
        with self.assertRaisesRegex(RuntimeError, "dify request failed: 500"):
            await adapter.list_datasets()

    async def test_unit_empty_records_returns_empty_list(self) -> None:
        fake = _FakeDifyClient([_FakeDifyResponse(200, payload={"records": []})])
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            _client=fake,
        )
        rows = await adapter.search("gpu timeout", top_k=3)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.real
class TestKnowledgeChannelReal:
    def test_real_knowledge_readonly_flow(self) -> None:
        require_real_tests()
        env = require_env(
            "SRE_KB_URL",
            "SRE_KB_TOKEN",
            "SRE_KB_DEFAULT_DATASET_ID",
            "SRE_KB_RUNBOOK_DATASET_ID",
            "SRE_TEST_KB_QUERY",
            "SRE_TEST_KB_SYMPTOM",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        adapter = DifyKnowledgeStoreAdapter(
            base_url=env["SRE_KB_URL"],
            api_key=env["SRE_KB_TOKEN"],
            default_dataset_id=env["SRE_KB_DEFAULT_DATASET_ID"],
            runbook_dataset_id=env["SRE_KB_RUNBOOK_DATASET_ID"],
            timeout=timeout_sec,
            retries=retry_count,
            api_prefix=os.getenv("SRE_KB_API_PREFIX", "/v1"),
        )
        channel = KnowledgeBaseChannel(store=adapter)

        try:
            assert _run(channel.connect()) is True
            health = _run(channel.health_check())
            assert health.get("connected") is True
            assert health.get("searchable") is True
            assert health.get("runbook_searchable") is True

            category = os.getenv("SRE_TEST_KB_CATEGORY", "").strip() or None
            chunks = _run(channel.search(env["SRE_TEST_KB_QUERY"], category=category, top_k=5))
            assert len(chunks) > 0, "knowledge search returned no results in real test"

            runbooks = _run(channel.search_runbook(env["SRE_TEST_KB_SYMPTOM"]))
            assert len(runbooks) > 0, "knowledge runbook search returned no results in real test"
        finally:
            _run(channel.disconnect())
            _run(adapter.aclose())
