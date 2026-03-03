"""
Base scenario contracts and shared helpers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
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
    logger.info(
        "load_simulator subprocess starting: config=%s only=%s timeout=%ss",
        config_path,
        only,
        timeout_seconds,
    )
    started = time.monotonic()

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
    except asyncio.CancelledError:
        proc.kill()
        await proc.communicate()
        raise

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
    elapsed = time.monotonic() - started
    summary = payload.get("summary", {})
    summary_session = summary.get("session_id") if isinstance(summary, dict) else None
    summary_duration = summary.get("duration_seconds") if isinstance(summary, dict) else None
    logger.info(
        "load_simulator subprocess completed: elapsed=%.2fs summary_session_id=%s summary_duration=%s",
        elapsed,
        summary_session,
        summary_duration,
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

    def _ls_state(self) -> dict[str, Any]:
        state = getattr(self, "_load_simulator_state", None)
        if state is None:
            state = {}
            setattr(self, "_load_simulator_state", state)
        return state

    def _start_load_simulator_task(self, ctx: FaultContext) -> str | None:
        """
        Start load_simulator in background task.

        Returns:
            strict-mode configuration error string when pre-check fails, else None.
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
        logger.info(
            "load_simulator task scheduled: scenario=%s fault_id=%s strict=%s config=%s only=%s timeout=%ss",
            self.name,
            ctx.fault_id,
            cfg.strict,
            cfg.config_path,
            cfg.only,
            cfg.timeout_seconds,
        )

        async def _runner() -> dict[str, Any]:
            try:
                payload = await _run_load_simulator(cfg.config_path, cfg.only, timeout_seconds=cfg.timeout_seconds)
                run_detail["success"] = True
                run_detail["result"] = payload.get("summary", payload)
                return run_detail
            except Exception as exc:  # noqa: BLE001
                run_detail["success"] = False
                run_detail["error"] = str(exc)
                logger.warning(
                    "load_simulator task failed: scenario=%s fault_id=%s error=%s",
                    self.name,
                    ctx.fault_id,
                    exc,
                )
                return run_detail

        task = asyncio.create_task(_runner())
        state = self._ls_state()
        state[ctx.fault_id] = {
            "task": task,
            "strict": cfg.strict,
        }
        return None

    async def _await_load_simulator_task(self, ctx: FaultContext) -> str | None:
        """
        Wait for background load_simulator task and persist run metadata.

        Returns:
            strict-mode runtime error string when task failed, else None.
        """
        state = self._ls_state()
        slot = state.pop(ctx.fault_id, None)
        if not isinstance(slot, dict):
            return None

        task = slot.get("task")
        strict = bool(slot.get("strict", True))
        if not isinstance(task, asyncio.Task):
            return None

        try:
            run_detail = await task
        except asyncio.CancelledError:
            run_detail = {
                "scenario": self.name,
                "success": False,
                "error": "load_simulator task cancelled",
            }

        if isinstance(run_detail, dict):
            ctx.params.setdefault("_load_simulator_runs", []).append(run_detail)
            if not bool(run_detail.get("success", False)):
                err = str(run_detail.get("error", "load_simulator failed"))
                ctx.params.setdefault("_load_simulator_warnings", []).append(err)
                if strict:
                    return err
            else:
                logger.info(
                    "load_simulator task joined: scenario=%s fault_id=%s success=true",
                    self.name,
                    ctx.fault_id,
                )
        return None

    async def _cancel_load_simulator_task(self, ctx: FaultContext) -> None:
        state = self._ls_state()
        slot = state.pop(ctx.fault_id, None)
        if not isinstance(slot, dict):
            return
        task = slot.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
            try:
                await task
            except Exception:  # noqa: BLE001
                pass
            logger.warning(
                "load_simulator task cancelled: scenario=%s fault_id=%s",
                self.name,
                ctx.fault_id,
            )

    async def post_inject(self, ctx: FaultContext) -> str | None:
        """
        Optional post-inject hook invoked by orchestrator during observe phase.
        """
        return await self._await_load_simulator_task(ctx)

    async def _maybe_run_load_simulator(self, ctx: FaultContext) -> str | None:
        """
        Optionally run load_simulator from scenario params.

        Returns:
            None on success / skipped, or an error string in strict mode.
        """
        precheck_error = self._start_load_simulator_task(ctx)
        if precheck_error:
            return precheck_error
        return await self._await_load_simulator_task(ctx)
