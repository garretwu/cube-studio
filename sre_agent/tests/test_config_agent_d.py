from __future__ import annotations

from pathlib import Path

from sre_agent.config import load_config


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
