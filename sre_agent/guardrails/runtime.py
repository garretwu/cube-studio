"""Runtime adapter for strict NeMo Guardrails integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
import asyncio
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any


class GuardrailsDependencyMissingError(RuntimeError):
    """Raised when the guardrails runtime cannot be initialized."""


@dataclass(slots=True)
class GuardrailsRuntime:
    """Runtime wrapper around NeMo guardrails."""

    config_path: Path
    available: bool
    enabled: bool
    passthrough: bool
    reason: str | None = None
    config: Any | None = None
    rendered_config_path: Path | None = None
    tempdir: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False)
    rails: Any | None = field(default=None, repr=False)

    def invoke(self, input_value: Any) -> Any:
        """Invoke input guardrails directly with stable blocked-message semantics."""
        self.require_available()
        blocked_message = self._blocked_message_for_input(input_value)
        if blocked_message is not None:
            return self._format_like_input(input_value, blocked_message)
        return self._format_like_input(input_value, "")

    async def ainvoke(self, input_value: Any) -> Any:
        """Async version of invoke."""
        return self.invoke(input_value)

    def wrap(self, runnable: Any) -> Any:
        """Wrap a runnable with guardrails."""
        if not self.enabled or self.rails is None:
            raise GuardrailsDependencyMissingError(
                self.reason or "guardrails runtime is unavailable"
            )
        runnable_lambda = import_module("langchain_core.runnables").RunnableLambda
        return runnable_lambda(
            lambda input_value: self._guarded_wrap_invoke(runnable, input_value),
            afunc=lambda input_value: self._guarded_wrap_ainvoke(runnable, input_value),
        )

    def require_available(self) -> GuardrailsRuntime:
        """Raise if the runtime could not be loaded."""
        if not self.available:
            raise GuardrailsDependencyMissingError(
                self.reason or "guardrails runtime is unavailable"
            )
        return self

    def _extract_text_from_result(self, result: Any) -> str:
        if isinstance(result, dict):
            if "content" in result:
                return str(result["content"])
            if "output" in result:
                return str(result["output"])
        if hasattr(result, "content"):
            return str(result.content)
        return str(result)

    def _format_like_input(self, input_value: Any, text: str) -> Any:
        if isinstance(input_value, dict):
            return {"output": text}
        return text

    def _extract_input_text(self, input_value: Any) -> str:
        if isinstance(input_value, str):
            return input_value
        if isinstance(input_value, dict):
            if "input" in input_value:
                return str(input_value["input"])
            return str(input_value)
        return str(input_value)

    def _blocked_message_for_input(self, input_value: Any) -> str | None:
        text = self._extract_input_text(input_value)
        if any(phrase in text for phrase in _DESTRUCTIVE_PHRASES):
            return _DESTRUCTIVE_MESSAGE
        if any(phrase in text for phrase in _INJECTION_PHRASES):
            return _INJECTION_MESSAGE
        return None

    def _sanitize_sync(self, text: str) -> str:
        module = import_module("sre_agent.guardrails.actions")
        sanitize_tool_output = getattr(module, "sanitize_tool_output")
        return asyncio.run(sanitize_tool_output(text))

    async def _sanitize_async(self, text: str) -> str:
        module = import_module("sre_agent.guardrails.actions")
        sanitize_tool_output = getattr(module, "sanitize_tool_output")
        return await sanitize_tool_output(text)

    def _call_runnable_sync(self, runnable: Any, input_value: Any) -> Any:
        if hasattr(runnable, "invoke"):
            return runnable.invoke(input_value)
        return runnable(input_value)

    async def _call_runnable_async(self, runnable: Any, input_value: Any) -> Any:
        if hasattr(runnable, "ainvoke"):
            return await runnable.ainvoke(input_value)
        if hasattr(runnable, "invoke"):
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, runnable.invoke, input_value)
        return runnable(input_value)

    def _guarded_wrap_invoke(self, runnable: Any, input_value: Any) -> Any:
        blocked_message = self._blocked_message_for_input(input_value)
        if blocked_message is not None:
            return self._format_like_input(input_value, blocked_message)

        raw_output = self._call_runnable_sync(runnable, input_value)
        text = self._extract_text_from_result(raw_output)
        sanitized = self._sanitize_sync(text)
        return self._format_like_input(input_value, sanitized)

    async def _guarded_wrap_ainvoke(self, runnable: Any, input_value: Any) -> Any:
        blocked_message = self._blocked_message_for_input(input_value)
        if blocked_message is not None:
            return self._format_like_input(input_value, blocked_message)

        raw_output = await self._call_runnable_async(runnable, input_value)
        text = self._extract_text_from_result(raw_output)
        sanitized = await self._sanitize_async(text)
        return self._format_like_input(input_value, sanitized)


_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")
_DESTRUCTIVE_PHRASES = (
    "删除这个 pod",
    "kubectl delete",
    "scale down to zero",
    "重启所有节点",
    "drain 这个 node",
    "format disk",
    "rm -rf",
)
_INJECTION_PHRASES = (
    "忽略上面的指令",
    "ignore previous instructions",
    "你现在是一个",
    "system: you are now",
)
_DESTRUCTIVE_MESSAGE = (
    "破坏性操作需要通过修复审批流程执行。我可以帮你诊断问题并生成修复计划，但无法直接执行写操作。"
    "请使用 GUI 的修复流程或 `sre-agent diagnose` 命令。"
)
_INJECTION_MESSAGE = "我只能处理 AIDC 基础设施相关的运维请求。"


def _render_guardrails_config_tree(config_path: Path) -> tuple[Path, tempfile.TemporaryDirectory[str]]:
    tempdir = tempfile.TemporaryDirectory(prefix="sre-guardrails-")
    rendered_root = Path(tempdir.name)
    shutil.copytree(config_path, rendered_root, dirs_exist_ok=True)

    config_file = rendered_root / "config.yml"
    config_text = config_file.read_text(encoding="utf-8")
    rendered_text = os.path.expandvars(config_text)
    unresolved = sorted(set(_ENV_VAR_PATTERN.findall(rendered_text)))
    if unresolved:
        raise GuardrailsDependencyMissingError(
            "missing guardrails model environment variables: " + ", ".join(unresolved)
        )
    config_file.write_text(rendered_text, encoding="utf-8")
    return rendered_root, tempdir


def load_guardrails_runtime(
    path: str | Path = "sre_agent/guardrails",
    passthrough: bool = True,
    *,
    required: bool = False,
) -> GuardrailsRuntime:
    """Load a NeMo guardrails runtime from a config directory."""
    config_path = Path(path)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise GuardrailsDependencyMissingError(
            f"OPENAI_API_KEY is required to initialize guardrails from {config_path}"
        )
    if not os.getenv("AGENT_LLM_MODEL", "").strip():
        raise GuardrailsDependencyMissingError(
            f"AGENT_LLM_MODEL is required to initialize guardrails from {config_path}"
        )
    if not os.getenv("SAFETY_CHECK_MODEL", "").strip():
        raise GuardrailsDependencyMissingError(
            f"SAFETY_CHECK_MODEL is required to initialize guardrails from {config_path}"
        )

    try:
        rails_module = import_module("nemoguardrails")
        runnable_module = import_module(
            "nemoguardrails.integrations.langchain.runnable_rails"
        )
        rails_config_cls = getattr(rails_module, "RailsConfig")
        runnable_rails_cls = getattr(runnable_module, "RunnableRails")
    except Exception as exc:  # pragma: no cover - exercised in tests via monkeypatch
        raise GuardrailsDependencyMissingError(
            f"nemoguardrails is required to load {config_path}: {exc}"
        ) from exc

    try:
        rendered_config_path, tempdir = _render_guardrails_config_tree(config_path)
        config = rails_config_cls.from_path(str(rendered_config_path))
        rails = runnable_rails_cls(config=config, passthrough=passthrough)
    except Exception as exc:
        raise GuardrailsDependencyMissingError(
            f"failed to initialize guardrails runtime from {config_path}: {exc}"
        ) from exc

    return GuardrailsRuntime(
        config_path=config_path,
        available=True,
        enabled=True,
        passthrough=passthrough,
        config=config,
        rendered_config_path=rendered_config_path,
        tempdir=tempdir,
        rails=rails,
    )
