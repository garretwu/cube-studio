"""Purpose: read a local file and return full text content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sre_agent.tools.registry import ToolExecutionContext, ToolValidationError


def _require_str(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key, "")).strip()
    if not value:
        raise ToolValidationError(f"parameter {key!r} is required")
    return value


async def read(params: dict[str, Any], context: ToolExecutionContext) -> Any:
    _ = context
    raw_path = _require_str(params, "path")
    encoding = str(params.get("encoding", "utf-8") or "utf-8").strip() or "utf-8"

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    if not path.exists():
        raise ToolValidationError(f"file not found: {path}")
    if not path.is_file():
        raise ToolValidationError(f"path is not a file: {path}")

    try:
        content = path.read_text(encoding=encoding, errors="replace")
    except LookupError as exc:
        raise ToolValidationError(f"unsupported encoding: {encoding}") from exc
    except OSError as exc:
        raise ToolValidationError(f"failed to read file: {exc}") from exc

    return {
        "path": str(path),
        "encoding": encoding,
        "size_bytes": path.stat().st_size,
        "content": content,
    }

