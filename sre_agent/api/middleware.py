"""Middleware wiring for trace IDs and JSON errors."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sre_agent.models.common import ErrorCode


def install_middlewares(app: FastAPI) -> None:
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        request.state.trace_id = request.headers.get("x-trace-id", uuid4().hex)
        response = await call_next(request)
        response.headers["x-trace-id"] = request.state.trace_id
        return response

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", uuid4().hex)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "data": None,
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": str(exc),
                    "details": None,
                    "trace_id": trace_id,
                },
                "trace_id": trace_id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
