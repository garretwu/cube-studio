"""Custom guardrails actions used by the optional NeMo layer."""

from __future__ import annotations

import re
from importlib import import_module
from typing import Any

try:
    from fault_injector.safety.guard import SafetyGuard

    ALLOWED_LOG_PATHS = set(SafetyGuard.ALLOWED_LOG_PATHS)
except Exception:  # pragma: no cover - fallback only
    ALLOWED_LOG_PATHS = {
        "/var/log/syslog",
        "/var/log/messages",
        "/var/log/kern.log",
        "/var/log/dmesg",
        "/var/log/auth.log",
        "/var/log/gpu-manager.log",
    }

SHELL_META_CHARS = (";", "|", "&", "`", "$(")
SENSITIVE_PATTERNS = [
    r"password\s*[:=]\s*\S+",
    r"api[_-]?key\s*[:=]\s*\S+",
    r"token\s*[:=]\s*\S+",
    r"secret\s*[:=]\s*\S+",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
]


def _load_action_decorator() -> Any:
    try:
        module = import_module("nemoguardrails.actions")
        return getattr(module, "action")
    except Exception:
        return _noop_action


def _noop_action(*args: Any, **kwargs: Any) -> Any:
    """Fallback decorator when nemoguardrails is unavailable."""

    def decorator(func: Any) -> Any:
        return func

    if args and callable(args[0]) and len(args) == 1 and not kwargs:
        return args[0]
    return decorator


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items: list[str] = []
        for nested in value.values():
            items.extend(_iter_strings(nested))
        return items
    if isinstance(value, (list, tuple, set)):
        items = []
        for nested in value:
            items.extend(_iter_strings(nested))
        return items
    return []


action = _load_action_decorator()


@action()
async def validate_tool_input(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """Validate tool input parameters before a tool call proceeds."""
    _ = tool_name
    payload = tool_input if isinstance(tool_input, dict) else {}
    log_path = payload.get("log_path")
    if isinstance(log_path, str) and log_path not in ALLOWED_LOG_PATHS:
        return False

    for text in _iter_strings(payload):
        if any(char in text for char in SHELL_META_CHARS):
            return False
    return True


@action()
async def sanitize_tool_output(tool_output: str) -> str:
    """Redact sensitive material from tool outputs before returning it."""
    sanitized = str(tool_output)
    for pattern in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    return sanitized


__all__ = [
    "ALLOWED_LOG_PATHS",
    "SENSITIVE_PATTERNS",
    "SHELL_META_CHARS",
    "sanitize_tool_output",
    "validate_tool_input",
]
