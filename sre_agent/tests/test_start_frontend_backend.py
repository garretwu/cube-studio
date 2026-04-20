from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import sre_agent.scripts.start_frontend_backend as launcher
from sre_agent.scripts.start_frontend_backend import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOCAL_LLM_BASE_URL,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_LLM_MODE,
    DEFAULT_KUBECONFIG_PATH,
    build_parser,
    build_runtime_env,
    wait_backend_ready,
)


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "auth:",
                "  jwt_secret_env: JWT_SECRET",
                "  jwt_algorithm: HS256",
                "  audience: sre-agent",
                "llm:",
                "  api_key: test-key-from-config",
                "  base_url: https://api.minimax.chat/v1",
                "  model: MiniMax-M2.7",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _write_glm_config(tmp_path: Path) -> Path:
    config = tmp_path / "config_glm.yaml"
    config.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "auth:",
                "  jwt_secret_env: JWT_SECRET",
                "  jwt_algorithm: HS256",
                "  audience: sre-agent",
                "llm:",
                "  provider: glm",
                "  api_key: glm-test-key-from-config",
                "  base_url: https://open.bigmodel.cn/api/coding/paas/v4",
                "  model: glm-5.1",
            ]
        ),
        encoding="utf-8",
    )
    return config


def test_build_runtime_env_proxy_mode_clears_api_base_url(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setenv("VITE_API_BASE_URL", "http://stale.example:18090")

    env, info = build_runtime_env(
        config_path=str(config_path),
        backend_host="127.0.0.1",
        backend_port=18090,
        frontend_port=5173,
        api_mode="proxy",
        role="operator",
        username="local-ui",
        token_expire_seconds=3600,
    )

    assert env["VITE_API_PROXY_TARGET"] == "http://127.0.0.1:18090"
    assert env["SRE_KUBECONFIG"] == DEFAULT_KUBECONFIG_PATH
    assert "VITE_API_BASE_URL" not in env
    assert info["api_mode"] == "proxy"
    assert info["api_base_url"] == ""


def test_build_runtime_env_direct_mode_sets_api_base_url(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    env, info = build_runtime_env(
        config_path=str(config_path),
        backend_host="127.0.0.1",
        backend_port=18090,
        frontend_port=5173,
        api_mode="direct",
        role="operator",
        username="local-ui",
        token_expire_seconds=3600,
    )

    assert env["VITE_API_PROXY_TARGET"] == "http://127.0.0.1:18090"
    assert env["SRE_KUBECONFIG"] == DEFAULT_KUBECONFIG_PATH
    assert env["VITE_API_BASE_URL"] == "http://127.0.0.1:18090"
    assert info["api_mode"] == "direct"
    assert info["api_base_url"] == "http://127.0.0.1:18090"


def test_build_runtime_env_config_always_overrides_existing_llm_env(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_glm_config(tmp_path)
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")
    monkeypatch.setenv("SRE_OPENAI_BASE_URL", "https://api.minimax.chat/v1")
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "minimax-env-key")
    monkeypatch.setenv("SRE_LLM_PROVIDER", "openai_compatible")

    env, info = build_runtime_env(
        config_path=str(config_path),
        backend_host="127.0.0.1",
        backend_port=18090,
        frontend_port=5173,
        api_mode="proxy",
        role="operator",
        username="local-ui",
        token_expire_seconds=3600,
    )

    assert env["SRE_LLM_MODEL"] == "glm-5.1"
    assert env["SRE_OPENAI_BASE_URL"] == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert env["SRE_OPENAI_API_KEY"] == "glm-test-key-from-config"
    assert env["SRE_LLM_PROVIDER"] == "glm"
    assert info["llm_model"] == "glm-5.1"
    assert info["llm_base_url"] == "https://open.bigmodel.cn/api/coding/paas/v4"


def test_parser_default_config_path() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.config == DEFAULT_CONFIG_PATH
    assert args.llm_mode == DEFAULT_LLM_MODE
    assert args.local_llm_base_url == DEFAULT_LOCAL_LLM_BASE_URL
    assert args.local_model == DEFAULT_LOCAL_LLM_MODEL


def test_build_runtime_env_accepts_legacy_llm_mode_alias(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_local_llm", lambda **kwargs: None)

    _env, info = build_runtime_env(
        config_path=str(config_path),
        backend_host="127.0.0.1",
        backend_port=18090,
        frontend_port=5173,
        api_mode="proxy",
        role="operator",
        username="local-ui",
        token_expire_seconds=3600,
        llm_mode="minimax_local",
        local_llm_base_url="http://10.11.4.13:18080/v1",
        local_model=DEFAULT_LOCAL_LLM_MODEL,
    )

    assert info["llm_mode"] == "openai_compatible_local"


def test_build_runtime_env_local_mode_overrides_llm_env_and_runs_probe(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    captured: dict[str, str] = {}

    def _fake_probe(*, base_url: str, model: str) -> None:
        captured["base_url"] = base_url
        captured["model"] = model

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_local_llm", _fake_probe)

    env, info = build_runtime_env(
        config_path=str(config_path),
        backend_host="127.0.0.1",
        backend_port=18090,
        frontend_port=5173,
        api_mode="proxy",
        role="operator",
        username="local-ui",
        token_expire_seconds=3600,
        llm_mode="openai_compatible_local",
        local_llm_base_url="http://10.11.4.13:18080/v1",
        local_model=DEFAULT_LOCAL_LLM_MODEL,
    )

    assert captured["base_url"] == "http://10.11.4.13:18080/v1"
    assert captured["model"] == DEFAULT_LOCAL_LLM_MODEL
    assert env["SRE_OPENAI_BASE_URL"] == "http://10.11.4.13:18080/v1"
    assert env["SRE_LLM_MODEL"] == DEFAULT_LOCAL_LLM_MODEL
    assert env["SRE_OPENAI_API_KEY"] == "local-llama-placeholder"
    assert info["llm_mode"] == "openai_compatible_local"
    assert info["llm_local_probe_passed"] == "true"
    assert info["llm_local_selected_model"] == DEFAULT_LOCAL_LLM_MODEL
    assert info["llm_local_base_url"] == "http://10.11.4.13:18080/v1"


def test_build_runtime_env_local_mode_probe_failure_blocks_startup(monkeypatch, tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)

    def _fake_probe(*, base_url: str, model: str) -> None:
        _ = (base_url, model)
        raise SystemExit("local llm probe failed: synthetic error")

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_local_llm", _fake_probe)

    with pytest.raises(SystemExit, match="local llm probe failed"):
        build_runtime_env(
            config_path=str(config_path),
            backend_host="127.0.0.1",
            backend_port=18090,
            frontend_port=5173,
            api_mode="proxy",
            role="operator",
            username="local-ui",
            token_expire_seconds=3600,
            llm_mode="openai_compatible_local",
            local_llm_base_url="http://10.11.4.13:18080/v1",
            local_model=DEFAULT_LOCAL_LLM_MODEL,
        )


class _FakeProc:
    def __init__(self, pid: int, poll_values: list[int | None]) -> None:
        self.pid = pid
        self._poll_values = list(poll_values)
        self._last = self._poll_values[-1] if self._poll_values else None

    def poll(self) -> int | None:
        if self._poll_values:
            self._last = self._poll_values.pop(0)
        return self._last


def _runtime_info_template() -> dict[str, str]:
    return {
        "backend_url": "http://127.0.0.1:8000",
        "frontend_url": "http://127.0.0.1:8080",
        "api_mode": "proxy",
        "proxy_target": "http://127.0.0.1:8000",
        "api_base_url": "",
        "llm_api_key_configured": "true",
        "llm_api_key_length": "8",
        "llm_model": "MiniMax-M2.7",
        "llm_base_url": "https://api.minimax.chat/v1",
        "llm_mode": "openai_compatible_api",
        "llm_local_probe_passed": "false",
        "llm_local_selected_model": "",
        "llm_local_base_url": "",
        "token_expire_seconds": "3600",
        "jwt_secret_source": "fixed",
        "config_path": str((Path(__file__).resolve().parents[2] / "sre_agent" / "conf" / "config.yaml")),
        "sre_kubeconfig": DEFAULT_KUBECONFIG_PATH,
    }


def test_wait_backend_ready_success_after_retries(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    backend = _FakeProc(pid=1001, poll_values=[None, None, None])
    probe_results = iter([False, False, True])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_backend_ready", lambda *args, **kwargs: next(probe_results))
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.time.sleep", lambda *_: None)

    ready = wait_backend_ready(
        backend=backend, readiness_url="http://127.0.0.1:8000/openapi.json", timeout_seconds=30, interval_seconds=0.1
    )

    output = capsys.readouterr().out
    assert ready is True
    assert "[wait] backend readiness probe" in output
    assert "[ok] backend ready, starting frontend" in output


def test_wait_backend_ready_fails_when_backend_exits(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    backend = _FakeProc(pid=1002, poll_values=[None, 7])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_backend_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.time.sleep", lambda *_: None)

    ready = wait_backend_ready(
        backend=backend, readiness_url="http://127.0.0.1:8000/openapi.json", timeout_seconds=30, interval_seconds=0.1
    )

    output = capsys.readouterr().out
    assert ready is False
    assert "[error] backend exited before ready" in output


def test_wait_backend_ready_times_out(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    backend = _FakeProc(pid=1003, poll_values=[None, None, None, None, None])
    monotonic_values = iter([0.0, 0.2, 0.6, 1.1])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._probe_backend_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.time.sleep", lambda *_: None)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.time.monotonic", lambda: next(monotonic_values))

    ready = wait_backend_ready(
        backend=backend, readiness_url="http://127.0.0.1:8000/openapi.json", timeout_seconds=1.0, interval_seconds=0.1
    )

    output = capsys.readouterr().out
    assert ready is False
    assert "[error] backend readiness timeout" in output


def test_main_waits_for_backend_then_starts_frontend(monkeypatch) -> None:
    call_order: list[str] = []

    monkeypatch.setattr(
        launcher,
        "args",
        argparse.Namespace(
            config=DEFAULT_CONFIG_PATH,
            backend_host="127.0.0.1",
            backend_port=8000,
            frontend_port=8080,
            api_mode="proxy",
            role="operator",
            username="local-ui",
            token_expire_seconds=3600,
            llm_mode="openai_compatible_api",
            local_llm_base_url=DEFAULT_LOCAL_LLM_BASE_URL,
            local_model=DEFAULT_LOCAL_LLM_MODEL,
            runtime_info="sre_agent/temp/dev_runtime_info.json",
        ),
        raising=False,
    )

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._next_free_port", lambda host, port, limit=30: port)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.ensure_frontend_dependencies", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "sre_agent.scripts.start_frontend_backend.build_runtime_env",
        lambda **kwargs: ({"PYTHONUNBUFFERED": "1"}, _runtime_info_template()),
    )
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._resolve_config_path", lambda _: Path(DEFAULT_CONFIG_PATH))

    backend_proc = _FakeProc(pid=1101, poll_values=[0])
    frontend_proc = _FakeProc(pid=1102, poll_values=[None])

    def _spawn_backend(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        call_order.append("spawn_backend")
        return backend_proc

    def _wait_backend_ready(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        call_order.append("wait_backend_ready")
        return True

    def _spawn_frontend(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        call_order.append("spawn_frontend")
        return frontend_proc

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.spawn_backend", _spawn_backend)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.wait_backend_ready", _wait_backend_ready)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.spawn_frontend", _spawn_frontend)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.write_runtime_info", lambda *args, **kwargs: None)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.terminate_process", lambda *args, **kwargs: None)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.time.sleep", lambda *_: None)

    exit_code = launcher.main()
    assert exit_code == 0
    assert call_order[:3] == ["spawn_backend", "wait_backend_ready", "spawn_frontend"]


def test_main_does_not_start_frontend_when_backend_not_ready(monkeypatch) -> None:
    frontend_started = False
    stopped: list[str] = []

    monkeypatch.setattr(
        launcher,
        "args",
        argparse.Namespace(
            config=DEFAULT_CONFIG_PATH,
            backend_host="127.0.0.1",
            backend_port=8000,
            frontend_port=8080,
            api_mode="proxy",
            role="operator",
            username="local-ui",
            token_expire_seconds=3600,
            llm_mode="openai_compatible_api",
            local_llm_base_url=DEFAULT_LOCAL_LLM_BASE_URL,
            local_model=DEFAULT_LOCAL_LLM_MODEL,
            runtime_info="sre_agent/temp/dev_runtime_info.json",
        ),
        raising=False,
    )

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._next_free_port", lambda host, port, limit=30: port)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.ensure_frontend_dependencies", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "sre_agent.scripts.start_frontend_backend.build_runtime_env",
        lambda **kwargs: ({"PYTHONUNBUFFERED": "1"}, _runtime_info_template()),
    )
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend._resolve_config_path", lambda _: Path(DEFAULT_CONFIG_PATH))

    backend_proc = _FakeProc(pid=1201, poll_values=[None])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.spawn_backend", lambda *args, **kwargs: backend_proc)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.wait_backend_ready", lambda *args, **kwargs: False)

    def _spawn_frontend(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        nonlocal frontend_started
        frontend_started = True
        return _FakeProc(pid=1202, poll_values=[None])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.spawn_frontend", _spawn_frontend)
    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.write_runtime_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "sre_agent.scripts.start_frontend_backend.terminate_process",
        lambda proc, name: stopped.append(name),
    )

    exit_code = launcher.main()
    assert exit_code != 0
    assert frontend_started is False
    assert "backend" in stopped


def test_main_fails_fast_when_frontend_dependencies_are_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "args",
        argparse.Namespace(
            config=DEFAULT_CONFIG_PATH,
            backend_host="127.0.0.1",
            backend_port=8000,
            frontend_port=8080,
            api_mode="proxy",
            role="operator",
            username="local-ui",
            token_expire_seconds=3600,
            llm_mode="openai_compatible_api",
            local_llm_base_url=DEFAULT_LOCAL_LLM_BASE_URL,
            local_model=DEFAULT_LOCAL_LLM_MODEL,
            runtime_info="sre_agent/temp/dev_runtime_info.json",
        ),
        raising=False,
    )

    spawn_backend_called = False

    monkeypatch.setattr(
        "sre_agent.scripts.start_frontend_backend.ensure_frontend_dependencies",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("frontend dependencies are missing")),
    )

    def _spawn_backend(*args, **kwargs):  # noqa: ANN002, ANN003
        _ = (args, kwargs)
        nonlocal spawn_backend_called
        spawn_backend_called = True
        return _FakeProc(pid=1301, poll_values=[None])

    monkeypatch.setattr("sre_agent.scripts.start_frontend_backend.spawn_backend", _spawn_backend)

    with pytest.raises(SystemExit, match="frontend dependencies are missing"):
        launcher.main()

    assert spawn_backend_called is False
