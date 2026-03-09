"""Jupyter notebook channel — async httpx + websockets."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import websockets

logger = logging.getLogger("load_simulator.channels.notebook")


class NotebookChannel:
    """Jupyter API client: REST for kernel lifecycle, WebSocket for execution.

    ``base_url`` may contain a path prefix (e.g.
    ``http://host/notebook/jupyter/name``).  httpx treats absolute request
    paths (starting with ``/``) as replacing the base_url path, so we
    ensure base_url ends with ``/`` and all request paths are **relative**.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str = "",
        username: str = "",
        timeout: int = 30,
    ) -> None:
        # Ensure trailing slash so relative paths resolve correctly:
        #   base="http://h/prefix/" + "api/kernels" → "http://h/prefix/api/kernels"
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.username = username
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
        )
        auth_mode = "token" if token else ("username" if username else "none")
        logger.info("NotebookChannel init  base_url=%s  auth=%s  timeout=%ds", self.base_url, auth_mode, timeout)

    # ── Auth helpers ──────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        elif self.username:
            headers["Authorization"] = self.username
            headers["Cookie"] = f"myapp_username={self.username}"
        return headers

    def _ws_url(self, kernel_id: str) -> str:
        """Build the WebSocket URL for a kernel's channels endpoint."""
        # http(s)://host/prefix/ → ws(s)://host/prefix/api/kernels/{id}/channels
        url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        url = url + f"api/kernels/{kernel_id}/channels"
        if self.token:
            url += f"?token={self.token}"
        return url

    def _ws_extra_headers(self) -> dict[str, str]:
        """Headers passed during the WebSocket handshake (cookie auth)."""
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        elif self.username:
            headers["Authorization"] = self.username
            headers["Cookie"] = f"myapp_username={self.username}"
        return headers

    # ── Kernel lifecycle (standard Jupyter REST API) ──────────────

    async def create_kernel(self, kernel_name: str = "python3") -> dict[str, Any]:
        """POST api/kernels — create a new kernel."""
        logger.debug("create_kernel  kernel_name=%s", kernel_name)
        resp = await self._client.post(
            "api/kernels",
            json={"name": kernel_name},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("create_kernel  status=%d  kernel_id=%s", resp.status_code, data.get("id"))
        return data

    async def delete_kernel(self, kernel_id: str) -> None:
        """DELETE api/kernels/{id} — shut down a kernel."""
        logger.debug("delete_kernel  kernel_id=%s", kernel_id)
        resp = await self._client.delete(
            f"api/kernels/{kernel_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        logger.info("delete_kernel  status=%d  kernel_id=%s", resp.status_code, kernel_id)

    async def list_kernels(self) -> list[dict[str, Any]]:
        """GET api/kernels — list active kernels."""
        resp = await self._client.get(
            "api/kernels",
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        kernels = data if isinstance(data, list) else []
        logger.info("list_kernels  status=%d  count=%d", resp.status_code, len(kernels))
        return kernels

    # ── Code execution (Jupyter WebSocket protocol) ───────────────

    async def execute_code(self, kernel_id: str, code: str) -> dict[str, Any]:
        """Execute *code* on a running kernel via WebSocket.

        Connects to ``ws(s)://…/api/kernels/{id}/channels``, sends an
        ``execute_request`` message following the Jupyter messaging protocol,
        and waits for the corresponding ``execute_reply``.
        """
        ws_url = self._ws_url(kernel_id)
        logger.debug("execute_code  kernel_id=%s  ws=%s  code_len=%d", kernel_id, ws_url, len(code))

        msg_id = uuid.uuid4().hex
        session_id = uuid.uuid4().hex

        execute_request = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "username": self.username or "",
                "session": session_id,
                "date": datetime.now(timezone.utc).isoformat(),
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "buffers": [],
            "channel": "shell",
        }

        async with websockets.connect(
            ws_url,
            additional_headers=self._ws_extra_headers(),
            open_timeout=self.timeout,
            close_timeout=5,
        ) as ws:
            await ws.send(json.dumps(execute_request))
            logger.debug("execute_request sent  msg_id=%s  kernel_id=%s", msg_id, kernel_id)

            # Wait for the execute_reply that matches our msg_id
            while True:
                raw = await ws.recv()
                resp_msg = json.loads(raw)
                msg_type = resp_msg.get("msg_type") or resp_msg.get("header", {}).get("msg_type", "")
                parent_msg_id = resp_msg.get("parent_header", {}).get("msg_id", "")

                if parent_msg_id == msg_id and msg_type == "execute_reply":
                    status = resp_msg.get("content", {}).get("status", "unknown")
                    logger.info(
                        "execute_reply  kernel_id=%s  status=%s  msg_id=%s",
                        kernel_id, status, msg_id,
                    )
                    return {
                        "status": status,
                        "content": resp_msg.get("content", {}),
                        "msg_id": msg_id,
                    }

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()
        logger.info("NotebookChannel closed")
