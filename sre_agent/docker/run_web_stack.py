#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path("/app")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sre_agent.scripts.start_frontend_backend import (  # noqa: E402
    _resolve_config_path,
    build_runtime_env,
    ensure_frontend_dependencies,
    terminate_process,
    wait_backend_ready,
)


def _spawn_backend(config_path: str, host: str, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "sre_agent",
        "serve",
        "--config",
        config_path,
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "info",
    ]
    return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env)


def _spawn_frontend(frontend_port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    frontend_dir = REPO_ROOT / "sre_agent" / "frontend"
    npm_path = str(Path("/usr/local/bin/npm"))
    cmd = [npm_path, "run", "dev", "--", "--host", "0.0.0.0", "--port", str(frontend_port)]
    return subprocess.Popen(cmd, cwd=str(frontend_dir), env=env)


def main() -> int:
    config_path = str(os.environ.get("STARTUP_CONFIG_PATH", "/app/sre_agent/conf/config.yaml")).strip()
    backend_bind_host = str(os.environ.get("STARTUP_BACKEND_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    backend_url_host = str(os.environ.get("STARTUP_BACKEND_URL_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    backend_port = int(str(os.environ.get("BACKEND_PORT", "8000")).strip() or "8000")
    frontend_port = int(str(os.environ.get("FRONTEND_PORT", "8080")).strip() or "8080")
    api_mode = str(os.environ.get("STARTUP_API_MODE", "proxy")).strip() or "proxy"

    frontend_dir = REPO_ROOT / "sre_agent" / "frontend"
    ensure_frontend_dependencies(frontend_dir)

    env, _info = build_runtime_env(
        config_path=config_path,
        backend_host=backend_url_host,
        backend_port=backend_port,
        frontend_port=frontend_port,
        api_mode=api_mode,
        role="operator",
        username="container-ui",
        token_expire_seconds=8 * 3600,
        llm_mode="openai_compatible_api",
    )

    resolved_config_path = str(_resolve_config_path(config_path))
    backend = _spawn_backend(resolved_config_path, backend_bind_host, backend_port, env)
    frontend: subprocess.Popen[bytes] | None = None

    def _shutdown(_signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
        terminate_process(frontend, "frontend")
        terminate_process(backend, "backend")
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    readiness_url = f"http://{backend_url_host}:{backend_port}/openapi.json"
    if not wait_backend_ready(backend=backend, readiness_url=readiness_url):
        terminate_process(backend, "backend")
        exit_code = backend.poll()
        return int(exit_code) if exit_code is not None else 1

    frontend = _spawn_frontend(frontend_port, env)

    try:
        while True:
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                terminate_process(frontend, "frontend")
                return int(backend_code)
            if frontend_code is not None:
                terminate_process(backend, "backend")
                return int(frontend_code)
            time.sleep(1.0)
    except KeyboardInterrupt:
        terminate_process(frontend, "frontend")
        terminate_process(backend, "backend")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
