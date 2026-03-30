"""Middleware wiring for trace IDs and JSON errors."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sre_agent.models.common import ErrorCode


def install_middlewares(app: FastAPI) -> None:
    config = getattr(getattr(app.state, "config", None), "global_", None)
    allowed_origins = list(getattr(config, "cors_allowed_origins", []) or [])
    allow_methods = list(getattr(config, "cors_allow_methods", []) or ["GET", "POST", "OPTIONS"])
    allow_headers = list(getattr(config, "cors_allow_headers", []) or ["Authorization", "Content-Type", "x-trace-id"])
    expose_headers = list(getattr(config, "cors_expose_headers", []) or ["x-trace-id"])
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=False,
            allow_methods=allow_methods,
            allow_headers=allow_headers,
            expose_headers=expose_headers,
        )

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
