#!/usr/bin/env python3
"""One-click launcher for SRE backend + frontend local manual testing.

Features:
- Auto-generate JWT_SECRET (ephemeral for this run).
- Auto-generate frontend bearer token (VITE_API_TOKEN).
- Start backend (`python -m sre_agent serve ...`) and frontend (`npm run dev`) together.
- No manual env export needed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt

# Ensure package imports work when launched as:
#   python sre_agent/scripts/start_frontend_backend.py
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sre_agent.config import apply_llm_env_from_config, load_config

DEFAULT_CONFIG_PATH = "sre_agent/conf/config.yaml"
DEFAULT_KUBECONFIG_PATH = str((REPO_ROOT / "sre_agent" / "conf" / "kube.conf").resolve())
DEFAULT_LOCAL_LLM_BASE_URL = "http://10.11.4.13:18080/v1"
DEFAULT_LOCAL_LLM_MODEL = "MiniMax-M2.5-IQ4_XS-00001-of-00004.gguf"
LOCAL_LLM_PLACEHOLDER_KEY = "local-llama-placeholder"
BACKEND_READINESS_PATH = "/openapi.json"
BACKEND_READINESS_TIMEOUT_SECONDS = 180.0
BACKEND_READINESS_INTERVAL_SECONDS = 1.0
BACKEND_READINESS_PROBE_TIMEOUT_SECONDS = 2.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start SRE backend and frontend with auto JWT/token setup.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Backend config file path.")
    parser.add_argument("--backend-host", default="127.0.0.1", help="Backend bind host.")
    parser.add_argument("--backend-port", type=int, default=8000, help="Backend bind port.")
    parser.add_argument("--frontend-port", type=int, default=8080, help="Frontend dev server port.")
    parser.add_argument(
        "--api-mode",
        choices=["proxy", "direct"],
        default="proxy",
        help="proxy: frontend uses /api + Vite proxy; direct: frontend uses VITE_API_BASE_URL.",
    )
    parser.add_argument("--role", choices=["viewer", "operator", "admin"], default="operator", help="JWT role for frontend requests.")
    parser.add_argument("--username", default="local-ui", help="JWT username.")
    parser.add_argument("--token-expire-seconds", type=int, default=8 * 3600, help="Frontend token expire seconds.")
    parser.add_argument(
        "--llm-mode",
        choices=["minimax_api", "minimax_local"],
        default="minimax_api",
        help="minimax_api: keep current cloud MiniMax config; minimax_local: use local llama.cpp gateway.",
    )
    parser.add_argument(
        "--local-llm-base-url",
        default=DEFAULT_LOCAL_LLM_BASE_URL,
        help="Local OpenAI-compatible base URL used when --llm-mode=minimax_local.",
    )
    parser.add_argument(
        "--local-model",
        default=DEFAULT_LOCAL_LLM_MODEL,
        help="Local model name used when --llm-mode=minimax_local.",
    )
    parser.add_argument(
        "--runtime-info",
        default="sre_agent/temp/dev_runtime_info.json",
        help="Where to save generated runtime info (token/urls/pids).",
    )
    return parser


def require_command(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"missing required command: {name}")


def resolve_npm_command() -> str:
    for candidate in ("npm.cmd", "npm"):
        found = shutil.which(candidate)
        if found:
            return found
    raise SystemExit("missing required command: npm (or npm.cmd)")


def _http_json_request(
    *,
    method: str,
    url: str,
    payload: dict[str, object] | None = None,
    timeout_seconds: float = 8.0,
) -> tuple[int, dict[str, object]]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, float(timeout_seconds))) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        snippet = body[:240]
        raise SystemExit(f"local llm probe failed: {method} {url} returned status={exc.code}, body={snippet}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"local llm probe failed: {method} {url} request error: {exc}") from exc

    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001
        parsed = {"raw": raw}
    if not isinstance(parsed, dict):
        parsed = {"data": parsed}
    return status_code, parsed


def _probe_local_llm(*, base_url: str, model: str) -> None:
    normalized_base = base_url.rstrip("/")
    model_name = model.strip()
    if not normalized_base:
        raise SystemExit("local llm probe failed: base_url is empty")
    if not model_name:
        raise SystemExit("local llm probe failed: model is empty")

    models_url = f"{normalized_base}/models"
    _http_json_request(method="GET", url=models_url, timeout_seconds=8.0)

    completion_url = f"{normalized_base}/chat/completions"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Reply with OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    _, completion = _http_json_request(method="POST", url=completion_url, payload=payload, timeout_seconds=30.0)
    choices = completion.get("choices")
    if not isinstance(choices, list):
        raise SystemExit(f"local llm probe failed: POST {completion_url} missing choices field in response")


def build_runtime_env(
    *,
    config_path: str,
    backend_host: str,
    backend_port: int,
    frontend_port: int,
    api_mode: str,
    role: str,
    username: str,
    token_expire_seconds: int,
    llm_mode: str = "minimax_api",
    local_llm_base_url: str = DEFAULT_LOCAL_LLM_BASE_URL,
    local_model: str = DEFAULT_LOCAL_LLM_MODEL,
) -> tuple[dict[str, str], dict[str, str]]:
    resolved_config_path = _resolve_config_path(config_path)
    config = load_config(resolved_config_path)
    auth = _load_auth_settings(config)
    jwt_secret = secrets.token_urlsafe(48)
    token = _encode_token(
        secret=jwt_secret,
        algorithm=auth["jwt_algorithm"],
        audience=auth["audience"],
        role=role,
        username=username,
        expire_seconds=token_expire_seconds,
    )

    backend_url = f"http://{backend_host}:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"

    env = dict(os.environ)
    apply_llm_env_from_config(config, env, only_if_missing=True)
    resolved_llm_mode = str(llm_mode or "minimax_api").strip().lower()
    if resolved_llm_mode not in {"minimax_api", "minimax_local"}:
        raise SystemExit(f"invalid --llm-mode: {llm_mode}")

    llm_local_probe_passed = "false"
    llm_local_selected_model = ""
    llm_local_base_url = ""
    if resolved_llm_mode == "minimax_local":
        llm_local_selected_model = str(local_model or DEFAULT_LOCAL_LLM_MODEL).strip() or DEFAULT_LOCAL_LLM_MODEL
        llm_local_base_url = str(local_llm_base_url or DEFAULT_LOCAL_LLM_BASE_URL).strip().rstrip("/")
        _probe_local_llm(base_url=llm_local_base_url, model=llm_local_selected_model)
        llm_local_probe_passed = "true"
        env["SRE_OPENAI_BASE_URL"] = llm_local_base_url
        env["SRE_LLM_MODEL"] = llm_local_selected_model
        env["SRE_OPENAI_API_KEY"] = LOCAL_LLM_PLACEHOLDER_KEY

    llm_api_key = str(env.get("SRE_OPENAI_API_KEY") or env.get("OPENAI_API_KEY") or "").strip()
    if resolved_llm_mode == "minimax_api" and not llm_api_key:
        raise SystemExit("missing required LLM API key: set SRE_OPENAI_API_KEY (or OPENAI_API_KEY) before starting backend")
    env[auth["jwt_secret_env"]] = jwt_secret
    env["VITE_API_TOKEN"] = token
    env["VITE_API_PROXY_TARGET"] = backend_url
    env["VITE_USE_MSW"] = "false"
    env["VITE_WS_ENABLED"] = "true"
    env["SRE_KUBECONFIG"] = DEFAULT_KUBECONFIG_PATH
    if api_mode == "proxy":
        env.pop("VITE_API_BASE_URL", None)
    else:
        env["VITE_API_BASE_URL"] = backend_url
    env.setdefault("PYTHONUNBUFFERED", "1")

    info = {
        "jwt_secret_env": auth["jwt_secret_env"],
        "jwt_secret": jwt_secret,
        "token_audience": auth["audience"],
        "token_role": role,
        "token_username": username,
        "token_expire_seconds": str(token_expire_seconds),
        "frontend_bearer_token": token,
        "backend_url": backend_url,
        "frontend_url": frontend_url,
        "api_mode": api_mode,
        "proxy_target": backend_url,
        "api_base_url": env.get("VITE_API_BASE_URL", ""),
        "sre_kubeconfig": env["SRE_KUBECONFIG"],
        "config_path": str(resolved_config_path),
        "llm_mode": resolved_llm_mode,
        "llm_local_probe_passed": llm_local_probe_passed,
        "llm_local_selected_model": llm_local_selected_model,
        "llm_local_base_url": llm_local_base_url,
        "llm_api_key_configured": "true" if llm_api_key else "false",
        "llm_api_key_length": str(len(llm_api_key)),
        "llm_model": str(env.get("SRE_LLM_MODEL", "")).strip(),
        "llm_base_url": str(env.get("SRE_OPENAI_BASE_URL", "")).strip(),
    }
    return env, info


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path).expanduser()
    if path.is_absolute():
        return path
    return (REPO_ROOT / path).resolve()


def _load_auth_settings(config) -> dict[str, str]:
    auth = getattr(config, "auth", None)
    if auth is None:
        return {
            "jwt_secret_env": "JWT_SECRET",
            "jwt_algorithm": "HS256",
            "audience": "sre-agent",
        }
    return {
        "jwt_secret_env": str(getattr(auth, "jwt_secret_env", "") or "JWT_SECRET").strip(),
        "jwt_algorithm": str(getattr(auth, "jwt_algorithm", "") or "HS256").strip(),
        "audience": str(getattr(auth, "audience", "") or "sre-agent").strip(),
    }


def _encode_token(
    *,
    secret: str,
    algorithm: str,
    audience: str,
    role: str,
    username: str,
    expire_seconds: int,
) -> str:
    payload = {
        "sub": "local-ui",
        "username": username,
        "role": role,
        "aud": audience,
        "exp": datetime.now(UTC) + timedelta(seconds=expire_seconds),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def _is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _next_free_port(host: str, preferred: int, limit: int = 30) -> int:
    for offset in range(limit + 1):
        candidate = preferred + offset
        if _is_port_free(host, candidate):
            return candidate
    raise SystemExit(f"no free port found near {preferred} on host {host}")


def write_runtime_info(path_text: str, payload: dict[str, str]) -> None:
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def spawn_backend(config_path: str, backend_host: str, backend_port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "sre_agent",
        "serve",
        "--config",
        config_path,
        "--host",
        backend_host,
        "--port",
        str(backend_port),
        "--log-level",
        "info",
    ]
    return subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env)


def spawn_frontend(frontend_port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    frontend_dir = REPO_ROOT / "sre_agent" / "frontend"
    cmd = [resolve_npm_command(), "run", "dev", "--", "--host", "0.0.0.0", "--port", str(frontend_port)]
    return subprocess.Popen(cmd, cwd=str(frontend_dir), env=env)


def terminate_process(proc: subprocess.Popen[bytes] | None, name: str) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        return
    print(f"[stop] {name} pid={proc.pid}")
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _probe_backend_ready(readiness_url: str, *, timeout_seconds: float = BACKEND_READINESS_PROBE_TIMEOUT_SECONDS) -> bool:
    request = urllib.request.Request(url=readiness_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.5, float(timeout_seconds))) as response:
            status_code = int(getattr(response, "status", 200) or 200)
            return status_code == 200
    except urllib.error.HTTPError:
        return False
    except urllib.error.URLError:
        return False
    except TimeoutError:
        return False
    except OSError:
        return False


def wait_backend_ready(
    *,
    backend: subprocess.Popen[bytes],
    readiness_url: str,
    timeout_seconds: float = BACKEND_READINESS_TIMEOUT_SECONDS,
    interval_seconds: float = BACKEND_READINESS_INTERVAL_SECONDS,
    probe_timeout_seconds: float = BACKEND_READINESS_PROBE_TIMEOUT_SECONDS,
) -> bool:
    timeout = max(0.0, float(timeout_seconds))
    interval = max(0.1, float(interval_seconds))
    deadline = time.monotonic() + timeout
    print(f"[wait] backend readiness probe url={readiness_url} timeout={timeout:.1f}s", flush=True)
    while True:
        backend_exit_code = backend.poll()
        if backend_exit_code is not None:
            print(f"[error] backend exited before ready code={backend_exit_code} url={readiness_url}", flush=True)
            return False
        if _probe_backend_ready(readiness_url, timeout_seconds=probe_timeout_seconds):
            print(f"[ok] backend ready, starting frontend url={readiness_url}", flush=True)
            return True
        now = time.monotonic()
        if now >= deadline:
            waited = max(0.0, timeout)
            print(f"[error] backend readiness timeout url={readiness_url} waited={waited:.1f}s", flush=True)
            return False
        time.sleep(interval)


def main() -> int:
    backend_port = _next_free_port(args.backend_host, args.backend_port)
    frontend_port = _next_free_port("0.0.0.0", args.frontend_port)
    if backend_port != args.backend_port:
        print(f"[warn] backend port {args.backend_port} is busy, switched to {backend_port}", flush=True)
    if frontend_port != args.frontend_port:
        print(f"[warn] frontend port {args.frontend_port} is busy, switched to {frontend_port}", flush=True)

    env, info = build_runtime_env(
        config_path=args.config,
        backend_host=args.backend_host,
        backend_port=backend_port,
        frontend_port=frontend_port,
        api_mode=args.api_mode,
        role=args.role,
        username=args.username,
        token_expire_seconds=args.token_expire_seconds,
        llm_mode=args.llm_mode,
        local_llm_base_url=args.local_llm_base_url,
        local_model=args.local_model,
    )

    config_path = str(_resolve_config_path(args.config))
    backend = spawn_backend(config_path, args.backend_host, backend_port, env)
    readiness_url = f"http://{args.backend_host}:{backend_port}{BACKEND_READINESS_PATH}"
    try:
        if not wait_backend_ready(backend=backend, readiness_url=readiness_url):
            terminate_process(backend, "backend")
            exit_code = backend.poll()
            return int(exit_code) if exit_code is not None else 1
        frontend = spawn_frontend(frontend_port, env)
    except KeyboardInterrupt:
        print("\n[stop] received Ctrl+C", flush=True)
        terminate_process(backend, "backend")
        return 0
    except Exception:  # noqa: BLE001
        terminate_process(backend, "backend")
        raise

    info["backend_pid"] = str(backend.pid)
    info["frontend_pid"] = str(frontend.pid)
    write_runtime_info(args.runtime_info, info)

    print(f"[ok] backend pid={backend.pid} url={info['backend_url']}", flush=True)
    print(f"[ok] frontend pid={frontend.pid} url={info['frontend_url']}", flush=True)
    print(
        f"[ok] api mode={info['api_mode']} "
        f"VITE_API_PROXY_TARGET={info['proxy_target']} "
        f"VITE_API_BASE_URL={info['api_base_url'] or '<unset>'}",
        flush=True,
    )
    print(
        f"[ok] llm key configured={info['llm_api_key_configured']} "
        f"key_length={info['llm_api_key_length']} "
        f"model={info['llm_model'] or '<default>'} "
        f"base_url={info['llm_base_url'] or '<default>'} "
        f"mode={info['llm_mode']}",
        flush=True,
    )
    if info["llm_mode"] == "minimax_local":
        print(
            f"[ok] local llm probe passed={info['llm_local_probe_passed']} "
            f"selected_model={info['llm_local_selected_model']} "
            f"local_base_url={info['llm_local_base_url']}",
            flush=True,
        )
    print(f"[ok] config={info['config_path']} SRE_KUBECONFIG={info['sre_kubeconfig']}", flush=True)
    print(f"[ok] runtime info saved: {args.runtime_info}", flush=True)
    print("[hint] press Ctrl+C to stop both processes", flush=True)

    try:
        while True:
            b = backend.poll()
            f = frontend.poll()
            if b is not None:
                print(f"[exit] backend exited with code {b}", flush=True)
                terminate_process(frontend, "frontend")
                return b
            if f is not None:
                print(f"[exit] frontend exited with code {f}", flush=True)
                terminate_process(backend, "backend")
                return f
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[stop] received Ctrl+C", flush=True)
        terminate_process(frontend, "frontend")
        terminate_process(backend, "backend")
        return 0


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(main())
