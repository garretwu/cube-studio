"""Loki-backed log channel for SRE diagnostics."""
from __future__ import annotations

import inspect
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .base import BaseChannel, ChannelResult, SafetyViolationError

LOGGER = logging.getLogger(__name__)


class LokiLogBackend(Protocol):
    """Narrow Loki adapter used by LogChannel."""

    def query(self, *args: Any, **kwargs: Any) -> Any: ...

    def query_range(self, *args: Any, **kwargs: Any) -> Any: ...


class LogChannel(BaseChannel):
    """Unified log access over a Loki-compatible backend."""

    ALLOWED_LOG_PATHS = {
        "/var/log/syslog",
        "/var/log/messages",
        "/var/log/kern.log",
        "/var/log/dmesg",
        "/var/log/auth.log",
        "/var/log/gpu-manager.log",
    }
    MAX_FILTER_LENGTH = 100
    MAX_QUERY_LENGTH = 2000
    _DURATION_TOKEN = re.compile(r"(\d+)([smhd])")
    _LABEL_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    _LABEL_VALUE_RE = re.compile(r"^[a-zA-Z0-9._:/-]+$")

    def __init__(
        self,
        *,
        k8s: Any | None = None,
        ssh: Any | None = None,
        loki: LokiLogBackend | None = None,
        cube_studio: Any | None = None,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        # Keep legacy dependency names for constructor compatibility.
        self.k8s = k8s
        self.ssh = ssh
        self.loki = loki if loki is not None else k8s
        self.cube_studio = cube_studio
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

    async def query_logs(
        self,
        query: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        step: str = "30s",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if len(query) > self.MAX_QUERY_LENGTH:
            raise SafetyViolationError(f"query too long (max {self.MAX_QUERY_LENGTH} chars)")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        result = await self.execute(
            "query_logs",
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
                "limit": int(limit),
            },
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                out.append(
                    {
                        "timestamp": item.get("timestamp"),
                        "line": str(item.get("line", "")),
                        "labels": item.get("labels", {}) if isinstance(item.get("labels"), dict) else {},
                    }
                )
        return out

    async def read_pod_logs(
        self,
        pod: str,
        namespace: str,
        *,
        tail: int = 200,
        since: str | None = None,
    ) -> list[str]:
        result = await self.execute(
            "read_pod_logs",
            {"pod": pod, "namespace": namespace, "tail": int(tail), "since": since},
        )
        data = self._unwrap(result, default=[])
        return self._normalize_lines(data)

    async def search_pod_logs(
        self,
        pattern: str,
        namespace: str,
        *,
        pod_selector: str | None = None,
    ) -> list[dict[str, str]]:
        result = await self.execute(
            "search_pod_logs",
            {"pattern": pattern, "namespace": namespace, "pod_selector": pod_selector},
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        out: list[dict[str, str]] = []
        for item in data:
            if isinstance(item, dict):
                out.append({"pod": str(item.get("pod", "")), "line": str(item.get("line", ""))})
        return out

    async def read_system_log(
        self,
        node: str,
        *,
        log_path: str = "/var/log/syslog",
        tail: int = 100,
    ) -> list[str]:
        if log_path not in self.ALLOWED_LOG_PATHS:
            raise SafetyViolationError(f"log_path {log_path!r} is not allowed")
        if int(tail) <= 0:
            raise ValueError("tail must be > 0")

        result = await self.execute(
            "read_system_log",
            {"node": node, "log_path": log_path, "tail": int(tail)},
        )
        data = self._unwrap(result, default=[])
        return self._normalize_lines(data)

    async def read_dmesg(
        self,
        node: str,
        *,
        filter_str: str | None = None,
    ) -> list[str]:
        if filter_str and len(filter_str) > self.MAX_FILTER_LENGTH:
            raise SafetyViolationError(f"filter_str too long (max {self.MAX_FILTER_LENGTH} chars)")

        result = await self.execute(
            "read_dmesg",
            {"node": node, "filter_str": filter_str},
        )
        data = self._unwrap(result, default=[])
        return self._normalize_lines(data)

    async def query_logs_range_only(
        self,
        query: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        lookback: str = "15m",
        step: str = "30s",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("query must not be blank")
        if len(query) > self.MAX_QUERY_LENGTH:
            raise SafetyViolationError(f"query too long (max {self.MAX_QUERY_LENGTH} chars)")
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")

        resolved_start, resolved_end = self._compute_default_range(
            start=start,
            end=end,
            lookback=lookback,
            default=timedelta(minutes=15),
        )
        payload = await self._query_loki_range_only(
            query=query,
            start=resolved_start,
            end=resolved_end,
            step=step,
            limit=int(limit),
        )
        entries = self._extract_entries(payload)[: int(limit)]
        out: list[dict[str, Any]] = []
        for entry in entries:
            labels = entry.get("labels")
            out.append(
                {
                    "timestamp": entry.get("timestamp"),
                    "line": str(entry.get("line", "")),
                    "labels": labels if isinstance(labels, dict) else {},
                }
            )
        return out

    async def query_logs_by_labels(
        self,
        labels: dict[str, str],
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        lookback: str = "15m",
        step: str = "30s",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        selector = self._build_stream_selector(self._normalize_selector_input(labels))
        return await self.query_logs_range_only(
            selector,
            start=start,
            end=end,
            lookback=lookback,
            step=step,
            limit=limit,
        )

    async def read_logs_by_selector(
        self,
        labels: dict[str, str],
        *,
        regex: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        lookback: str = "15m",
        tail: int = 200,
    ) -> list[str]:
        if int(tail) <= 0:
            raise ValueError("tail must be > 0")

        selector = self._build_stream_selector(self._normalize_selector_input(labels))
        query = selector
        if regex:
            regex_text = self._sanitize_regex(regex, field="regex", max_len=256)
            query = f'{selector} |~ "{regex_text}"'

        resolved_start, resolved_end = self._compute_default_range(
            start=start,
            end=end,
            lookback=lookback,
            default=timedelta(minutes=15),
        )
        payload = await self._query_loki_range_only(
            query=query,
            start=resolved_start,
            end=resolved_end,
            step="30s",
            limit=int(tail),
        )
        entries = self._extract_entries(payload)
        return [str(entry.get("line", "")) for entry in entries[: int(tail)]]

    async def search_logs_by_selector(
        self,
        labels: dict[str, str],
        pattern: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        lookback: str = "15m",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if int(limit) <= 0:
            raise ValueError("limit must be > 0")
        regex_text = self._sanitize_regex(pattern, field="pattern", max_len=256)

        selector = self._build_stream_selector(self._normalize_selector_input(labels))
        query = f'{selector} |~ "{regex_text}"'
        resolved_start, resolved_end = self._compute_default_range(
            start=start,
            end=end,
            lookback=lookback,
            default=timedelta(minutes=15),
        )
        payload = await self._query_loki_range_only(
            query=query,
            start=resolved_start,
            end=resolved_end,
            step="30s",
            limit=int(limit),
        )
        entries = self._extract_entries(payload)[: int(limit)]
        out: list[dict[str, Any]] = []
        for entry in entries:
            labels_payload = entry.get("labels")
            out.append(
                {
                    "timestamp": entry.get("timestamp"),
                    "line": str(entry.get("line", "")),
                    "labels": labels_payload if isinstance(labels_payload, dict) else {},
                }
            )
        return out

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
                "loki": self.loki is not None,
                "queryable": self._has_loki_api(),
                # Legacy visibility only; no longer used for execution.
                "k8s": self.k8s is not None,
                "ssh": self.ssh is not None,
                "cube_studio": self.cube_studio is not None,
            }
            ok = all([status["connected"], status["loki"], status["queryable"]])
            return ChannelResult(success=ok, data=status)
        if action == "query_logs":
            rows = await self._query_logs_impl(
                query=str(params["query"]),
                start=params.get("start"),
                end=params.get("end"),
                step=str(params.get("step") or "30s"),
                limit=int(params.get("limit", 200)),
            )
            return ChannelResult(success=True, data=rows)
        if action == "read_pod_logs":
            lines = await self._read_pod_logs_impl(
                pod=str(params["pod"]),
                namespace=str(params["namespace"]),
                tail=int(params.get("tail", 200)),
                since=params.get("since"),
            )
            return ChannelResult(success=True, data=lines)
        if action == "search_pod_logs":
            matches = await self._search_pod_logs_impl(
                pattern=str(params["pattern"]),
                namespace=str(params["namespace"]),
                pod_selector=params.get("pod_selector"),
            )
            return ChannelResult(success=True, data=matches)
        if action == "read_system_log":
            lines = await self._read_system_log_impl(
                node=str(params["node"]),
                log_path=str(params["log_path"]),
                tail=int(params.get("tail", 100)),
            )
            return ChannelResult(success=True, data=lines)
        if action == "read_dmesg":
            lines = await self._read_dmesg_impl(
                node=str(params["node"]),
                filter_str=params.get("filter_str"),
            )
            return ChannelResult(success=True, data=lines)
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def _query_logs_impl(
        self,
        *,
        query: str,
        start: datetime | None,
        end: datetime | None,
        step: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        payload = await self._query_loki(
            query=query,
            start=start,
            end=end,
            step=step,
            limit=limit,
        )
        return self._extract_entries(payload)[:limit]

    async def _read_pod_logs_impl(
        self,
        *,
        pod: str,
        namespace: str,
        tail: int,
        since: str | None,
    ) -> list[str]:
        pod = self._validate_label_value(pod, field="pod")
        namespace = self._validate_label_value(namespace, field="namespace")
        if int(tail) <= 0:
            raise ValueError("tail must be > 0")

        selector = self._build_stream_selector({"namespace": namespace, "pod": pod})
        start, end = self._compute_time_range(lookback=since, default=timedelta(minutes=15))
        payload = await self._query_loki(
            query=selector,
            start=start,
            end=end,
            step="30s",
            limit=int(tail),
        )
        entries = self._extract_entries(payload)
        return [entry["line"] for entry in entries[:tail]]

    async def _search_pod_logs_impl(
        self,
        *,
        pattern: str,
        namespace: str,
        pod_selector: str | None,
    ) -> list[dict[str, str]]:
        namespace = self._validate_label_value(namespace, field="namespace")
        regex = self._sanitize_regex(pattern, field="pattern", max_len=256)
        selector_labels = {"namespace": namespace}
        selector_labels.update(self._parse_label_selector(pod_selector))
        query = f'{self._build_stream_selector(selector_labels)} |~ "{regex}"'

        start, end = self._compute_time_range(lookback="15m", default=timedelta(minutes=15))
        payload = await self._query_loki(
            query=query,
            start=start,
            end=end,
            step="30s",
            limit=500,
        )
        entries = self._extract_entries(payload)

        out: list[dict[str, str]] = []
        for entry in entries:
            labels = entry.get("labels", {})
            pod_name = ""
            if isinstance(labels, dict):
                pod_name = str(labels.get("pod") or labels.get("pod_name") or "")
            out.append({"pod": pod_name, "line": str(entry.get("line", ""))})
        return out

    async def _read_system_log_impl(self, *, node: str, log_path: str, tail: int) -> list[str]:
        node = self._validate_label_value(node, field="node")
        selector = self._build_stream_selector(
            {"job": "system", "node": node, "filename": log_path}
        )
        payload = await self._query_loki(query=selector, start=None, end=None, step="30s", limit=tail)
        entries = self._extract_entries(payload)
        return [entry["line"] for entry in entries[:tail]]

    async def _read_dmesg_impl(self, *, node: str, filter_str: str | None) -> list[str]:
        node = self._validate_label_value(node, field="node")
        query = self._build_stream_selector({"job": "kernel", "node": node, "source": "dmesg"})
        if filter_str:
            regex = self._sanitize_regex(
                filter_str,
                field="filter_str",
                max_len=self.MAX_FILTER_LENGTH,
            )
            query = f'{query} |~ "{regex}"'
        payload = await self._query_loki(query=query, start=None, end=None, step="30s", limit=200)
        entries = self._extract_entries(payload)
        return [entry["line"] for entry in entries]

    def _has_loki_api(self) -> bool:
        if self.loki is None:
            return False
        return hasattr(self.loki, "query") or hasattr(self.loki, "query_range")

    async def _query_loki(
        self,
        *,
        query: str,
        start: datetime | None,
        end: datetime | None,
        step: str,
        limit: int,
    ) -> Any:
        if self.loki is None:
            raise RuntimeError("loki dependency is not configured")
        if len(query) > self.MAX_QUERY_LENGTH:
            raise SafetyViolationError(f"query too long (max {self.MAX_QUERY_LENGTH} chars)")

        if start is not None and end is not None and hasattr(self.loki, "query_range"):
            method = getattr(self.loki, "query_range")
            try:
                response = await self._call_query_range(
                    method=method,
                    query=query,
                    start=start,
                    end=end,
                    step=step,
                    limit=limit,
                )
                return response
            except Exception as exc:  # noqa: BLE001
                if self._is_recoverable_loki_error(exc):
                    return self._empty_loki_response(query=query, error=exc)
                raise

        if hasattr(self.loki, "query"):
            method = getattr(self.loki, "query")
            try:
                response = await self._call_query(method=method, query=query, limit=limit)
                return response
            except Exception as exc:  # noqa: BLE001
                if self._is_recoverable_loki_error(exc):
                    return self._empty_loki_response(query=query, error=exc)
                raise

        if hasattr(self.loki, "query_range"):
            method = getattr(self.loki, "query_range")
            computed_start, computed_end = self._compute_time_range(
                lookback="15m",
                default=timedelta(minutes=15),
            )
            try:
                return await self._call_query_range(
                    method=method,
                    query=query,
                    start=computed_start,
                    end=computed_end,
                    step=step,
                    limit=limit,
                )
            except Exception as exc:  # noqa: BLE001
                if self._is_recoverable_loki_error(exc):
                    return self._empty_loki_response(query=query, error=exc)
                raise

        raise RuntimeError("loki dependency does not provide query/query_range")

    async def _query_loki_range_only(
        self,
        *,
        query: str,
        start: datetime,
        end: datetime,
        step: str,
        limit: int,
    ) -> Any:
        if self.loki is None:
            raise RuntimeError("loki dependency is not configured")
        if len(query) > self.MAX_QUERY_LENGTH:
            raise SafetyViolationError(f"query too long (max {self.MAX_QUERY_LENGTH} chars)")

        method = getattr(self.loki, "query_range", None)
        if method is None:
            raise RuntimeError("loki dependency does not provide query_range")
        try:
            return await self._call_query_range(
                method=method,
                query=query,
                start=start,
                end=end,
                step=step,
                limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            if self._is_recoverable_loki_error(exc):
                return self._empty_loki_response(query=query, error=exc)
            raise

    async def _call_query(self, *, method: Any, query: str, limit: int) -> Any:
        kwargs_options = [
            {"query": query, "limit": limit, "direction": "backward"},
            {"logql": query, "limit": limit, "direction": "backward"},
            {"query": query, "limit": limit},
            {"logql": query, "limit": limit},
            {"query": query},
            {"logql": query},
        ]
        for kwargs in kwargs_options:
            try:
                return await self._maybe_await(method(**kwargs))
            except TypeError:
                continue

        args_options = [
            (query, limit, "backward"),
            (query, limit),
            (query,),
        ]
        for args in args_options:
            try:
                return await self._maybe_await(method(*args))
            except TypeError:
                continue
        raise RuntimeError("loki query method signature is not supported")

    async def _call_query_range(
        self,
        *,
        method: Any,
        query: str,
        start: datetime,
        end: datetime,
        step: str,
        limit: int,
    ) -> Any:
        kwargs_options = [
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit,
                "direction": "backward",
            },
            {
                "logql": query,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit,
                "direction": "backward",
            },
            {
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
                "limit": limit,
                "direction": "backward",
            },
            {
                "query": query,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step": step,
                "limit": limit,
                "direction": "backward",
            },
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
                "limit": limit,
            },
            {
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
            {
                "query": query,
                "start": start,
                "end": end,
                "step": step,
            },
        ]
        for kwargs in kwargs_options:
            try:
                return await self._maybe_await(method(**kwargs))
            except TypeError:
                continue

        args_options = [
            (query, start, end, step, limit, "backward"),
            (query, start, end, step, limit),
            (query, start.timestamp(), end.timestamp(), step),
            (query, start, end, step),
            (query, start, end),
        ]
        for args in args_options:
            try:
                return await self._maybe_await(method(*args))
            except TypeError:
                continue
        raise RuntimeError("loki query_range method signature is not supported")

    def _compute_time_range(
        self,
        *,
        lookback: str | None,
        default: timedelta,
    ) -> tuple[datetime, datetime]:
        end = datetime.now(UTC)
        if lookback is None:
            return end - default, end
        return end - self._parse_duration(lookback), end

    def _compute_default_range(
        self,
        *,
        start: datetime | None,
        end: datetime | None,
        lookback: str | None,
        default: timedelta,
    ) -> tuple[datetime, datetime]:
        if start is not None and start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if end is not None and end.tzinfo is None:
            raise ValueError("end must be timezone-aware")

        if start is not None and end is not None:
            if start >= end:
                raise ValueError("start must be earlier than end")
            return start, end

        if start is not None:
            computed_end = datetime.now(UTC)
            if start >= computed_end:
                raise ValueError("start must be earlier than end")
            return start, computed_end

        if end is not None:
            if lookback is None:
                duration = default
            else:
                duration = self._parse_duration(lookback)
            computed_start = end - duration
            if computed_start >= end:
                raise ValueError("start must be earlier than end")
            return computed_start, end

        return self._compute_time_range(lookback=lookback, default=default)

    def _normalize_selector_input(self, labels: dict[str, str]) -> dict[str, str]:
        if not isinstance(labels, dict):
            raise ValueError("labels must be a dict[str, str]")
        if not labels:
            raise ValueError("labels must not be empty")

        normalized: dict[str, str] = {}
        for key, value in labels.items():
            key_text = str(key).strip()
            if not key_text:
                raise ValueError("label key must not be blank")
            value_text = self._validate_label_value(str(value), field=key_text)
            normalized[key_text] = value_text
        return normalized

    def _parse_duration(self, duration: str) -> timedelta:
        text = duration.strip().lower()
        if not text:
            raise ValueError("duration must not be blank")
        total_seconds = 0
        consumed = 0
        for match in self._DURATION_TOKEN.finditer(text):
            value = int(match.group(1))
            unit = match.group(2)
            consumed += len(match.group(0))
            if unit == "s":
                total_seconds += value
            elif unit == "m":
                total_seconds += value * 60
            elif unit == "h":
                total_seconds += value * 3600
            elif unit == "d":
                total_seconds += value * 86400
        if consumed != len(text) or total_seconds <= 0:
            raise ValueError(f"invalid duration format: {duration!r}")
        return timedelta(seconds=total_seconds)

    def _parse_label_selector(self, selector: str | None) -> dict[str, str]:
        if not selector:
            return {}
        labels: dict[str, str] = {}
        chunks = [chunk.strip() for chunk in selector.split(",") if chunk.strip()]
        for chunk in chunks:
            if "=" not in chunk:
                raise SafetyViolationError("pod_selector entries must use key=value syntax")
            key, value = chunk.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not self._LABEL_KEY_RE.fullmatch(key):
                raise SafetyViolationError(f"invalid label key in pod_selector: {key!r}")
            labels[key] = self._validate_label_value(value, field=f"pod_selector[{key}]")
        return labels

    def _build_stream_selector(self, labels: dict[str, str]) -> str:
        pairs: list[str] = []
        for key, value in sorted(labels.items()):
            if not self._LABEL_KEY_RE.fullmatch(key):
                raise SafetyViolationError(f"invalid label key: {key!r}")
            safe_value = self._validate_label_value(str(value), field=key)
            pairs.append(f'{key}="{self._logql_escape(safe_value)}"')
        return "{" + ",".join(pairs) + "}"

    def _validate_label_value(self, value: str, *, field: str) -> str:
        text = str(value).strip()
        if not text:
            raise ValueError(f"{field} must not be blank")
        if not self._LABEL_VALUE_RE.fullmatch(text):
            raise SafetyViolationError(f"{field} contains unsupported characters")
        return text

    def _sanitize_regex(self, value: str, *, field: str, max_len: int) -> str:
        text = value.strip()
        if not text:
            raise ValueError(f"{field} must not be blank")
        if len(text) > max_len:
            raise SafetyViolationError(f"{field} too long (max {max_len} chars)")
        re.compile(text)
        return self._logql_escape(text)

    def _extract_entries(self, payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []

        if isinstance(payload, list):
            return self._extract_entries_from_list(payload)
        if not isinstance(payload, dict):
            return [{"timestamp": None, "line": str(payload), "labels": {}}]

        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, list):
            return self._extract_entries_from_result(result)
        if isinstance(data, list):
            return self._extract_entries_from_list(data)
        return []

    def _extract_entries_from_list(self, rows: list[Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, str):
                entries.append({"timestamp": None, "line": row, "labels": {}})
                continue
            if not isinstance(row, dict):
                entries.append({"timestamp": None, "line": str(row), "labels": {}})
                continue
            labels = row.get("labels")
            if not isinstance(labels, dict):
                labels = {}
            line = row.get("line")
            if line is None:
                line = row.get("message", "")
            timestamp = row.get("timestamp") or row.get("ts")
            entries.append({"timestamp": timestamp, "line": str(line), "labels": labels})
        return entries

    def _extract_entries_from_result(self, result: list[Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for stream_row in result:
            if not isinstance(stream_row, dict):
                continue
            stream = stream_row.get("stream")
            labels = stream if isinstance(stream, dict) else {}

            values = stream_row.get("values")
            if isinstance(values, list):
                for pair in values:
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                        entries.append(
                            {
                                "timestamp": pair[0],
                                "line": str(pair[1]),
                                "labels": labels,
                            }
                        )

            value = stream_row.get("value")
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                entries.append(
                    {
                        "timestamp": value[0],
                        "line": str(value[1]),
                        "labels": labels,
                    }
                )
        return entries

    @staticmethod
    def _logql_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _is_recoverable_loki_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "400 bad request" in message or "invalid query" in message or "parse error" in message

    @staticmethod
    def _empty_loki_response(*, query: str, error: Exception) -> dict[str, Any]:
        LOGGER.warning("loki query degraded to empty result: query=%s error=%s", query, error)
        return {"data": {"result": []}}

    @staticmethod
    def _normalize_lines(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return value.splitlines()
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value]
        return [str(value)]

    @staticmethod
    def _unwrap(result: ChannelResult, *, default: Any) -> Any:
        if not result.success:
            raise RuntimeError(result.error or "log channel action failed")
        if result.dry_run:
            return default
        if result.data is None:
            return default
        return result.data


__all__ = ["LogChannel"]
