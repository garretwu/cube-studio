from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import pytest

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_DEFAULT_REAL_CONFIG = Path(__file__).resolve().parent / "real_test.local.json"
_LOADED = False


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _to_env_str(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, str)):
        return str(value)
    return ""


def _extract_sre_values(data: Any, out: dict[str, str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.startswith("SRE_"):
                text = _to_env_str(value).strip()
                if text:
                    out[key] = text
                continue
            _extract_sre_values(value, out)
        return
    if isinstance(data, list):
        for item in data:
            _extract_sre_values(item, out)


def _load_real_config_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    out: dict[str, str] = {}
    _extract_sre_values(payload, out)
    return out


def _resolve_config_path() -> Path:
    raw = os.getenv("SRE_REAL_CONFIG_FILE", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        return (Path.cwd() / candidate).resolve()
    return _DEFAULT_REAL_CONFIG


def load_real_config_once() -> None:
    global _LOADED
    if _LOADED:
        return
    config_path = _resolve_config_path()
    try:
        file_values = _load_real_config_values(config_path)
    except Exception as exc:
        raise RuntimeError(f"failed to load real test config {config_path}: {exc}") from exc
    for key, value in file_values.items():
        os.environ.setdefault(key, value)
    _LOADED = True


def require_real_tests() -> None:
    load_real_config_once()
    if not _env_bool("SRE_REAL_TEST", default=False):
        pytest.skip("real tests disabled; set SRE_REAL_TEST=1")


def require_env(*names: str) -> dict[str, str]:
    load_real_config_once()
    missing = [name for name in names if not (os.getenv(name) or "").strip()]
    if missing:
        joined = ", ".join(missing)
        pytest.skip(
            "missing required env vars for real tests: "
            f"{joined}. Set env vars or populate lib/tests/real_test.local.json",
        )
    return {name: str(os.getenv(name, "")).strip() for name in names}


def get_http_timeout_sec() -> float:
    load_real_config_once()
    return _env_float("SRE_HTTP_TIMEOUT_SEC", 15.0)


def get_http_retry_count() -> int:
    load_real_config_once()
    return max(1, _env_int("SRE_HTTP_RETRY_COUNT", 2))


def allow_write_ops() -> bool:
    load_real_config_once()
    return _env_bool("SRE_ALLOW_WRITE_OPS", default=False)


def build_auth_headers(prefix: str) -> dict[str, str]:
    load_real_config_once()
    normalized = prefix.strip().upper()
    if not normalized:
        return {}

    headers: dict[str, str] = {}
    explicit = os.getenv(f"SRE_{normalized}_AUTH_HEADER", "").strip()
    if explicit and ":" in explicit:
        name, value = explicit.split(":", 1)
        if name.strip() and value.strip():
            headers[name.strip()] = value.strip()

    token = os.getenv(f"SRE_{normalized}_TOKEN", "").strip()
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"

    username = os.getenv(f"SRE_{normalized}_USERNAME", "").strip()
    password = os.getenv(f"SRE_{normalized}_PASSWORD", "").strip()
    if username and password and "Authorization" not in headers:
        basic = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {basic}"

    tenant = os.getenv(f"SRE_{normalized}_TENANT_ID", "").strip()
    if tenant:
        headers["X-Scope-OrgID"] = tenant
    return headers
