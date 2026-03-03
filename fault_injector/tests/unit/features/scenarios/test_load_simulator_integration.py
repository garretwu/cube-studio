from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from fault_injector.config.schema import SafetyConfig
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from fault_injector.scenarios.base import FaultContext, LoadSimulatorError, _run_load_simulator
from fault_injector.scenarios.vllm_latency import NetworkJitterScenario


class _FakeProc:
    def __init__(self, *, stdout: bytes, stderr: bytes, returncode: int, delay: float = 0.0):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._delay = delay
        self.killed = False

    async def communicate(self):
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_run_load_simulator_should_parse_json_payload(monkeypatch):
    payload = b'{"exit_code": 0, "summary": {"session_id": "abc"}}'

    async def _fake_create(*args, **kwargs):  # noqa: ANN002,ANN003
        _ = (args, kwargs)
        return _FakeProc(stdout=payload, stderr=b"", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    out = await _run_load_simulator("cfg.yaml", ["inference"], timeout_seconds=5)
    assert out["exit_code"] == 0
    assert out["summary"]["session_id"] == "abc"


@pytest.mark.asyncio
async def test_run_load_simulator_should_raise_on_timeout(monkeypatch):
    fake = _FakeProc(stdout=b"{}", stderr=b"", returncode=0, delay=0.2)

    async def _fake_create(*args, **kwargs):  # noqa: ANN002,ANN003
        _ = (args, kwargs)
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    with pytest.raises(LoadSimulatorError, match="timeout"):
        await _run_load_simulator("cfg.yaml", [], timeout_seconds=0)
    assert fake.killed is True


def _context(tmp_path: Path, strict: bool) -> FaultContext:
    rollback = RollbackJournal(tmp_path / "rollback.jsonl")
    guard = SafetyGuard(SafetyConfig(require_confirmation=False, dry_run=True))
    ssh = AsyncMock()
    ssh.run_command = AsyncMock(return_value=type("R", (), {"success": True, "output": "", "error": ""})())
    return FaultContext(
        ssh=ssh,
        rollback=rollback,
        guard=guard,
        target_node="node-1",
        params={
            "interface": "eth0",
            "delay_ms": 10,
            "duration": 1,
            "load_simulator": {
                "enabled": True,
                "config_path": "load_simulator/config/notebook-soak-only.yaml",
                "only": ["inference"],
                "timeout_seconds": 5,
                "strict": strict,
            },
        },
        fault_id="f1",
        session_id="s1",
    )


@pytest.mark.asyncio
async def test_network_jitter_should_fail_when_ls_fails_in_strict_mode(tmp_path, monkeypatch):
    async def _boom(*args, **kwargs):  # noqa: ANN002,ANN003
        _ = (args, kwargs)
        raise LoadSimulatorError("ls failed")

    monkeypatch.setattr("fault_injector.scenarios.base._run_load_simulator", _boom)
    scenario = NetworkJitterScenario()
    ctx = _context(tmp_path, strict=True)

    result = await scenario.inject(ctx)
    assert result.success is True
    post_err = await scenario.post_inject(ctx)
    assert "ls failed" in (post_err or "")


@pytest.mark.asyncio
async def test_network_jitter_should_continue_when_ls_fails_in_best_effort_mode(tmp_path, monkeypatch):
    async def _boom(*args, **kwargs):  # noqa: ANN002,ANN003
        _ = (args, kwargs)
        raise LoadSimulatorError("ls failed")

    monkeypatch.setattr("fault_injector.scenarios.base._run_load_simulator", _boom)
    scenario = NetworkJitterScenario()
    ctx = _context(tmp_path, strict=False)

    result = await scenario.inject(ctx)
    assert result.success is True
    post_err = await scenario.post_inject(ctx)
    assert post_err is None
    warnings = ctx.params.get("_load_simulator_warnings", [])
    assert any("ls failed" in str(w) for w in warnings)
