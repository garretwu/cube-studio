#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def _check(url: str) -> None:
    request = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(request, timeout=3.0) as response:
        status = int(getattr(response, "status", 200) or 200)
        if status >= 400:
            raise RuntimeError(f"unexpected status={status} for {url}")


def main() -> int:
    backend_port = str(os.environ.get("BACKEND_PORT", "8000")).strip() or "8000"
    frontend_port = str(os.environ.get("FRONTEND_PORT", "8080")).strip() or "8080"
    backend_url = os.environ.get("BACKEND_HEALTHCHECK_URL", f"http://127.0.0.1:{backend_port}/openapi.json")
    frontend_url = os.environ.get("FRONTEND_HEALTHCHECK_URL", f"http://127.0.0.1:{frontend_port}/")

    try:
        _check(backend_url)
        _check(frontend_url)
    except (OSError, RuntimeError, urllib.error.URLError) as exc:
        print(f"unhealthy: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
