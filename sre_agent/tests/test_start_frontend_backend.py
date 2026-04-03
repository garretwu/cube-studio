from __future__ import annotations

from pathlib import Path

import pytest

from sre_agent.scripts.start_frontend_backend import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOCAL_LLM_BASE_URL,
    DEFAULT_LOCAL_LLM_MODEL,
    DEFAULT_KUBECONFIG_PATH,
    build_parser,
    build_runtime_env,
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


def test_parser_default_config_path() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.config == DEFAULT_CONFIG_PATH
    assert args.llm_mode == "minimax_api"
    assert args.local_llm_base_url == DEFAULT_LOCAL_LLM_BASE_URL
    assert args.local_model == DEFAULT_LOCAL_LLM_MODEL


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
        llm_mode="minimax_local",
        local_llm_base_url="http://10.11.4.13:18080/v1",
        local_model=DEFAULT_LOCAL_LLM_MODEL,
    )

    assert captured["base_url"] == "http://10.11.4.13:18080/v1"
    assert captured["model"] == DEFAULT_LOCAL_LLM_MODEL
    assert env["SRE_OPENAI_BASE_URL"] == "http://10.11.4.13:18080/v1"
    assert env["SRE_LLM_MODEL"] == DEFAULT_LOCAL_LLM_MODEL
    assert env["SRE_OPENAI_API_KEY"] == "local-llama-placeholder"
    assert info["llm_mode"] == "minimax_local"
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
            llm_mode="minimax_local",
            local_llm_base_url="http://10.11.4.13:18080/v1",
            local_model=DEFAULT_LOCAL_LLM_MODEL,
        )
