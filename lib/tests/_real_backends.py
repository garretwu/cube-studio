from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover - optional runtime dependency
    httpx = None  # type: ignore[assignment]


class _HttpJsonClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        if httpx is None:
            raise RuntimeError("httpx is required for real HTTP channel tests")
        self.retries = max(1, int(retries))
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=headers or {},
        )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
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
            except Exception:
                if attempt >= self.retries:
                    raise
                await asyncio.sleep(delay)
                delay = min(delay * 2, 2.0)
        raise RuntimeError("unreachable retry loop")

    async def close(self) -> None:
        await self._client.aclose()


def _to_loki_timestamp(value: datetime | float | int | str) -> str:
    if isinstance(value, datetime):
        return str(int(value.timestamp() * 1_000_000_000))
    if isinstance(value, str):
        return value
    as_float = float(value)
    if as_float > 1e14:
        return str(int(as_float))
    return str(int(as_float * 1_000_000_000))


def _to_prom_timestamp(value: datetime | float | int | str) -> str:
    if isinstance(value, datetime):
        return str(value.timestamp())
    if isinstance(value, str):
        return value
    return str(float(value))


def _extract_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "entities", "chunks", "runbooks", "data"):
        candidate = payload.get(key)
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            nested = _extract_list(candidate)
            if nested:
                return nested
    return []


def _unwrap_sre_envelope(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "success" not in payload or "data" not in payload:
        return payload
    if payload.get("success") is False:
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "SRE API request failed").strip()
            raise RuntimeError(message or "SRE API request failed")
        raise RuntimeError("SRE API request failed")
    return payload.get("data")


class LokiHttpBackend:
    """Loki HTTP adapter for real channel tests."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._client = _HttpJsonClient(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            headers=headers,
        )

    async def query(self, query: str, limit: int = 100, direction: str = "backward") -> Any:
        return await self._client.request_json(
            "GET",
            "/loki/api/v1/query",
            params={
                "query": query,
                "limit": int(limit),
                "direction": direction,
            },
        )

    async def query_range(
        self,
        query: str,
        start: datetime | float | int | str,
        end: datetime | float | int | str,
        step: str = "30s",
        limit: int = 1000,
        direction: str = "backward",
    ) -> Any:
        return await self._client.request_json(
            "GET",
            "/loki/api/v1/query_range",
            params={
                "query": query,
                "start": _to_loki_timestamp(start),
                "end": _to_loki_timestamp(end),
                "step": step,
                "limit": int(limit),
                "direction": direction,
            },
        )

    async def aclose(self) -> None:
        await self._client.close()


class PrometheusHttpBackend:
    """Prometheus HTTP adapter for real alert-channel tests."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._client = _HttpJsonClient(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            headers=headers,
        )

    async def query_range(
        self,
        query: str,
        start: datetime | float | int | str,
        end: datetime | float | int | str,
        step: str = "1m",
    ) -> Any:
        return await self._client.request_json(
            "GET",
            "/api/v1/query_range",
            params={
                "query": query,
                "start": _to_prom_timestamp(start),
                "end": _to_prom_timestamp(end),
                "step": step,
            },
        )

    async def aclose(self) -> None:
        await self._client.close()


class OntologyHttpAdapter:
    """Ontology HTTP adapter used only in real tests."""

    def __init__(
        self,
        *,
        base_url: str,
        query_path: str,
        blast_path: str,
        path_path: str,
        refresh_path: str | None = None,
        timeout: float,
        retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.query_path = query_path
        self.blast_path = blast_path
        self.path_path = path_path
        self.refresh_path = refresh_path
        self._client = _HttpJsonClient(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            headers=headers,
        )

    async def find_entities(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = {"entity_type": entity_type, "filters": filters or {}}
        response = _unwrap_sre_envelope(await self._client.request_json("POST", self.query_path, json_body=payload))
        rows = _extract_list(response)
        return [row for row in rows if isinstance(row, dict)]

    async def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        if "{entity_id}" in self.blast_path:
            path = self.blast_path.format(entity_id=entity_id)
            response = await self._client.request_json("GET", path)
        else:
            response = await self._client.request_json("POST", self.blast_path, json_body={"entity_id": entity_id})
        response = _unwrap_sre_envelope(response)
        if isinstance(response, dict):
            return response
        if isinstance(response, list):
            return {"items": response}
        return {"value": response}

    async def get_path(self, from_id: str, to_id: str) -> list[str]:
        if "{from_id}" in self.path_path or "{to_id}" in self.path_path:
            path = self.path_path.format(from_id=from_id, to_id=to_id)
            response = await self._client.request_json("GET", path)
        else:
            try:
                response = await self._client.request_json(
                    "GET",
                    self.path_path,
                    params={"from_id": from_id, "to_id": to_id},
                )
            except Exception:
                response = await self._client.request_json(
                    "POST",
                    self.path_path,
                    json_body={"from_id": from_id, "to_id": to_id},
                )
        response = _unwrap_sre_envelope(response)

        if isinstance(response, list):
            return [str(item) for item in response]
        if isinstance(response, dict):
            for key in ("path", "nodes", "data", "items"):
                value = response.get(key)
                if isinstance(value, list):
                    return [str(item) for item in value]
        raise RuntimeError("ontology path response does not contain a path list")

    async def refresh_entity(self, entity_id: str) -> dict[str, Any]:
        if not self.refresh_path:
            raise RuntimeError("refresh path is not configured for ontology adapter")
        response = await self._client.request_json(
            "POST",
            self.refresh_path,
            json_body={"entity_id": entity_id},
        )
        response = _unwrap_sre_envelope(response)
        if isinstance(response, dict):
            return response
        return {"value": response}

    async def aclose(self) -> None:
        await self._client.close()


class KnowledgeHttpAdapter:
    """Knowledge HTTP adapter used only in real tests."""

    def __init__(
        self,
        *,
        base_url: str,
        search_path: str,
        runbook_path: str,
        timeout: float,
        retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.search_path = search_path
        self.runbook_path = runbook_path
        self._client = _HttpJsonClient(
            base_url=base_url,
            timeout=timeout,
            retries=retries,
            headers=headers,
        )

    async def search(self, query: str, *, category: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        payload = {"query": query, "category": category, "top_k": int(top_k)}
        try:
            response = await self._client.request_json("POST", self.search_path, json_body=payload)
        except Exception:
            response = await self._client.request_json(
                "GET",
                self.search_path,
                params={"query": query, "category": category, "top_k": int(top_k)},
            )
        rows = _extract_list(response)
        return [row for row in rows if isinstance(row, dict)]

    async def search_runbooks(self, symptom: str) -> list[dict[str, Any]]:
        payload = {"symptom": symptom}
        try:
            response = await self._client.request_json("POST", self.runbook_path, json_body=payload)
        except Exception:
            response = await self._client.request_json(
                "GET",
                self.runbook_path,
                params={"symptom": symptom},
            )
        rows = _extract_list(response)
        return [row for row in rows if isinstance(row, dict)]

    async def aclose(self) -> None:
        await self._client.close()
