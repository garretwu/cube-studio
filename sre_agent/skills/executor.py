from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sre_agent.skills.policy import SkillDecision, SkillPolicy, SkillPolicyResult
from sre_agent.skills.registry import SkillDescriptor, SkillRegistry, SkillRegistryError


@dataclass(frozen=True)
class SkillExecutionResult:
    skill_id: str
    status: str
    summary: str
    mode: str
    script: str | None = None
    args: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    policy_decision: str = SkillDecision.ALLOW.value
    audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillExecutor:
    """Execute Claude-style skill scripts."""

    DEFAULT_TIMEOUT_SEC = 300
    DEFAULT_OUTPUT_LIMIT = 8000

    async def execute(
        self,
        *,
        skill: SkillDescriptor,
        script: str | None = None,
        args: list[str] | None = None,
        policy: SkillPolicy | None = None,
        skill_registry: SkillRegistry | None = None,
        timeout_sec: int | float | None = None,
        output_limit: int | None = None,
    ) -> SkillExecutionResult:
        if not script:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="failed",
                summary="parameter 'script' is required for skill execution",
                mode="script",
            )
        return await self.execute_script(
            skill=skill,
            script=script,
            args=args or [],
            policy=policy or SkillPolicy(),
            skill_registry=skill_registry,
            timeout_sec=timeout_sec,
            output_limit=output_limit,
        )

    async def execute_script(
        self,
        *,
        skill: SkillDescriptor,
        script: str,
        args: list[str],
        policy: SkillPolicy,
        skill_registry: SkillRegistry | None = None,
        timeout_sec: int | float | None = None,
        output_limit: int | None = None,
    ) -> SkillExecutionResult:
        registry = skill_registry or SkillRegistry()
        try:
            script_path = registry.resolve_script_path(skill.id, script)
        except SkillRegistryError as exc:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="failed",
                summary=str(exc),
                mode="script",
                script=script,
                args=list(args),
            )

        decision = policy.evaluate(skill_id=skill.id, script=script)
        if decision.decision is SkillDecision.DENY:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="denied",
                summary=decision.reason,
                mode="script",
                script=script,
                args=list(args),
                policy_decision=decision.decision.value,
                audit=self._build_audit(skill=skill, script_path=script_path, args=args, decision=decision),
            )
        if decision.decision is SkillDecision.ASK:
            return SkillExecutionResult(
                skill_id=skill.id,
                status="ask",
                summary=decision.reason,
                mode="script",
                script=script,
                args=list(args),
                policy_decision=decision.decision.value,
                audit=self._build_audit(skill=skill, script_path=script_path, args=args, decision=decision),
            )

        command = self._build_command(script_path, args)
        env = self._build_env()
        limit = max(256, int(output_limit or self.DEFAULT_OUTPUT_LIMIT))
        timeout = max(1, int(timeout_sec or self.DEFAULT_TIMEOUT_SEC))
        stdout = ""
        stderr = ""
        exit_code: int | None = None
        timed_out = False
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(skill.directory),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                timed_out = True
                process.kill()
                raw_stdout, raw_stderr = await process.communicate()
            stdout = self._truncate(raw_stdout.decode("utf-8", errors="replace"), limit)
            stderr = self._truncate(raw_stderr.decode("utf-8", errors="replace"), limit)
            exit_code = process.returncode
        except Exception as exc:  # noqa: BLE001
            return SkillExecutionResult(
                skill_id=skill.id,
                status="failed",
                summary=f"skill script execution failed: {exc}",
                mode="script",
                script=script,
                args=list(args),
                policy_decision=decision.decision.value,
                audit=self._build_audit(skill=skill, script_path=script_path, args=args, decision=decision),
            )

        status = "success" if not timed_out and exit_code == 0 else "failed"
        summary = f"script completed with exit_code={exit_code}"
        if timed_out:
            summary = f"script timed out after {timeout}s"
        elif exit_code not in (0, None):
            summary = f"script exited with code {exit_code}"
        return SkillExecutionResult(
            skill_id=skill.id,
            status=status,
            summary=summary,
            mode="script",
            script=script,
            args=list(args),
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            policy_decision=decision.decision.value,
            audit=self._build_audit(skill=skill, script_path=script_path, args=args, decision=decision),
        )

    @staticmethod
    def _build_command(script_path: Path, args: list[str]) -> list[str]:
        suffix = script_path.suffix.lower()
        normalized_args = [str(item) for item in args]
        if suffix == ".py":
            return [sys.executable, str(script_path), *normalized_args]
        if suffix in {".sh", ".bash"}:
            return ["bash", str(script_path), *normalized_args]
        return [str(script_path), *normalized_args]

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    @staticmethod
    def _build_env() -> dict[str, str]:
        allowed_exact = {
            "HOME",
            "LANG",
            "LC_ALL",
            "PATH",
            "PYTHONPATH",
            "PYTHONUNBUFFERED",
            "KUBECONFIG",
        }
        allowed_prefixes = ("SRE_", "OPENAI_", "CUDA_", "NVIDIA_")
        env: dict[str, str] = {}
        for key, value in os.environ.items():
            if key in allowed_exact or key.startswith(allowed_prefixes):
                env[key] = value
        env.setdefault("PATH", os.environ.get("PATH", ""))
        env.setdefault("LANG", "C.UTF-8")
        env.setdefault("LC_ALL", "C.UTF-8")
        return env

    @staticmethod
    def _build_audit(
        *,
        skill: SkillDescriptor,
        script_path: Path,
        args: list[str],
        decision: SkillPolicyResult,
    ) -> dict[str, Any]:
        return {
            "skill_id": skill.id,
            "skill_path": skill.path,
            "script": script_path.name,
            "script_path": str(script_path),
            "args": [str(item) for item in args],
            "policy_decision": decision.decision.value,
            "policy_reason": decision.reason,
        }
