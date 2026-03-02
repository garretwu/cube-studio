"""
Base scenario contracts and shared helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fault_injector.config.schema import IPMIConfig, InjectResult, RedfishConfig, RecoverResult
from fault_injector.safety.guard import SafetyGuard
from fault_injector.safety.rollback import RollbackJournal
from lib.channels.ssh import SSHChannel

if TYPE_CHECKING:
    from lib.channels.ipmi import IPMIChannel
    from lib.channels.kubernetes import K8sChannel
    from lib.channels.prometheus import PrometheusChannel
    from lib.channels.redfish import RedfishChannel
    from lib.channels.switch import SwitchChannel

logger = logging.getLogger(__name__)


class LoadSimulatorError(RuntimeError):
    """Raised when load_simulator execution fails."""


@dataclass
class _LoadSimulatorRuntimeConfig:
    enabled: bool = False
    config_path: str = ""
    only: list[str] = None
    timeout_seconds: int = 900
    strict: bool = True

    def __post_init__(self) -> None:
        if self.only is None:
            self.only = []


@dataclass
class FaultContext:
    """
    Scenario execution context.
    """

    ssh: SSHChannel
    rollback: RollbackJournal
    guard: SafetyGuard
    target_node: str
    params: dict[str, Any]
    fault_id: str
    redfish: "RedfishChannel | None" = None
    ipmi: "IPMIChannel | None" = None
    target_redfish: RedfishConfig | None = None
    target_ipmi: IPMIConfig | None = None
    switch: "SwitchChannel | None" = None
    k8s: "K8sChannel | None" = None
    prometheus: "PrometheusChannel | None" = None
    session_id: str = ""
    interface: str = "eth0"


def _extract_load_simulator_config(params: dict[str, Any]) -> _LoadSimulatorRuntimeConfig:
    raw = params.get("load_simulator")
    if not isinstance(raw, dict):
        return _LoadSimulatorRuntimeConfig()

    only_raw = raw.get("only", [])
    only = [str(item).strip() for item in only_raw if str(item).strip()] if isinstance(only_raw, list) else []
    return _LoadSimulatorRuntimeConfig(
        enabled=bool(raw.get("enabled", False)),
        config_path=str(raw.get("config_path", "")).strip(),
        only=only,
        timeout_seconds=int(raw.get("timeout_seconds", 900) or 900),
        strict=bool(raw.get("strict", True)),
    )


def _sanitize_text(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


async def _run_load_simulator(config_path: str, only: list[str], timeout_seconds: int = 900) -> dict[str, Any]:
    """
    Run load_simulator and return parsed JSON payload.
    """
    cmd = [
        sys.executable,
        "-m",
        "load_simulator",
        "run",
        "--config",
        config_path,
        "--output-format",
        "json",
    ]
    for scenario in only:
        cmd.extend(["--only", scenario])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        raise LoadSimulatorError(f"failed to start load_simulator: {exc}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise LoadSimulatorError(f"load_simulator timeout after {timeout_seconds}s") from exc

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise LoadSimulatorError(
            "load_simulator process failed "
            f"(returncode={proc.returncode}, stderr={_sanitize_text(stderr)})"
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LoadSimulatorError(
            "invalid load_simulator JSON output "
            f"(stdout={_sanitize_text(stdout)}, stderr={_sanitize_text(stderr)})"
        ) from exc

    if not isinstance(payload, dict):
        raise LoadSimulatorError("load_simulator JSON payload must be an object")

    exit_code = int(payload.get("exit_code", 1))
    if exit_code != 0:
        raise LoadSimulatorError(
            f"load_simulator exit_code={exit_code} "
            f"(stderr={_sanitize_text(stderr)})"
        )
    return payload


class BaseScenario(ABC):
    """
    Base class for all fault scenarios.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    def description(self) -> str:
        return ""

    @property
    def layer(self) -> str:
        return "os"

    @abstractmethod
    async def inject(self, ctx: FaultContext) -> InjectResult:
        pass

    async def recover(self, ctx: FaultContext) -> RecoverResult:
        return RecoverResult(success=True, fault_id=ctx.fault_id)

    async def verify(self, ctx: FaultContext) -> bool:
        return True

    def monitor_queries(self) -> dict[str, str]:
        return {}

    def generate_fault_id(self, ctx: FaultContext) -> str:
        return f"{self.name}_{ctx.target_node}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    async def _maybe_run_load_simulator(self, ctx: FaultContext) -> str | None:
        """
        Optionally run load_simulator from scenario params.

        Returns:
            None on success / skipped, or an error string in strict mode.
        """
        cfg = _extract_load_simulator_config(ctx.params)
        if not cfg.enabled:
            return None

        if not cfg.config_path:
            err = "load_simulator.enabled=true but config_path is empty"
            if cfg.strict:
                return err
            ctx.params.setdefault("_load_simulator_warnings", []).append(err)
            return None

        run_detail: dict[str, Any] = {
            "scenario": self.name,
            "config_path": cfg.config_path,
            "only": cfg.only,
            "timeout_seconds": cfg.timeout_seconds,
            "strict": cfg.strict,
        }

        try:
            payload = await _run_load_simulator(cfg.config_path, cfg.only, timeout_seconds=cfg.timeout_seconds)
            run_detail["success"] = True
            run_detail["result"] = payload.get("summary", payload)
        except Exception as exc:  # noqa: BLE001
            run_detail["success"] = False
            run_detail["error"] = str(exc)
            ctx.params.setdefault("_load_simulator_runs", []).append(run_detail)
            if cfg.strict:
                return str(exc)
            ctx.params.setdefault("_load_simulator_warnings", []).append(str(exc))
            return None

        ctx.params.setdefault("_load_simulator_runs", []).append(run_detail)
        return None
