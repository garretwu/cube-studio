"""Cube Studio REST API channel."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

from .base import BaseChannel, ChannelResult


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def build_auth_header(*, method: str, username: str, jwt_secret: str | None = None) -> str:
    """Build Authorization header value for Cube Studio API."""
    if method == "username":
        return username
    if method == "jwt":
        if not jwt_secret:
            raise ValueError("jwt_secret is required for jwt auth")
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"iss": "cube-studio", "sub": username}
        signing_input = (
            _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            + "."
            + _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        )
        signature = hmac.new(jwt_secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
        return signing_input + "." + _b64url(signature)
    raise ValueError(f"Unsupported auth method: {method}")


class CubeStudioChannel(BaseChannel):
    """Cube Studio API client with async httpx, retry/backoff and CRUD helpers."""

    def __init__(
        self,
        *,
        base_url: str,
        auth_method: str = "username",
        username: str = "admin",
        jwt_secret: str | None = None,
        dry_run: bool = False,
        timeout: int = 30,
        retry_count: int = 3,
        retry_backoff: float = 1.0,
        wal: Any | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run, wal=wal)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_count = retry_count
        self.retry_backoff = retry_backoff
        self._headers = {
            "Authorization": build_auth_header(method=auth_method, username=username, jwt_secret=jwt_secret),
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers=self._headers,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _execute_impl(self, action: str, params: dict[str, Any]) -> ChannelResult:
        method = params.get("method", "GET")
        path = params.get("path", "/")
        payload = params.get("json")
        response = await self._request(method, path, json_body=payload)
        return ChannelResult(success=True, data=response)

    def _is_forbidden(self, action: str, params: dict[str, Any]) -> bool:
        _ = action
        method = str(params.get("method", "GET")).upper()
        path = str(params.get("path", ""))
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return True
        if not path.startswith("/"):
            return True
        return False

    async def _execute_request(
        self,
        *,
        action: str,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        recovery_action: str | None = None,
        recovery_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = await self.execute(
            action=action,
            params={"method": method, "path": path, "json": json_body},
            recovery_action=recovery_action,
            recovery_params=recovery_params,
        )
        if not result.success:
            raise RuntimeError(result.error or f"{action} failed")
        payload = result.data
        if isinstance(payload, dict):
            return payload
        return {}

    async def _request(self, method: str, path: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                return await self._http_request_async(method, path, json_body)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt >= self.retry_count:
                    break
                await asyncio.sleep(self.retry_backoff * (2**attempt))
        raise RuntimeError(f"CubeStudio request failed: {last_err}") from last_err

    async def _http_request_async(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.request(
            method=method.upper(),
            url=path,
            json=json_body,
        )
        if response.status_code >= 400:
            detail = response.text[:200]
            raise RuntimeError(f"HTTP {response.status_code}: {detail}")
        try:
            return response.json() if response.text else {}
        except Exception:  # noqa: BLE001
            logger.warning("Failed to parse JSON response from %s %s: %s", method, path, response.text[:200])
            return {}

    # ---- inference service ----
    async def create_inference_service(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_request(
            action="create_inference_service",
            method="POST",
            path="/inferenceservice_modelview/api/",
            json_body=params,
            recovery_action="delete_inference_service",
        )

    async def deploy_inference_service(self, service_id: int, env: str = "test") -> dict[str, Any]:
        return await self._execute_request(
            action="deploy_inference_service",
            method="POST",
            path=f"/inferenceservice_modelview/api/deploy/{env}/{service_id}",
        )

    async def update_inference_replicas(self, service_id: int, min_replicas: int) -> dict[str, Any]:
        return await self._execute_request(
            action="update_inference_replicas",
            method="POST",
            path="/inferenceservice_modelview/api/deploy/update/",
            json_body={"service_id": service_id, "min_replicas": min_replicas},
        )

    async def clear_inference_service(self, service_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="clear_inference_service",
            method="POST",
            path=f"/inferenceservice_modelview/api/clear/{service_id}",
        )

    async def delete_inference_service(self, service_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="delete_inference_service",
            method="DELETE",
            path=f"/inferenceservice_modelview/api/{service_id}",
        )

    async def list_inference_services(self) -> dict[str, Any]:
        return await self._execute_request(
            action="list_inference_services",
            method="GET",
            path="/inferenceservice_modelview/api/",
        )

    async def get_service_status(self, service_name: str) -> dict[str, Any]:
        """Get inference service status by name."""
        import urllib.parse
        filters = json.dumps([{"col": "name", "opr": "eq", "value": service_name}])
        encoded_filters = urllib.parse.quote(filters)
        return await self._execute_request(
            action="get_service_status",
            method="GET",
            path=f"/inferenceservice_modelview/api/?_filters={encoded_filters}",
        )

    async def update_inference_service(self, service_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Update inference service by name (deploy/update)."""
        return await self._execute_request(
            action="update_inference_service",
            method="POST",
            path="/inferenceservice_modelview/api/deploy/update/",
            json_body={"service_name": service_name, **params},
        )

    # ---- pipeline ----
    async def create_pipeline(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_request(
            action="create_pipeline",
            method="POST",
            path="/pipeline_modelview/api/",
            json_body=params,
            recovery_action="delete_pipeline",
        )

    async def create_task(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_request(
            action="create_task",
            method="POST",
            path="/task_modelview/api/",
            json_body=params,
        )

    async def run_pipeline(self, pipeline_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="run_pipeline",
            method="GET",
            path=f"/pipeline_modelview/api/run_pipeline/{pipeline_id}",
        )

    async def get_pipeline_status(self, pipeline_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="get_pipeline_status",
            method="GET",
            path=f"/pipeline_modelview/api/web/workflow/{pipeline_id}",
        )

    async def list_pipelines(self) -> dict[str, Any]:
        return await self._execute_request(
            action="list_pipelines",
            method="GET",
            path="/pipeline_modelview/api/",
        )

    # ---- notebook ----
    async def create_notebook(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._execute_request(
            action="create_notebook",
            method="POST",
            path="/notebook_modelview/api/entry/jupyter",
            json_body=params,
            recovery_action="stop_notebook",
        )

    async def list_notebooks(self) -> dict[str, Any]:
        return await self._execute_request(
            action="list_notebooks",
            method="GET",
            path="/notebook_modelview/api/list/",
        )

    async def reset_notebook(self, notebook_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="reset_notebook",
            method="POST",
            path=f"/notebook_modelview/api/reset/{notebook_id}",
        )

    async def stop_notebook(self, notebook_id: int) -> dict[str, Any]:
        return await self._execute_request(
            action="stop_notebook",
            method="POST",
            path=f"/notebook_modelview/api/stop/{notebook_id}",
        )
