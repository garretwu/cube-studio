from __future__ import annotations

from pathlib import Path

from sre_agent.config import (
    GLM_DEFAULT_BASE_URL,
    GLM_DEFAULT_FALLBACK_MODELS,
    GLM_DEFAULT_MODEL,
    SREAgentConfig,
    apply_llm_env_from_config,
    load_config,
    resolve_llm_runtime_settings,
)


def test_agent_d_config_blocks_parse_from_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: aidc-test",
                "ha:",
                "  enabled: true",
                "  heartbeat_interval: 5",
                "  heartbeat_timeout: 15",
                '  redis_url: "redis://redis:6379/1"',
                '  shared_storage: "/srv/shared"',
                "slo:",
                "  enabled: true",
                "  window_hours: 24",
                "  diagnosis_success_threshold: 0.85",
                "  false_fix_threshold: 0.05",
                "  llm_success_threshold: 0.99",
                "  auto_recovery_hours: 1",
                "data_lifecycle:",
                "  hot_retention_days: 90",
                "  warm_retention_days: 365",
                '  cleanup_schedule: "0 3 * * *"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.ha.enabled is True
    assert config.ha.heartbeat_interval == 5
    assert config.ha.heartbeat_timeout == 15
    assert config.slo.window_hours == 24
    assert config.slo.auto_recovery_hours == 1
    assert config.data_lifecycle.hot_retention_days == 90
    assert config.data_lifecycle.cleanup_schedule == "0 3 * * *"


def test_apply_llm_env_from_config_glm_provider_applies_defaults() -> None:
    config = SREAgentConfig.model_validate(
        {
            "llm": {
                "provider": "glm",
            }
        }
    )
    env: dict[str, str] = {}
    applied = apply_llm_env_from_config(config, env, only_if_missing=True)

    assert env["SRE_LLM_PROVIDER"] == "glm"
    assert env["SRE_OPENAI_BASE_URL"] == GLM_DEFAULT_BASE_URL
    assert env["SRE_LLM_MODEL"] == GLM_DEFAULT_MODEL
    assert env["SRE_LLM_FALLBACK_MODELS"] == ",".join(GLM_DEFAULT_FALLBACK_MODELS)
    assert applied["SRE_LLM_PROVIDER"] == "glm"


def test_resolve_llm_runtime_settings_env_override_provider_defaults() -> None:
    env = {
        "SRE_LLM_PROVIDER": "glm",
        "SRE_OPENAI_BASE_URL": "https://example-override/v1",
        "SRE_LLM_MODEL": "glm-override",
        "SRE_LLM_FALLBACK_MODELS": "glm-5-turbo,glm-4.7",
        "SRE_OPENAI_API_KEY": "test-key",
    }
    resolved = resolve_llm_runtime_settings(env)

    assert resolved["provider"] == "glm"
    assert resolved["base_url"] == "https://example-override/v1"
    assert resolved["model"] == "glm-override"
    assert resolved["fallback_models"] == ["glm-5-turbo", "glm-4.7"]
    assert resolved["api_key_source"] == "SRE_OPENAI_API_KEY"


def test_load_config_applies_reason_timeout_tuning_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: aidc-timeout-tuning",
                "agent:",
                '  reasoning_model_family: "glm-5.1"',
                "  reasoning_context_strategy: transcript_compact",
                "  reasoning_overflow_behavior: compact",
                "  reasoning_input_target_tokens: 32000",
                '  ttft_external_process_default_node: "10.11.4.13"',
                "  step_timeout_sec: 240",
                "  total_timeout_sec: 900",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.agent.reasoning_model_family == "glm-5.1"
    assert config.agent.reasoning_context_strategy == "transcript_compact"
    assert config.agent.reasoning_overflow_behavior == "compact"
    assert config.agent.reasoning_input_target_tokens == 32000
    assert config.agent.ttft_external_process_default_node == "10.11.4.13"
    assert config.agent.step_timeout_sec == 240
    assert config.agent.total_timeout_sec == 900


def test_load_config_applies_remediation_observation_polling_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "global:",
                "  aidc_id: aidc-remediation-observe",
                "remediation:",
                "  observation_seconds: 600",
                "  observation_poll_seconds: 10",
                "  execution_timeout_seconds: 900",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.remediation.observation_seconds == 600
    assert config.remediation.ranked_intermediate_observation_seconds == 30
    assert config.remediation.observation_poll_seconds == 10
    assert config.remediation.execution_timeout_seconds == 900
