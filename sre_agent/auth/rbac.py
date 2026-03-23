"""Role-based access control helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, status

from sre_agent.auth.jwt import get_current_user
from sre_agent.models.common import SafetyLevel


ROLE_TO_MAX_SAFETY = {
    "viewer": SafetyLevel.READONLY,
    "operator": SafetyLevel.HIGH,
    "admin": SafetyLevel.CRITICAL,
}

SAFETY_ORDER = {
    SafetyLevel.READONLY: 0,
    SafetyLevel.LOW: 1,
    SafetyLevel.MEDIUM: 2,
    SafetyLevel.HIGH: 3,
    SafetyLevel.CRITICAL: 4,
}


def role_allows_safety_level(role: str, safety_level: SafetyLevel | str) -> bool:
    level = safety_level if isinstance(safety_level, SafetyLevel) else SafetyLevel(safety_level)
    maximum = ROLE_TO_MAX_SAFETY.get(role, SafetyLevel.READONLY)
    return SAFETY_ORDER[level] <= SAFETY_ORDER[maximum]


def require_role(*allowed_roles: str) -> Callable[[Any], Any]:
    """FastAPI dependency factory for role checks."""

    allowed = {role.strip() for role in allowed_roles if role.strip()}

    async def _check(user: Any = Depends(get_current_user)) -> Any:
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        if allowed and getattr(user, "role", "") not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {getattr(user, 'role', 'unknown')!r} is not allowed",
            )
        return user

    return _check
