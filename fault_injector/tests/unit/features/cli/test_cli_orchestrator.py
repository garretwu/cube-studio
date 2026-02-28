from __future__ import annotations

from click.testing import CliRunner

from fault_injector.cli import main
from fault_injector.orchestrator.session import Session


def _config_yaml() -> str:
    return """
global:
  session_dir: \"./fault-reports/sessions/\"
  safety:
    dry_run: true
inventory:
  nodes:
    - name: \"node-1\"
      ssh:
        host: \"127.0.0.1\"
        user: \"root\"
scenarios:
  network_jitter:
    enabled: true
    target_nodes: [\"node-1\"]
    params:
      duration: 0
      interface: \"eth0\"
      delay_ms: 5
"""


def test_cli_run_uses_orchestrator(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(_config_yaml(), encoding="utf-8")

    async def _fake_run(self):
        s = Session.create(config_hash="x", session_dir=str(tmp_path))
        s.complete()
        return s

    monkeypatch.setattr("fault_injector.cli.FaultOrchestrator.run", _fake_run)

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(cfg), "--dry-run", "--yes"])

    assert result.exit_code == 0
    assert "session_id" in result.output


def test_cli_resume(monkeypatch):
    async def _fake_resume(session_id: str, session_dir: str):
        s = Session.create(config_hash="x", session_dir=session_dir)
        s.mark_recovered()
        return s

    monkeypatch.setattr("fault_injector.cli.FaultOrchestrator.resume", _fake_resume)

    runner = CliRunner()
    result = runner.invoke(main, ["resume", "--session", "abc123"])

    assert result.exit_code == 0
    assert "Resume recovery complete" in result.output
