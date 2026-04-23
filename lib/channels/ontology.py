"""Ontology graph query channel for SRE workflows."""
from __future__ import annotations

import inspect
from typing import Any

from .base import BaseChannel, ChannelResult


class OntologyChannel(BaseChannel):
    """Channel wrapper over an injected ontology graph/store object."""

    def __init__(
        self,
        *,
        ontology: Any | None,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.ontology = ontology
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

    async def query(self, entity_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result = await self.execute(
            "query",
            {"entity_type": entity_type, "filters": filters or {}},
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            out.append(self._to_mapping(item))
        return out

    async def get_blast_radius(self, entity_id: str) -> dict[str, Any]:
        result = await self.execute("get_blast_radius", {"entity_id": entity_id})
        data = self._unwrap(result, default={})
        if isinstance(data, dict):
            return data
        return {"value": data}

    async def get_path(self, from_id: str, to_id: str) -> list[str] | None:
        result = await self.execute("get_path", {"from_id": from_id, "to_id": to_id})
        data = self._unwrap(result, default=None)
        if data is None:
            return None
        if isinstance(data, list):
            return [str(item) for item in data]
        return [str(data)]

    async def get_neighbors(self, entity_id: str, relation: str | None = None) -> list[dict[str, Any]]:
        result = await self.execute("get_neighbors", {"entity_id": entity_id, "relation": relation})
        data = self._unwrap(result, default=[])
        if isinstance(data, list):
            return data
        return []

    async def refresh_entity(self, entity_id: str) -> dict[str, Any]:
        result = await self.execute("refresh_entity", {"entity_id": entity_id})
        data = self._unwrap(result, default={})
        if isinstance(data, dict):
            return data
        return {"value": data}

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
                "ontology": self.ontology is not None,
                "queryable": self._has_any("find_entities", "query"),
            }
            ok = all([status["connected"], status["ontology"], status["queryable"]])
            return ChannelResult(success=ok, data=status)
        if action == "query":
            data = await self._invoke_ontology(
                ("find_entities", "query"),
                str(params["entity_type"]),
                params.get("filters") or {},
            )
            rows = data if isinstance(data, list) else []
            return ChannelResult(success=True, data=rows)
        if action == "get_blast_radius":
            data = await self._invoke_ontology(("get_blast_radius",), str(params["entity_id"]))
            return ChannelResult(success=True, data=self._to_jsonable(data))
        if action == "get_path":
            data = await self._invoke_ontology(
                ("get_path",),
                str(params["from_id"]),
                str(params["to_id"]),
            )
            return ChannelResult(success=True, data=self._to_jsonable(data))
        if action == "get_neighbors":
            data = await self._invoke_ontology(("get_neighbors",), str(params["entity_id"]))
            rows = data if isinstance(data, list) else []
            return ChannelResult(success=True, data=self._to_jsonable(rows))
        if action == "refresh_entity":
            if self._has_any("refresh_entity"):
                data = await self._invoke_ontology(("refresh_entity",), str(params["entity_id"]))
                return ChannelResult(success=True, data=self._to_jsonable(data))
            return ChannelResult(
                success=True,
                data={
                    "entity_id": str(params["entity_id"]),
                    "refreshed": False,
                    "reason": "refresh_entity is not implemented on ontology dependency",
                },
            )
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def _invoke_ontology(self, method_names: tuple[str, ...], *args: Any) -> Any:
        if self.ontology is None:
            raise RuntimeError("ontology dependency is not configured")
        for method_name in method_names:
            method = getattr(self.ontology, method_name, None)
            if method is None:
                continue
            value = method(*args)
            return await self._maybe_await(value)
        raise RuntimeError(f"ontology dependency does not provide any of: {', '.join(method_names)}")

    def _has_any(self, *method_names: str) -> bool:
        if self.ontology is None:
            return False
        return any(hasattr(self.ontology, name) for name in method_names)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @classmethod
    def _to_jsonable(cls, value: Any) -> Any:
        if value is None:
            return None
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
            raise RuntimeError(result.error or "ontology channel action failed")
        if result.dry_run:
            return default
        if result.data is None:
            return default
        return result.data


__all__ = ["OntologyChannel"]

