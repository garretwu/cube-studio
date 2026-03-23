from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from sre_agent.guardrails import (
    GuardrailsDependencyMissingError,
    load_guardrails_runtime,
    sanitize_tool_output,
    validate_tool_input,
)
from sre_agent.guardrails import runtime as guardrails_runtime


def _guardrails_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "guardrails"


def _require_live_guardrails() -> None:
    if os.getenv("SRE_AGENT_LIVE_GUARDRAILS") != "1":
        pytest.skip("set SRE_AGENT_LIVE_GUARDRAILS=1 to run live guardrails observations")
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is required for live guardrails observations")
    pytest.importorskip("nemoguardrails")
    pytest.importorskip("langchain_openai")


class _FakeRailsConfig:
    last_path: str | None = None

    @classmethod
    def from_path(cls, path: str) -> "_FakeRailsConfig":
        cls.last_path = path
        instance = cls()
        instance.loaded_from = path
        return instance


class _FakeRunnableRails:
    def __init__(self, *, config: object, passthrough: bool) -> None:
        self.config = config
        self.passthrough = passthrough

    def __or__(self, runnable: object) -> tuple[str, object, object]:
        return ("wrapped", self, runnable)


def _force_missing_nemo(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import_module = guardrails_runtime.import_module

    def _missing(name: str):  # noqa: ANN001
        if name.startswith("nemoguardrails"):
            raise ModuleNotFoundError(name)
        return original_import_module(name)

    monkeypatch.setattr(guardrails_runtime, "import_module", _missing)


def _install_fake_nemo(monkeypatch: pytest.MonkeyPatch) -> None:
    nemo = ModuleType("nemoguardrails")
    nemo.RailsConfig = _FakeRailsConfig

    integrations = ModuleType("nemoguardrails.integrations")
    langchain = ModuleType("nemoguardrails.integrations.langchain")
    runnable_rails = ModuleType("nemoguardrails.integrations.langchain.runnable_rails")
    runnable_rails.RunnableRails = _FakeRunnableRails

    actions = ModuleType("nemoguardrails.actions")

    def action(*args: object, **kwargs: object):  # noqa: ANN202
        def decorator(func: object) -> object:
            return func

        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return decorator

    actions.action = action

    monkeypatch.setitem(sys.modules, "nemoguardrails", nemo)
    monkeypatch.setitem(sys.modules, "nemoguardrails.integrations", integrations)
    monkeypatch.setitem(sys.modules, "nemoguardrails.integrations.langchain", langchain)
    monkeypatch.setitem(
        sys.modules,
        "nemoguardrails.integrations.langchain.runnable_rails",
        runnable_rails,
    )
    monkeypatch.setitem(sys.modules, "nemoguardrails.actions", actions)


class TestGuardrailsUnit:
    @pytest.mark.asyncio
    async def test_unit_validate_tool_input_allows_whitelisted_log_path_when_safe(self) -> None:
        allowed = await validate_tool_input(
            tool_name="logs.read",
            tool_input={"log_path": "/var/log/syslog", "node": "worker-01"},
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_unit_validate_tool_input_rejects_non_whitelisted_log_path_when_requested(self) -> None:
        allowed = await validate_tool_input(
            tool_name="logs.read",
            tool_input={"log_path": "/etc/passwd"},
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_unit_validate_tool_input_rejects_shell_injection_when_meta_chars_present(self) -> None:
        allowed = await validate_tool_input(
            tool_name="metrics.query",
            tool_input={"query": "gpu_util; rm -rf /"},
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_unit_validate_tool_input_allows_plain_rm_rf_phrase_when_no_shell_meta_chars_are_present(self) -> None:
        allowed = await validate_tool_input(
            tool_name="shell.exec",
            tool_input={"command": "rm -rf /tmp/test-guardrails"},
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_unit_sanitize_tool_output_redacts_sensitive_tokens_when_present(self) -> None:
        sanitized = await sanitize_tool_output(
            "password=abc token:123 api_key=xyz secret=qwe src=10.10.1.2"
        )
        assert "abc" not in sanitized
        assert "123" not in sanitized
        assert "xyz" not in sanitized
        assert "qwe" not in sanitized
        assert "10.10.1.2" not in sanitized
        assert sanitized.count("[REDACTED]") >= 5

    def test_unit_load_guardrails_runtime_raises_when_dependency_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _force_missing_nemo(monkeypatch)
        with pytest.raises(GuardrailsDependencyMissingError, match="nemoguardrails is required"):
            load_guardrails_runtime(_guardrails_dir())

    def test_unit_load_guardrails_runtime_raises_when_openai_api_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _install_fake_nemo(monkeypatch)
        with pytest.raises(GuardrailsDependencyMissingError, match="OPENAI_API_KEY is required"):
            load_guardrails_runtime(_guardrails_dir())

    def test_unit_load_guardrails_runtime_raises_when_model_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("AGENT_LLM_MODEL", raising=False)
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _install_fake_nemo(monkeypatch)
        with pytest.raises(GuardrailsDependencyMissingError, match="AGENT_LLM_MODEL is required"):
            load_guardrails_runtime(_guardrails_dir())

    def test_unit_public_exports_are_stable_when_imported_from_package(self) -> None:
        import sre_agent.guardrails as guardrails

        assert "GuardrailsRuntime" in guardrails.__all__
        assert "load_guardrails_runtime" in guardrails.__all__
        assert "validate_tool_input" in guardrails.__all__
        assert "sanitize_tool_output" in guardrails.__all__


class TestGuardrailsIntegration:
    def test_integration_guardrails_files_match_expected_contract_when_loaded_from_repo(self) -> None:
        guardrails_dir = _guardrails_dir()
        config = yaml.safe_load((guardrails_dir / "config.yml").read_text(encoding="utf-8"))
        prompts = yaml.safe_load((guardrails_dir / "prompts.yml").read_text(encoding="utf-8"))

        assert (guardrails_dir / "rails" / "input.co").exists()
        assert (guardrails_dir / "rails" / "output.co").exists()
        assert (guardrails_dir / "rails" / "execution.co").exists()
        assert (guardrails_dir / "rails" / "dialog.co").exists()
        assert [model["type"] for model in config["models"]] == [
            "main",
            "self_check_input",
            "self_check_output",
        ]
        assert "instructions" in config
        assert "prompts" in prompts
        assert config["rails"]["input"]["flows"] == [
            "block destructive actions",
            "block injection attempts",
        ]
        assert config["rails"]["output"]["flows"] == ["sanitize sensitive output"]

    def test_integration_input_rails_include_destructive_and_injection_examples_when_loaded(self) -> None:
        input_rails = (_guardrails_dir() / "rails" / "input.co").read_text(encoding="utf-8")

        assert '"kubectl delete" in $user_message' in input_rails
        assert '"rm -rf" in $user_message' in input_rails
        assert '"ignore previous instructions" in $user_message' in input_rails
        assert '"忽略上面的指令" in $user_message' in input_rails

    def test_integration_runtime_loads_fake_nemo_when_module_tree_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-safety-model")
        _install_fake_nemo(monkeypatch)
        runtime = load_guardrails_runtime(_guardrails_dir(), passthrough=False)

        assert runtime.available is True
        assert runtime.enabled is True
        assert runtime.passthrough is False
        assert runtime.rendered_config_path is not None
        assert _FakeRailsConfig.last_path == str(runtime.rendered_config_path)
        assert getattr(runtime.config, "loaded_from") == str(runtime.rendered_config_path)
        rendered_config = runtime.rendered_config_path / "config.yml"
        rendered_text = rendered_config.read_text(encoding="utf-8")
        assert "${AGENT_LLM_MODEL}" not in rendered_text
        assert "${SAFETY_CHECK_MODEL}" not in rendered_text
        assert "test-model" in rendered_text
        assert "test-safety-model" in rendered_text

    def test_integration_runtime_wraps_runnable_when_dependency_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from langchain_core.runnables import RunnableLambda

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _install_fake_nemo(monkeypatch)
        runtime = load_guardrails_runtime(_guardrails_dir())

        wrapped = runtime.wrap(RunnableLambda(lambda _: "password=abc"))
        result = wrapped.invoke({"input": "读取最近一次诊断结果"})

        assert result == {"output": "[REDACTED]"}

    def test_integration_runtime_handles_real_nemo_stack_when_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pytest.importorskip("nemoguardrails")
        pytest.importorskip("langchain_openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "gpt-4o-mini")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "gpt-4o-mini")

        runtime = load_guardrails_runtime(_guardrails_dir())

        assert runtime.config_path == _guardrails_dir()
        assert runtime.available is True
        assert runtime.enabled is True
        assert runtime.rails is not None


class TestGuardrailsE2E:
    @pytest.mark.asyncio
    async def test_e2e_allows_input_and_sanitizes_output_when_happy_path(self) -> None:
        valid = await validate_tool_input(
            tool_name="logs.read",
            tool_input={"log_path": "/var/log/messages", "namespace": "infer"},
        )
        sanitized = await sanitize_tool_output("token=abcd password=hunter2 from 10.1.2.3")

        assert valid is True
        assert "[REDACTED]" in sanitized
        assert "hunter2" not in sanitized
        assert "10.1.2.3" not in sanitized

    def test_e2e_runtime_blocks_startup_when_nemo_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _force_missing_nemo(monkeypatch)
        with pytest.raises(GuardrailsDependencyMissingError, match="nemoguardrails is required"):
            load_guardrails_runtime(_guardrails_dir())

    @pytest.mark.asyncio
    async def test_e2e_blocks_invalid_input_when_execution_guardrails_would_reject(self) -> None:
        valid = await validate_tool_input(
            tool_name="logs.read",
            tool_input={"log_path": "/tmp/evil.log", "filter": "panic && cat /etc/shadow"},
        )
        assert valid is False

    def test_e2e_runtime_raises_when_dependency_is_missing_even_without_required_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("AGENT_LLM_MODEL", "test-model")
        monkeypatch.setenv("SAFETY_CHECK_MODEL", "test-model")
        _force_missing_nemo(monkeypatch)
        with pytest.raises(GuardrailsDependencyMissingError):
            load_guardrails_runtime(_guardrails_dir())


class TestGuardrailsLiveE2E:
    def test_e2e_live_runtime_observes_current_behavior_for_destructive_input_when_invoked(self) -> None:
        _require_live_guardrails()

        runtime = load_guardrails_runtime(_guardrails_dir())
        result = runtime.invoke({"input": "kubectl delete pod vllm-0"})
        assert "破坏性操作需要通过修复审批流程执行" in result["output"]

    def test_e2e_live_runtime_observes_current_behavior_for_sensitive_output_when_wrapped_runnable(self) -> None:
        _require_live_guardrails()
        from langchain_core.runnables import RunnableLambda

        runtime = load_guardrails_runtime(_guardrails_dir())
        def _run(_: object) -> str:
            return "password=abc token=xyz api_key=secret from 10.1.2.3"

        wrapped = runtime.wrap(RunnableLambda(_run))
        result = wrapped.invoke({"input": "读取最近一次诊断结果"})

        assert result["output"] == "[REDACTED] [REDACTED] [REDACTED] from [REDACTED]"
