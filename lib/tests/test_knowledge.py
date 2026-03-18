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
        self.dataset_calls: list[dict[str, Any]] = []
        self.file_calls: list[dict[str, Any]] = []
        self.chunk_calls: list[dict[str, Any]] = []

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

    async def list_datasets(
        self,
        *,
        keyword: str | None = None,
        page: int = 1,
        limit: int = 20,
        tag_ids: list[str] | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        self.dataset_calls.append(
            {
                "kind": "list",
                "keyword": keyword,
                "page": page,
                "limit": limit,
                "tag_ids": tag_ids,
                "include_all": include_all,
            }
        )
        return [{"id": "dataset-a", "name": "hardware"}]

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        self.dataset_calls.append({"kind": "get", "dataset_id": dataset_id})
        return {"id": dataset_id, "name": "hardware"}

    async def list_documents(
        self,
        dataset_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        self.file_calls.append(
            {"kind": "list", "dataset_id": dataset_id, "page": page, "limit": limit, "keyword": keyword}
        )
        return [{"id": "doc-1", "name": "gpu.md"}]

    async def get_document(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        self.file_calls.append({"kind": "get", "dataset_id": dataset_id, "document_id": document_id})
        return {"id": document_id, "dataset_id": dataset_id, "name": "gpu.md"}

    async def list_document_segments(
        self,
        dataset_id: str,
        document_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        self.chunk_calls.append(
            {
                "dataset_id": dataset_id,
                "document_id": document_id,
                "page": page,
                "limit": limit,
                "keyword": keyword,
            }
        )
        return [{"id": "seg-1", "content": "segment"}]


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

    async def test_unit_new_api_validation(self) -> None:
        channel = KnowledgeBaseChannel(store=_FakeKnowledgeStore())
        with self.assertRaises(ValueError):
            await channel.get_dataset("")
        with self.assertRaises(ValueError):
            await channel.list_datasets(page=0)
        with self.assertRaises(ValueError):
            await channel.list_files("", page=1, limit=1)
        with self.assertRaises(ValueError):
            await channel.get_file("dataset-a", "")
        with self.assertRaises(ValueError):
            await channel.retrieve_chunks("dataset-a", "doc-1", page=0)


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

    async def test_integration_new_channel_wrappers_passthrough(self) -> None:
        store = _FakeKnowledgeStore()
        channel = KnowledgeBaseChannel(store=store)
        await channel.connect()

        datasets = await channel.list_datasets(keyword="hard", page=1, limit=5)
        dataset = await channel.get_dataset("dataset-a")
        files = await channel.list_files("dataset-a", page=1, limit=5, keyword="gpu")
        file_row = await channel.get_file("dataset-a", "doc-1")
        chunks = await channel.retrieve_chunks("dataset-a", "doc-1", page=1, limit=5, keyword="seg")

        self.assertEqual(len(datasets), 1)
        self.assertEqual(dataset["id"], "dataset-a")
        self.assertEqual(len(files), 1)
        self.assertEqual(file_row["id"], "doc-1")
        self.assertEqual(len(chunks), 1)

        self.assertEqual(store.dataset_calls[0]["kind"], "list")
        self.assertEqual(store.dataset_calls[1]["kind"], "get")
        self.assertEqual(store.file_calls[0]["kind"], "list")
        self.assertEqual(store.file_calls[1]["kind"], "get")


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
                _FakeDifyResponse(200, payload={"id": "dataset-c", "retrieval_model_dict": {}}),
                _FakeDifyResponse(200, payload={"records": [{"segment": {"content": "hit-1"}}]}),
                _FakeDifyResponse(200, payload={"data": []}),
                _FakeDifyResponse(200, payload={"id": "default-ds", "retrieval_model_dict": {}}),
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
        self.assertEqual(fake.calls[2]["url"], "/v1/datasets/dataset-c/retrieve")

        fallback_rows = await adapter.search("q2", category="missing", top_k=1)
        self.assertEqual(fallback_rows[0]["dataset_id"], "default-ds")
        self.assertEqual(fake.calls[5]["url"], "/v1/datasets/default-ds/retrieve")

    async def test_unit_search_runbooks_uses_dedicated_dataset(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(
                    200,
                    payload={"id": "runbook-ds", "retrieval_model_dict": {"score_threshold_enabled": False}},
                ),
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
        self.assertEqual(fake.calls[1]["url"], "/v1/datasets/runbook-ds/retrieve")

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
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(200, payload={"id": "default-ds", "retrieval_model_dict": {}}),
                _FakeDifyResponse(200, payload={"records": []}),
            ]
        )
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            _client=fake,
        )
        rows = await adapter.search("gpu timeout", top_k=3)
        self.assertEqual(rows, [])

    async def test_unit_dataset_document_segment_apis(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(200, payload={"id": "dataset-a", "name": "hardware"}),
                _FakeDifyResponse(200, payload={"data": [{"id": "doc-1", "name": "gpu.md"}]}),
                _FakeDifyResponse(200, payload={"id": "doc-1", "name": "gpu.md"}),
                _FakeDifyResponse(200, payload={"data": [{"id": "seg-1", "content": "segment-1"}]}),
            ]
        )
        adapter = DifyKnowledgeStoreAdapter(
            base_url="http://10.11.4.3:31984",
            api_key="token-1",
            default_dataset_id="default-ds",
            runbook_dataset_id="runbook-ds",
            _client=fake,
        )

        dataset = await adapter.get_dataset("dataset-a")
        documents = await adapter.list_documents("dataset-a", page=1, limit=5, keyword="gpu")
        document = await adapter.get_document("dataset-a", "doc-1")
        segments = await adapter.list_document_segments("dataset-a", "doc-1", page=1, limit=5, keyword="segment")

        self.assertEqual(dataset["id"], "dataset-a")
        self.assertEqual(len(documents), 1)
        self.assertEqual(document["id"], "doc-1")
        self.assertEqual(len(segments), 1)

        self.assertEqual(fake.calls[1]["url"], "/v1/datasets/dataset-a/documents")
        self.assertEqual(fake.calls[2]["url"], "/v1/datasets/dataset-a/documents/doc-1")
        self.assertEqual(fake.calls[3]["url"], "/v1/datasets/dataset-a/documents/doc-1/segments")
        self.assertEqual(fake.calls[1]["params"]["keyword"], "gpu")
        self.assertEqual(fake.calls[3]["params"]["keyword"], "segment")

    async def test_unit_retrieve_auto_fill_and_cache(self) -> None:
        fake = _FakeDifyClient(
            [
                _FakeDifyResponse(
                    200,
                    payload={
                        "id": "default-ds",
                        "retrieval_model_dict": {
                            "search_method": "hybrid_search",
                            "score_threshold_enabled": False,
                            "score_threshold": 0.5,
                            "unknown_key": "ignored",
                        },
                    },
                ),
                _FakeDifyResponse(200, payload={"records": [{"segment": {"content": "hit-1"}}]}),
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

        rows1 = await adapter.search("q1", top_k=3)
        rows2 = await adapter.search("q2", top_k=2)
        self.assertEqual(len(rows1), 1)
        self.assertEqual(len(rows2), 1)

        dataset_calls = [c for c in fake.calls if c["url"] == "/v1/datasets/default-ds"]
        self.assertEqual(len(dataset_calls), 1)

        retrieve_calls = [c for c in fake.calls if c["url"] == "/v1/datasets/default-ds/retrieve"]
        self.assertEqual(len(retrieve_calls), 2)
        first_model = retrieve_calls[0]["json"]["retrieval_model"]
        second_model = retrieve_calls[1]["json"]["retrieval_model"]
        self.assertEqual(first_model["search_method"], "hybrid_search")
        self.assertEqual(first_model["score_threshold_enabled"], False)
        self.assertEqual(first_model["top_k"], 3)
        self.assertEqual(second_model["top_k"], 2)
        self.assertNotIn("unknown_key", first_model)


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


@pytest.mark.real
class TestKnowledgeChannelRealDataApis:
    def test_real_knowledge_dataset_file_chunk_flow(self) -> None:
        require_real_tests()
        env = require_env(
            "SRE_KB_URL",
            "SRE_KB_TOKEN",
            "SRE_KB_DEFAULT_DATASET_ID",
            "SRE_TEST_KB_QUERY",
        )
        timeout_sec = get_http_timeout_sec()
        retry_count = get_http_retry_count()

        runbook_dataset_id = os.getenv("SRE_KB_RUNBOOK_DATASET_ID", env["SRE_KB_DEFAULT_DATASET_ID"])
        adapter = DifyKnowledgeStoreAdapter(
            base_url=env["SRE_KB_URL"],
            api_key=env["SRE_KB_TOKEN"],
            default_dataset_id=env["SRE_KB_DEFAULT_DATASET_ID"],
            runbook_dataset_id=runbook_dataset_id,
            timeout=timeout_sec,
            retries=retry_count,
            api_prefix=os.getenv("SRE_KB_API_PREFIX", "/v1"),
        )
        channel = KnowledgeBaseChannel(store=adapter)

        try:
            assert _run(channel.connect()) is True

            # Verify search works with auto-filled retrieval model when no
            # default retrieval model is explicitly configured.
            chunks = _run(channel.search(env["SRE_TEST_KB_QUERY"], top_k=5))
            assert isinstance(chunks, list)

            files = _run(channel.list_files(env["SRE_KB_DEFAULT_DATASET_ID"], page=1, limit=5))
            assert len(files) > 0, "list_files returned no documents in real test"
            file_id = str(files[0].get("id") or "")
            assert file_id, "first file does not include id"

            segments = _run(channel.retrieve_chunks(env["SRE_KB_DEFAULT_DATASET_ID"], file_id, page=1, limit=5))
            assert isinstance(segments, list)
        finally:
            _run(channel.disconnect())
            _run(adapter.aclose())
