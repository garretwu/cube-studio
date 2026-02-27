"""Jupyter notebook channel."""
from __future__ import annotations

import json
import urllib.request
from typing import Any


class NotebookChannel:
    """Direct Jupyter API client for kernel lifecycle and code execution."""

    def __init__(self, *, base_url: str, token: str = "", timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers

    async def create_kernel(self, kernel_name: str = "python3") -> dict[str, Any]:
        return self._request_json("POST", "/api/kernels", {"name": kernel_name})

    async def execute_code(self, kernel_id: str, code: str) -> dict[str, Any]:
        return self._request_json("POST", f"/api/kernels/{kernel_id}/execute", {"code": code})

    async def delete_kernel(self, kernel_id: str) -> dict[str, Any]:
        return self._request_json("DELETE", f"/api/kernels/{kernel_id}", None)

    async def list_kernels(self) -> list[dict[str, Any]]:
        data = self._request_json("GET", "/api/kernels", None)
        return data if isinstance(data, list) else []

    def _request_json(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers=self._headers(),
            data=data,
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            text = resp.read().decode("utf-8")
            return json.loads(text or "{}")
