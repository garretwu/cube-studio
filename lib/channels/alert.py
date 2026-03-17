"""Alert lifecycle + Prometheus origin-metrics channel for SRE alerts."""
from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sre_agent.models.alert import Alert

from .base import BaseChannel, ChannelResult

try:
    import httpx
except Exception:  # pragma: no cover - optional runtime dependency
    httpx = None  # type: ignore[assignment]


class AlertLifecycleBackend(Protocol):
    """Narrow alert lifecycle backend interface (Alertmanager-compatible)."""

    def get(self, *args: Any, **kwargs: Any) -> Any: ...

    def post(self, *args: Any, **kwargs: Any) -> Any: ...


class OriginMetricsBackend(Protocol):
    """Narrow metrics backend interface for origin metric queries."""

    def query_range(self, *args: Any, **kwargs: Any) -> Any: ...


class AlertChannel(BaseChannel):
    """Alert source integration with strict lifecycle/metrics separation."""

    _DURATION_TOKEN = re.compile(r"(\d+)([smhd])")

    def __init__(
        self,
        *,
        alertmanager_url: str = "",
        client: AlertLifecycleBackend | None = None,
        metrics_backend: OriginMetricsBackend | None = None,
        timeout: int = 15,
        dry_run: bool = False,
        wal: Any | None = None,
        guard: Any | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal, guard=guard)
        self.alertmanager_url = alertmanager_url.rstrip("/")
        self.timeout = timeout
        self.metrics_backend = metrics_backend
        self._connected = False
        self._owns_client = False

        if client is not None:
            self._client = client
            return

        self._client = None
        if self.alertmanager_url:
            if httpx is None:
                raise RuntimeError("httpx is required to create AlertChannel client")
            self._client = httpx.AsyncClient(base_url=self.alertmanager_url, timeout=timeout)
            self._owns_client = True

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

    async def get_active_alerts(self, filter_labels: dict[str, str] | None = None) -> list[Alert]:
        result = await self.execute("get_active_alerts", {"filter_labels": filter_labels or {}})
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        out: list[Alert] = []
        for item in data:
            if isinstance(item, Alert):
                out.append(item)
            else:
                out.append(Alert.model_validate(item))
        return out

    async def get_alert_history(self, alert_name: str, lookback: str = "24h") -> list[Alert]:
        result = await self.execute(
            "get_alert_history",
            {"alert_name": alert_name, "lookback": lookback},
        )
        data = self._unwrap(result, default=[])
        if not isinstance(data, list):
            return []
        out: list[Alert] = []
        for item in data:
            if isinstance(item, Alert):
                out.append(item)
            else:
                out.append(Alert.model_validate(item))
        return out

    async def get_origin_metrics(
        self,
        alert_name: str,
        *,
        lookback: str = "24h",
        metric_name: str | None = None,
        labels: dict[str, str] | None = None,
        step: str = "1m",
    ) -> list[dict[str, Any]]:
        result = await self.execute(
            "get_origin_metrics",
            {
                "alert_name": alert_name,
                "lookback": lookback,
                "metric_name": metric_name,
                "labels": labels or {},
                "step": step,
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
                        "value": item.get("value"),
                        "labels": item.get("labels", {}) if isinstance(item.get("labels"), dict) else {},
                        "metric": str(item.get("metric") or ""),
                    }
                )
        return out

    async def silence_alert(self, alert_id: str, duration: str, comment: str) -> str:
        result = await self.execute(
            "silence_alert",
            {"alert_id": alert_id, "duration": duration, "comment": comment},
        )
        data = self._unwrap(result, default="")
        if not isinstance(data, str):
            return str(data)
        return data

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        if action == "connect":
            self._connected = True
            return ChannelResult(success=True, data={"connected": True})
        if action == "disconnect":
            if self._owns_client and self._client is not None and hasattr(self._client, "aclose"):
                await self._maybe_await(self._client.aclose())
            self._connected = False
            return ChannelResult(success=True, data={"connected": False})
        if action == "health_check":
            status = {
                "connected": self._connected,
                "client": self._client is not None,
                "alertmanager": False,
                "metrics": self._has_metrics_api(),
            }
            if self._client is not None and self._connected:
                try:
                    response = await self._http_get("/-/healthy", params=None)
                    self._ensure_success(response)
                    status["alertmanager"] = True
                except Exception:
                    status["alertmanager"] = False
            ok = all([status["connected"], status["client"], status["alertmanager"], status["metrics"]])
            return ChannelResult(success=ok, data=status)
        if action == "get_active_alerts":
            alerts = await self._get_active_alerts_impl(filter_labels=params.get("filter_labels") or {})
            return ChannelResult(success=True, data=alerts)
        if action == "get_alert_history":
            alerts = await self._get_alert_history_impl(
                alert_name=str(params["alert_name"]),
                lookback=str(params.get("lookback", "24h")),
            )
            return ChannelResult(success=True, data=alerts)
        if action == "get_origin_metrics":
            points = await self._get_origin_metrics_impl(
                alert_name=str(params["alert_name"]),
                lookback=str(params.get("lookback", "24h")),
                metric_name=params.get("metric_name"),
                labels=params.get("labels") if isinstance(params.get("labels"), dict) else {},
                step=str(params.get("step") or "1m"),
            )
            return ChannelResult(success=True, data=points)
        if action == "silence_alert":
            silence_id = await self._silence_alert_impl(
                alert_id=str(params["alert_id"]),
                duration=str(params["duration"]),
                comment=str(params["comment"]),
            )
            return ChannelResult(success=True, data=silence_id)
        return ChannelResult(success=False, error=f"unknown action: {action}")

    async def _get_active_alerts_impl(self, *, filter_labels: dict[str, str]) -> list[Alert]:
        filters = self._build_filters(filter_labels)
        params: dict[str, Any] = {}
        if filters:
            params["filter"] = filters
        response = await self._http_get("/api/v2/alerts", params=params or None)
        self._ensure_success(response)
        payload = self._response_json(response)
        if not isinstance(payload, list):
            return []
        return [Alert.model_validate(item) for item in payload if isinstance(item, dict)]

    async def _get_alert_history_impl(self, *, alert_name: str, lookback: str) -> list[Alert]:
        if not alert_name.strip():
            raise ValueError("alert_name must not be blank")

        active_alerts = await self._get_active_alerts_impl(filter_labels={"alertname": alert_name})
        points = await self._get_origin_metrics_impl(
            alert_name=alert_name,
            lookback=lookback,
            metric_name=None,
            labels={},
            step="1m",
        )
        metric_history = self._alerts_from_origin_metrics(
            alert_name=alert_name,
            points=points,
            active_alerts=active_alerts,
        )
        if metric_history:
            return metric_history
        return active_alerts

    async def _get_origin_metrics_impl(
        self,
        *,
        alert_name: str,
        lookback: str,
        metric_name: str | None,
        labels: dict[str, str],
        step: str,
    ) -> list[dict[str, Any]]:
        if not alert_name.strip():
            raise ValueError("alert_name must not be blank")
        if self.metrics_backend is None:
            raise RuntimeError("metrics backend is not configured")

        duration = self._parse_duration(lookback)
        end = datetime.now(UTC)
        start = end - duration

        matcher_labels = {"alertname": alert_name}
        matcher_labels.update({k: v for k, v in labels.items() if isinstance(k, str) and isinstance(v, str)})
        metric = metric_name or "ALERTS"
        promql = self._build_metric_query(metric_name=metric, labels=matcher_labels)

        raw = await self._query_metric_range(
            promql=promql,
            start=start,
            end=end,
            step=step,
        )
        return self._normalize_metric_points(raw=raw, metric_name=metric)

    async def _query_metric_range(
        self,
        *,
        promql: str,
        start: datetime,
        end: datetime,
        step: str,
    ) -> Any:
        if self.metrics_backend is None:
            raise RuntimeError("metrics backend is not configured")

        method = getattr(self.metrics_backend, "query_range", None)
        if method is None:
            raise RuntimeError("metrics backend does not provide query_range")

        kwargs_options = [
            {"promql": promql, "start": start, "end": end, "step": step},
            {"query": promql, "start": start, "end": end, "step": step},
            {"promql": promql, "start": start.timestamp(), "end": end.timestamp(), "step": step},
            {"query": promql, "start": start.timestamp(), "end": end.timestamp(), "step": step},
            {"query": promql, "start": start.isoformat(), "end": end.isoformat(), "step": step},
        ]
        for kwargs in kwargs_options:
            try:
                return await self._maybe_await(method(**kwargs))
            except TypeError:
                continue

        args_options = [
            (promql, start, end, step),
            (promql, start.timestamp(), end.timestamp(), step),
            (promql, start, end),
            (promql,),
        ]
        for args in args_options:
            try:
                return await self._maybe_await(method(*args))
            except TypeError:
                continue
        raise RuntimeError("metrics query_range method signature is not supported")

    def _normalize_metric_points(self, *, raw: Any, metric_name: str) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []

        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    points.append(
                        {
                            "timestamp": item[0],
                            "value": self._to_float(item[1]),
                            "labels": {},
                            "metric": metric_name,
                        }
                    )
                elif isinstance(item, dict):
                    points.extend(self._normalize_point_mapping(item, metric_name=metric_name))
            return points

        if not isinstance(raw, dict):
            return points

        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, list):
            for series in result:
                if not isinstance(series, dict):
                    continue
                labels = series.get("metric") if isinstance(series.get("metric"), dict) else {}
                values = series.get("values")
                if isinstance(values, list):
                    for pair in values:
                        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                            points.append(
                                {
                                    "timestamp": pair[0],
                                    "value": self._to_float(pair[1]),
                                    "labels": labels,
                                    "metric": metric_name,
                                }
                            )
                value = series.get("value")
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    points.append(
                        {
                            "timestamp": value[0],
                            "value": self._to_float(value[1]),
                            "labels": labels,
                            "metric": metric_name,
                        }
                    )
            return points

        points.extend(self._normalize_point_mapping(data, metric_name=metric_name))
        return points

    def _normalize_point_mapping(self, payload: Any, *, metric_name: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if "timestamp" in payload and "value" in payload:
            labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
            return [
                {
                    "timestamp": payload.get("timestamp"),
                    "value": self._to_float(payload.get("value")),
                    "labels": labels,
                    "metric": metric_name,
                }
            ]
        return []

    def _alerts_from_origin_metrics(
        self,
        *,
        alert_name: str,
        points: list[dict[str, Any]],
        active_alerts: list[Alert],
    ) -> list[Alert]:
        if not points:
            return []

        groups: dict[str, list[dict[str, Any]]] = {}
        for point in points:
            labels = point.get("labels") if isinstance(point.get("labels"), dict) else {}
            key = str(labels.get("fingerprint") or labels.get("instance") or "default")
            groups.setdefault(key, []).append(point)

        template = active_alerts[0] if active_alerts else None
        built: list[Alert] = []

        for key, group in sorted(groups.items()):
            sorted_group = sorted(group, key=lambda item: self._to_timestamp(item.get("timestamp")))
            first = sorted_group[0]
            last = sorted_group[-1]
            start_dt = self._to_datetime(first.get("timestamp"))
            end_dt = self._to_datetime(last.get("timestamp"))
            last_value = self._to_float(last.get("value"))
            is_firing = last_value > 0

            labels: dict[str, str] = {}
            if template is not None:
                labels.update(template.labels)
            point_labels = first.get("labels") if isinstance(first.get("labels"), dict) else {}
            for label_key, label_value in point_labels.items():
                if isinstance(label_key, str) and isinstance(label_value, str):
                    labels[label_key] = label_value
            labels.setdefault("alertname", alert_name)

            annotations: dict[str, str] = {}
            if template is not None:
                annotations.update(template.annotations)
            annotations.setdefault("summary", f"{alert_name} origin metrics")
            annotations["origin_metric"] = str(last.get("metric") or "")
            annotations["origin_samples"] = str(len(sorted_group))

            severity = labels.get("severity")
            if severity is None and template is not None:
                severity = template.severity.value
            if severity is None:
                severity = "warning"

            payload: dict[str, Any] = {
                "alert_name": alert_name,
                "severity": severity,
                "labels": labels,
                "annotations": annotations,
                "startsAt": start_dt.isoformat(),
                "endsAt": None if is_firing else end_dt.isoformat(),
                "fingerprint": str(labels.get("fingerprint") or f"{alert_name}:{key}"),
                "status": "firing" if is_firing else "resolved",
                "source": "prometheus-origin-metrics",
            }
            built.append(Alert.model_validate(payload))

        return built

    @staticmethod
    def _build_metric_query(metric_name: str, labels: dict[str, str]) -> str:
        clean_metric = metric_name.strip()
        if not clean_metric:
            raise ValueError("metric_name must not be blank")
        if not labels:
            return clean_metric
        matchers = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return f"{clean_metric}" + "{" + ",".join(matchers) + "}"

    @staticmethod
    def _build_filters(filter_labels: dict[str, str] | None) -> list[str]:
        if not filter_labels:
            return []
        out: list[str] = []
        for key, value in sorted(filter_labels.items()):
            out.append(f'{key}="{value}"')
        return out

    async def _http_get(self, path: str, params: dict[str, Any] | None) -> Any:
        if self._client is None:
            raise RuntimeError("alert client is not configured")
        method = getattr(self._client, "get", None)
        if method is None:
            raise RuntimeError("alert client does not support GET")
        return await self._maybe_await(method(path, params=params))

    async def _http_post(self, path: str, json_body: dict[str, Any]) -> Any:
        if self._client is None:
            raise RuntimeError("alert client is not configured")
        method = getattr(self._client, "post", None)
        if method is None:
            raise RuntimeError("alert client does not support POST")
        return await self._maybe_await(method(path, json=json_body))

    @staticmethod
    def _ensure_success(response: Any) -> None:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
            return
        status_code = getattr(response, "status_code", 200)
        if int(status_code) >= 400:
            raise RuntimeError(f"HTTP {status_code}")

    @staticmethod
    def _response_json(response: Any) -> Any:
        json_fn = getattr(response, "json", None)
        if callable(json_fn):
            return json_fn()
        return response

    async def _silence_alert_impl(self, *, alert_id: str, duration: str, comment: str) -> str:
        if not alert_id.strip():
            raise ValueError("alert_id must not be blank")
        if not comment.strip():
            raise ValueError("comment must not be blank")

        now = datetime.now(UTC)
        delta = self._parse_duration(duration)
        payload = {
            "matchers": [{"name": "alertname", "value": alert_id, "isRegex": False}],
            "startsAt": now.isoformat(),
            "endsAt": (now + delta).isoformat(),
            "comment": comment,
            "createdBy": "sre-agent",
        }
        response = await self._http_post("/api/v2/silences", json_body=payload)
        self._ensure_success(response)
        data = self._response_json(response)
        if not isinstance(data, dict):
            raise RuntimeError("unexpected silence response payload")

        silence_id = data.get("silenceID") or data.get("silenceId") or data.get("id")
        if not silence_id:
            raise RuntimeError("silence id not found in response")
        return str(silence_id)

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
            else:
                raise ValueError(f"unsupported duration unit: {unit}")

        if consumed != len(text) or total_seconds <= 0:
            raise ValueError(f"invalid duration format: {duration!r}")
        return timedelta(seconds=total_seconds)

    def _has_metrics_api(self) -> bool:
        return self.metrics_backend is not None and hasattr(self.metrics_backend, "query_range")

    @staticmethod
    def _to_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _to_timestamp(cls, value: Any) -> float:
        parsed = cls._to_float(value)
        if parsed <= 0:
            return 0.0
        if parsed > 1e15:
            parsed = parsed / 1e9
        elif parsed > 1e12:
            parsed = parsed / 1000.0
        return parsed

    @classmethod
    def _to_datetime(cls, value: Any) -> datetime:
        ts = cls._to_timestamp(value)
        if ts <= 0:
            return datetime.now(UTC)
        return datetime.fromtimestamp(ts, tz=UTC)

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    def _unwrap(result: ChannelResult, *, default: Any) -> Any:
        if not result.success:
            raise RuntimeError(result.error or "alert channel action failed")
        if result.dry_run:
            return default
        if result.data is None:
            return default
        return result.data


__all__ = ["AlertChannel"]
