"""JWT helpers for REST and WebSocket auth."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.websockets import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict

from sre_agent.config import AuthConfig

security = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    role: Literal["viewer", "operator", "admin"] = "viewer"


@dataclass(frozen=True)
class JWTSettings:
    secret: str
    algorithm: str = "HS256"
    audience: str = "sre-agent"
    expire_seconds: int = 3600


def resolve_jwt_settings(config: AuthConfig | None = None) -> JWTSettings:
    auth = config or AuthConfig()
    secret = os.getenv(auth.jwt_secret_env)
    if not secret:
        raise RuntimeError(f"missing JWT secret env: {auth.jwt_secret_env}")
    return JWTSettings(
        secret=secret,
        algorithm=auth.jwt_algorithm,
        audience=auth.audience,
        expire_seconds=auth.token_expire_seconds,
    )


def encode_token(user: CurrentUser, settings: JWTSettings) -> str:
    payload = {
        "sub": user.user_id,
        "username": user.username,
        "role": user.role,
        "aud": settings.audience,
        "exp": datetime.now(UTC) + timedelta(seconds=settings.expire_seconds),
    }
    return jwt.encode(payload, settings.secret, algorithm=settings.algorithm)


def decode_token(token: str, settings: JWTSettings) -> CurrentUser:
    payload = jwt.decode(
        token,
        key=settings.secret,
        algorithms=[settings.algorithm],
        audience=settings.audience,
    )
    return CurrentUser(
        user_id=payload["sub"],
        username=payload["username"],
        role=payload.get("role", "viewer"),
    )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    settings = getattr(request.app.state, "jwt_settings", None)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="jwt settings not configured",
        )
    try:
        return decode_token(credentials.credentials, settings)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


async def ws_authenticate(websocket: WebSocket) -> CurrentUser:
    token = _extract_ws_token(websocket)
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        raise WebSocketDisconnect(code=4001)
    settings = getattr(websocket.app.state, "jwt_settings", None)
    if settings is None:
        await websocket.close(code=1011, reason="JWT settings not configured")
        raise WebSocketDisconnect(code=1011)
    try:
        return decode_token(token, settings)
    except jwt.PyJWTError as exc:
        await websocket.close(code=4001, reason=str(exc))
        raise WebSocketDisconnect(code=4001) from exc


def _extract_ws_token(websocket: WebSocket) -> str:
    token = str(websocket.query_params.get("token", "")).strip()
    if token:
        return token

    auth_header = str(websocket.headers.get("authorization", "")).strip()
    if auth_header.lower().startswith("bearer "):
        candidate = auth_header[7:].strip()
        if candidate:
            return candidate

    protocol_header = str(websocket.headers.get("sec-websocket-protocol", "")).strip()
    if protocol_header:
        for item in protocol_header.split(","):
            candidate = item.strip()
            if candidate.lower().startswith("bearer "):
                candidate = candidate[7:].strip()
            if candidate and candidate.lower() not in {"bearer", "token"}:
                return candidate

    return ""
