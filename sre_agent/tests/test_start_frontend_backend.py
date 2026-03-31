from __future__ import annotations

from pathlib import Path

from sre_agent.scripts.start_frontend_backend import (
    DEFAULT_CONFIG_PATH,
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
