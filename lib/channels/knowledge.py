"""Knowledge retrieval channel for SRE runbooks and troubleshooting docs."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any

from .base import BaseChannel, ChannelResult

try:
    import httpx
except Exception:  # pragma: no cover - optional runtime dependency
    httpx = None  # type: ignore[assignment]


class DifyKnowledgeStoreAdapter:
    """Dify Dataset API adapter compatible with KnowledgeBaseChannel."""

    _ALLOWED_RETRIEVAL_MODEL_KEYS = {
        "search_method",
        "reranking_enable",
        "reranking_mode",
        "reranking_model",
        "weights",
        "top_k",
        "score_threshold_enabled",
        "score_threshold",
    }

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_dataset_id: str,
        runbook_dataset_id: str,
        timeout: float = 15.0,
        retries: int = 2,
        api_prefix: str = "/v1",
        default_retrieval_model: dict[str, Any] | None = None,
        _client: Any | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be blank")
        if not api_key.strip():
            raise ValueError("api_key must not be blank")
        if not default_dataset_id.strip():
            raise ValueError("default_dataset_id must not be blank")
        if not runbook_dataset_id.strip():
            raise ValueError("runbook_dataset_id must not be blank")

        self.base_url = base_url.rstrip("/")
        self.api_prefix = "/" + api_prefix.strip("/") if api_prefix.strip() else "/v1"
        self.default_dataset_id = default_dataset_id.strip()
        self.runbook_dataset_id = runbook_dataset_id.strip()
        self.retries = max(1, int(retries))
        self.default_retrieval_model = self._sanitize_retrieval_model(default_retrieval_model or {})
        self._dataset_retrieval_model_cache: dict[str, dict[str, Any]] = {}

        if _client is not None:
            self._client = _client
        else:
            if httpx is None:
                raise RuntimeError("httpx is required for DifyKnowledgeStoreAdapter")
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {api_key.strip()}",
                    "Content-Type": "application/json",
                },
            )

    async def search(self, query: str, *, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        text = query.strip()
        if not text:
            raise ValueError("query must not be blank")
        if int(top_k) <= 0:
            raise ValueError("top_k must be > 0")

        dataset_id = self.default_dataset_id
        dataset_name: str | None = None

        if category and category.strip():
            datasets = await self.list_datasets(keyword=category.strip(), page=1, limit=20)
            selected = self._select_dataset_by_category(datasets, category.strip())
            if selected is not None:
                dataset_id = str(selected["id"])
                name = selected.get("name")
                dataset_name = str(name) if isinstance(name, str) else None

        records = await self._retrieve(dataset_id=dataset_id, query=text, top_k=int(top_k))
        return [self._to_result_item(record, dataset_id=dataset_id, dataset_name=dataset_name) for record in records]

    async def search_runbooks(self, symptom: str) -> list[dict[str, Any]]:
        text = symptom.strip()
        if not text:
            raise ValueError("symptom must not be blank")
        records = await self._retrieve(
            dataset_id=self.runbook_dataset_id,
            query=text,
            top_k=5,
        )
        return [
            self._to_result_item(
                record,
                dataset_id=self.runbook_dataset_id,
                dataset_name=None,
            )
            for record in records
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
        params: dict[str, Any] = {
            "page": int(page),
            "limit": int(limit),
            "include_all": "true" if include_all else "false",
        }
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()
        if tag_ids:
            clean_tags = [str(tag).strip() for tag in tag_ids if str(tag).strip()]
            if clean_tags:
                params["tag_ids"] = ",".join(clean_tags)

        payload = await self._request_json(
            "GET",
            f"{self.api_prefix}/datasets",
            params=params,
            dataset_id=None,
        )
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        dataset = dataset_id.strip()
        if not dataset:
            raise ValueError("dataset_id must not be blank")
        payload = await self._request_json(
            "GET",
            f"{self.api_prefix}/datasets/{dataset}",
            dataset_id=dataset,
        )
        if isinstance(payload, dict):
            return payload
        return {}

    async def list_documents(
        self,
        dataset_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        dataset = dataset_id.strip()
        if not dataset:
            raise ValueError("dataset_id must not be blank")
        if int(page) <= 0:
            raise ValueError("page must be > 0")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        params: dict[str, Any] = {"page": int(page), "limit": int(limit)}
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()
        payload = await self._request_json(
            "GET",
            f"{self.api_prefix}/datasets/{dataset}/documents",
            params=params,
            dataset_id=dataset,
        )
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def get_document(self, dataset_id: str, document_id: str) -> dict[str, Any]:
        dataset = dataset_id.strip()
        document = document_id.strip()
        if not dataset:
            raise ValueError("dataset_id must not be blank")
        if not document:
            raise ValueError("document_id must not be blank")

        payload = await self._request_json(
            "GET",
            f"{self.api_prefix}/datasets/{dataset}/documents/{document}",
            dataset_id=dataset,
        )
        if isinstance(payload, dict):
            return payload
        return {}

    async def list_document_segments(
        self,
        dataset_id: str,
        document_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        dataset = dataset_id.strip()
        document = document_id.strip()
        if not dataset:
            raise ValueError("dataset_id must not be blank")
        if not document:
            raise ValueError("document_id must not be blank")
        if int(page) <= 0:
            raise ValueError("page must be > 0")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        params: dict[str, Any] = {"page": int(page), "limit": int(limit)}
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()
        payload = await self._request_json(
            "GET",
            f"{self.api_prefix}/datasets/{dataset}/documents/{document}/segments",
            params=params,
            dataset_id=dataset,
        )
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _retrieve(self, *, dataset_id: str, query: str, top_k: int) -> list[dict[str, Any]]:
        retrieval_model = await self._build_retrieval_model(dataset_id=dataset_id, top_k=int(top_k))
        payload = await self._request_json(
            "POST",
            f"{self.api_prefix}/datasets/{dataset_id}/retrieve",
            json_body={"query": query, "retrieval_model": retrieval_model},
            dataset_id=dataset_id,
        )
        if not isinstance(payload, dict):
            return []
        records = payload.get("records")
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    async def _build_retrieval_model(self, *, dataset_id: str, top_k: int) -> dict[str, Any]:
        model = dict(self.default_retrieval_model)
        if not model:
            model = await self._get_dataset_retrieval_model(dataset_id)
        model["top_k"] = int(top_k)
        return model

    async def _get_dataset_retrieval_model(self, dataset_id: str) -> dict[str, Any]:
        dataset = dataset_id.strip()
        if not dataset:
            return {}
        cached = self._dataset_retrieval_model_cache.get(dataset)
        if cached is not None:
            return dict(cached)

        payload = await self.get_dataset(dataset)
        retrieval_model = payload.get("retrieval_model_dict") if isinstance(payload, dict) else {}
        clean = self._sanitize_retrieval_model(retrieval_model if isinstance(retrieval_model, dict) else {})
        self._dataset_retrieval_model_cache[dataset] = dict(clean)
        return clean

    def _sanitize_retrieval_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in payload.items():
            if key in self._ALLOWED_RETRIEVAL_MODEL_KEYS:
                clean[key] = value
        return clean

    def _select_dataset_by_category(
        self,
        datasets: list[dict[str, Any]],
        category: str,
    ) -> dict[str, Any] | None:
        needle = category.strip().lower()
        if not needle:
            return None

        # Priority 1: exact name match
        for item in datasets:
            name = item.get("name")
            dataset_id = item.get("id")
            if isinstance(name, str) and isinstance(dataset_id, str) and name.strip().lower() == needle:
                return item

        # Priority 2: contains name match
        for item in datasets:
            name = item.get("name")
            dataset_id = item.get("id")
            if isinstance(name, str) and isinstance(dataset_id, str) and needle in name.strip().lower():
                return item

        return None

    @staticmethod
    def _to_result_item(
        record: dict[str, Any],
        *,
        dataset_id: str,
        dataset_name: str | None,
    ) -> dict[str, Any]:
        segment = record.get("segment")
        if not isinstance(segment, dict):
            segment = {}

        content = segment.get("content")
        content_text = str(content) if content is not None else ""

        document = segment.get("document")
        doc_name: str | None = None
        if isinstance(document, dict):
            name = document.get("name")
            if isinstance(name, str):
                doc_name = name

        source = record.get("title") or doc_name or dataset_name or ""
        score = DifyKnowledgeStoreAdapter._to_float(record.get("score"))
        record_id = segment.get("id") or record.get("segment_id") or record.get("id")

        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        return {
            "content": content_text,
            "source": str(source),
            "score": score,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "record_id": str(record_id) if record_id is not None else None,
            "metadata": metadata,
            "raw": record,
        }

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        dataset_id: str | None = None,
    ) -> Any:
        delay = 0.2
        for attempt in range(1, self.retries + 1):
            try:
                response = await self._client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=json_body,
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
            except Exception as exc:
                mapped = self._map_request_error(exc, dataset_id=dataset_id)
                if mapped is not None:
                    raise mapped
                if attempt >= self.retries:
                    raise RuntimeError(f"dify request failed: {exc}") from exc
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 2.0)
        raise RuntimeError("unreachable retry loop")

    @staticmethod
    def _map_request_error(exc: Exception, *, dataset_id: str | None) -> RuntimeError | None:
        if httpx is None:
            return None
        if not isinstance(exc, httpx.HTTPStatusError):
            return None

        status = int(exc.response.status_code)
        body = (exc.response.text or "").strip()
        if status in (401, 403):
            return RuntimeError("dify auth failed")
        if status == 404 and dataset_id:
            return RuntimeError(f"dify dataset not found: {dataset_id}")
        return RuntimeError(f"dify request failed: {status} {body}")


class KnowledgeBaseChannel(BaseChannel):
    """Channel wrapper over an injected knowledge store object."""

    def __init__(
        self,
        *,
        store: Any | None,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.store = store
        self._connected = False

    async def connect(self) -> bool:
        result = await self.execute("connect", {})
        return result.success

    async def disconnect(self) -> bool:
        result = await self.execute("disconnect", {})
        return result.success

    async def health_check(self) -> dict[str, bool]:
        result = await self.execute("health_check", {})
        if not result.success or not isinstance(result.data, dict):
            return {"connected": False}
        return {str(k): bool(v) for k, v in result.data.items()}

    async def search(self, query: str, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if top_k <= 0:
            raise ValueError("top_k must be > 0")

        result = await self.execute(
            "search",
            {"query": query, "category": category, "top_k": int(top_k)},
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        return [self._to_mapping(item) for item in data]

    async def search_runbook(self, symptom: str) -> list[dict[str, Any]]:
        if not symptom.strip():
            raise ValueError("symptom must not be blank")

        result = await self.execute("search_runbook", {"symptom": symptom})
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        return [self._to_mapping(item) for item in data]

    async def list_datasets(
        self,
        *,
        keyword: str | None = None,
        page: int = 1,
        limit: int = 20,
        tag_ids: list[str] | None = None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        if int(page) <= 0:
            raise ValueError("page must be > 0")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")
        result = await self.execute(
            "list_datasets",
            {
                "keyword": keyword,
                "page": int(page),
                "limit": int(limit),
                "tag_ids": tag_ids,
                "include_all": bool(include_all),
            },
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        return [self._to_mapping(item) for item in data]

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be blank")
        result = await self.execute("get_dataset", {"dataset_id": dataset_id.strip()})
        data = self._unwrap(result, default={})
        return self._to_mapping(data)

    async def list_files(
        self,
        dataset_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be blank")
        if int(page) <= 0:
            raise ValueError("page must be > 0")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        result = await self.execute(
            "list_files",
            {
                "dataset_id": dataset_id.strip(),
                "page": int(page),
                "limit": int(limit),
                "keyword": keyword,
            },
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        return [self._to_mapping(item) for item in data]

    async def get_file(self, dataset_id: str, file_id: str) -> dict[str, Any]:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be blank")
        if not file_id.strip():
            raise ValueError("file_id must not be blank")

        result = await self.execute(
            "get_file",
            {"dataset_id": dataset_id.strip(), "file_id": file_id.strip()},
        )
        data = self._unwrap(result, default={})
        return self._to_mapping(data)

    async def retrieve_chunks(
        self,
        dataset_id: str,
        file_id: str,
        *,
        page: int = 1,
        limit: int = 20,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        if not dataset_id.strip():
            raise ValueError("dataset_id must not be blank")
        if not file_id.strip():
            raise ValueError("file_id must not be blank")
        if int(page) <= 0:
            raise ValueError("page must be > 0")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        result = await self.execute(
            "retrieve_chunks",
            {
                "dataset_id": dataset_id.strip(),
                "file_id": file_id.strip(),
                "page": int(page),
                "limit": int(limit),
                "keyword": keyword,
            },
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        return [self._to_mapping(item) for item in data]

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "connect":
            self._connected = True
            return ChannelResult(success=True, data={"connected": True})
        if action == "disconnect":
            self._connected = False
            return ChannelResult(success=True, data={"connected": False})
        if action == "health_check":
            status = {
                "connected": self._connected,
                "store": self.store is not None,
                "searchable": self._has_any("search"),
                "runbook_searchable": self._has_any("search_runbooks", "search_runbook"),
            }
            ok = all([status["connected"], status["store"], status["searchable"]])
            return ChannelResult(success=ok, data=status)
        if action == "search":
            rows = await self._search_impl(
                query=str(params["query"]),
                category=params.get("category"),
                top_k=int(params.get("top_k", 5)),
            )
            return ChannelResult(success=True, data=rows)
        if action == "search_runbook":
            rows = await self._search_runbook_impl(symptom=str(params["symptom"]))
            return ChannelResult(success=True, data=rows)
        if action == "list_datasets":
            rows = await self._list_datasets_impl(
                keyword=params.get("keyword"),
                page=int(params.get("page", 1)),
                limit=int(params.get("limit", 20)),
                tag_ids=params.get("tag_ids"),
                include_all=bool(params.get("include_all", False)),
            )
            return ChannelResult(success=True, data=rows)
        if action == "get_dataset":
            row = await self._get_dataset_impl(dataset_id=str(params["dataset_id"]))
            return ChannelResult(success=True, data=row)
        if action == "list_files":
            rows = await self._list_files_impl(
                dataset_id=str(params["dataset_id"]),
                page=int(params.get("page", 1)),
                limit=int(params.get("limit", 20)),
                keyword=params.get("keyword"),
            )
            return ChannelResult(success=True, data=rows)
        if action == "get_file":
            row = await self._get_file_impl(
                dataset_id=str(params["dataset_id"]),
                file_id=str(params["file_id"]),
            )
            return ChannelResult(success=True, data=row)
        if action == "retrieve_chunks":
            rows = await self._retrieve_chunks_impl(
                dataset_id=str(params["dataset_id"]),
                file_id=str(params["file_id"]),
                page=int(params.get("page", 1)),
                limit=int(params.get("limit", 20)),
                keyword=params.get("keyword"),
            )
            return ChannelResult(success=True, data=rows)
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def _search_impl(self, *, query: str, category: str | None, top_k: int) -> list[Any]:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "search", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide search")
        try:
            value = method(query, category=category, top_k=top_k)
        except TypeError:
            value = method(query, top_k=top_k)
        rows = await self._maybe_await(value)
        if isinstance(rows, list):
            return rows
        return []

    async def _search_runbook_impl(self, *, symptom: str) -> list[Any]:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")

        method = getattr(self.store, "search_runbooks", None)
        if method is None:
            method = getattr(self.store, "search_runbook", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide runbook search")

        rows = await self._maybe_await(method(symptom))
        if isinstance(rows, list):
            return rows
        return []

    async def _list_datasets_impl(
        self,
        *,
        keyword: str | None,
        page: int,
        limit: int,
        tag_ids: list[str] | None,
        include_all: bool,
    ) -> list[Any]:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "list_datasets", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide list_datasets")

        try:
            rows = await self._maybe_await(
                method(
                    keyword=keyword,
                    page=page,
                    limit=limit,
                    tag_ids=tag_ids,
                    include_all=include_all,
                )
            )
        except TypeError:
            rows = await self._maybe_await(method(keyword=keyword, page=page, limit=limit))

        if isinstance(rows, list):
            return rows
        return []

    async def _get_dataset_impl(self, *, dataset_id: str) -> Any:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "get_dataset", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide get_dataset")
        row = await self._maybe_await(method(dataset_id))
        if isinstance(row, dict):
            return row
        return {}

    async def _list_files_impl(
        self,
        *,
        dataset_id: str,
        page: int,
        limit: int,
        keyword: str | None,
    ) -> list[Any]:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "list_documents", None)
        if method is None:
            method = getattr(self.store, "list_files", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide list_files/list_documents")

        try:
            rows = await self._maybe_await(
                method(dataset_id, page=page, limit=limit, keyword=keyword)
            )
        except TypeError:
            rows = await self._maybe_await(method(dataset_id, page=page, limit=limit))

        if isinstance(rows, list):
            return rows
        return []

    async def _get_file_impl(self, *, dataset_id: str, file_id: str) -> Any:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "get_document", None)
        if method is None:
            method = getattr(self.store, "get_file", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide get_file/get_document")
        row = await self._maybe_await(method(dataset_id, file_id))
        if isinstance(row, dict):
            return row
        return {}

    async def _retrieve_chunks_impl(
        self,
        *,
        dataset_id: str,
        file_id: str,
        page: int,
        limit: int,
        keyword: str | None,
    ) -> list[Any]:
        if self.store is None:
            raise RuntimeError("knowledge store dependency is not configured")
        method = getattr(self.store, "list_document_segments", None)
        if method is None:
            method = getattr(self.store, "retrieve_chunks", None)
        if method is None:
            raise RuntimeError("knowledge store does not provide retrieve_chunks/list_document_segments")

        try:
            rows = await self._maybe_await(
                method(dataset_id, file_id, page=page, limit=limit, keyword=keyword)
            )
        except TypeError:
            rows = await self._maybe_await(method(dataset_id, file_id, page=page, limit=limit))

        if isinstance(rows, list):
            return rows
        return []

    def _has_any(self, *method_names: str) -> bool:
        if self.store is None:
            return False
        return any(hasattr(self.store, name) for name in method_names)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @classmethod
    def _to_jsonable(cls, value: Any) -> Any:
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump(mode="json")
        if isinstance(value, dict):
            return {str(k): cls._to_jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._to_jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._to_jsonable(item) for item in value]
        return value

    @classmethod
    def _to_mapping(cls, value: Any) -> dict[str, Any]:
        data = cls._to_jsonable(value)
        if isinstance(data, dict):
            return data
        return {"value": data}

    @staticmethod
    def _unwrap(result: ChannelResult, *, default: Any) -> Any:
        if not result.success:
            raise RuntimeError(result.error or "knowledge channel action failed")
        if result.dry_run:
            return default
        if result.data is None:
            return default
        return result.data


__all__ = ["KnowledgeBaseChannel", "DifyKnowledgeStoreAdapter"]
