from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import sre_agent.cli as cli_module
from sre_agent.cli import main


def test_serve_cli_starts_uvicorn_with_expected_arguments(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: test-aidc",
                "ontology:",
                f"  db_path: {tmp_path / 'ontology.db'}",
                "memory:",
                f"  db_dir: {tmp_path / 'memory'}",
            ]
        ),
        encoding="utf-8",
    )

    fake_app = object()
    calls: dict[str, object] = {}

    def _fake_create_app(*, config):  # noqa: ANN001
        calls["aidc_id"] = config.global_.aidc_id
        return fake_app

    def _fake_uvicorn_run(app, host, port, log_level):  # noqa: ANN001
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port
        calls["log_level"] = log_level

    monkeypatch.setattr(cli_module, "create_app", _fake_create_app)
    monkeypatch.setattr(cli_module.uvicorn, "run", _fake_uvicorn_run)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["serve", "--config", str(config_path), "--host", "0.0.0.0", "--port", "18080", "--log-level", "debug"],
    )

    assert result.exit_code == 0
    assert "Starting sre-agent serve on 0.0.0.0:18080" in result.output
    assert calls["aidc_id"] == "test-aidc"
    assert calls["app"] is fake_app
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 18080
    assert calls["log_level"] == "debug"
