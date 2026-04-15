from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from sre_agent.config import SREAgentConfig
from sre_agent.server import _load_redfish_preauth_targets, create_app
from sre_agent.tools.registry import ToolExecutionContext


@pytest.fixture(autouse=True)
def _ensure_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SRE_LLM_MODEL", "MiniMax-M2.7")


class _FakeAlertChannel:
    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        _ = (args, kwargs)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> bool:
        return True

    async def get_firing_alerts(self, filter_labels=None):  # noqa: ANN001, ANN201
        _ = filter_labels
        return []


@dataclass
class _AuthResult:
    success: bool
    error: str | None = None


class _FakeRedfishChannel:
    def __init__(self) -> None:
        self.auth_calls: list[tuple[str, str, str, bool]] = []
        self.web_credentials: list[tuple[str, str, str]] = []

    def set_web_credentials(self, bmc_host: str, username: str, password: str) -> None:
        self.web_credentials.append((bmc_host, username, password))

    async def authenticate(self, bmc_host: str, username: str, password: str, verify_tls: bool = True) -> _AuthResult:
        self.auth_calls.append((bmc_host, username, password, verify_tls))
        return _AuthResult(success=True)


def test_create_app_bootstraps_alert_knowledge_and_log_channels(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    monkeypatch.setattr("sre_agent.server.AlertChannel", _FakeAlertChannel)
    config = SREAgentConfig.model_validate(
        {
            "global": {
                "aidc_id": "test-aidc",
                "alertmanager_url": "http://alertmanager.local",
                "prometheus_url": "http://prometheus.local",
                "loki_url": "http://loki.local",
            },
            "ontology": {"db_path": str(tmp_path / "ontology.db"), "discovery": {"auto_discovery": False}},
            "memory": {"db_dir": str(tmp_path / "memory")},
            "knowledge_base": {"persist_dir": str(tmp_path / "knowledge")},
        }
    )

    with TestClient(create_app(config=config)) as client:
        statuses = client.app.state.services.tool_channel_status
        assert statuses["alert"]["health"] == "ready"
        assert statuses["knowledge"]["health"] == "ready"
        assert statuses["log"]["health"] == "ready"
        assert client.app.state.services.knowledge is not None


def test_load_redfish_preauth_targets_uses_env_fallback_for_placeholder_password(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    inventory = tmp_path / "live_inventory.yaml"
    inventory.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.1",
                "        username: admin",
                "        password: REPLACE_ME",
                "        verify_tls: false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SRE_REDFISH_PASSWORD", "Admin@9000")
    config = SREAgentConfig.model_validate(
        {
            "ontology": {
                "discovery": {
                    "mode": "live",
                    "live_inventory_path": str(inventory),
                    "auto_discovery": False,
                }
            }
        }
    )

    targets = _load_redfish_preauth_targets(config)
    assert len(targets) == 1
    assert targets[0]["bmc_host"] == "10.0.0.1"
    assert targets[0]["username"] == "admin"
    assert targets[0]["password"] == "Admin@9000"
    assert targets[0]["verify_tls"] is False


def test_create_app_preauthenticates_redfish_channel_on_startup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JWT_SECRET", "secret")
    inventory = tmp_path / "live_inventory.yaml"
    inventory.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.1",
                "        username: admin",
                "        password: Admin@9000",
                "        verify_tls: false",
            ]
        ),
        encoding="utf-8",
    )
    fake_redfish = _FakeRedfishChannel()
    context = ToolExecutionContext(channels={"redfish": fake_redfish})
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {
                "db_path": str(tmp_path / "ontology.db"),
                "discovery": {
                    "mode": "live",
                    "auto_discovery": False,
                    "live_inventory_path": str(inventory),
                },
            },
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config, execution_context=context)) as client:
        services = client.app.state.services
        assert len(fake_redfish.auth_calls) == 1
        assert services.tool_channel_status["redfish"]["health"] == "ready"
        assert services.tool_channel_status["redfish"]["mode"] == "channel+preauth"


def test_load_redfish_preauth_targets_supports_hybrid_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Test that hybrid mode loads BMC credentials just like live mode."""
    inventory = tmp_path / "live_inventory.yaml"
    inventory.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.1",
                "        username: admin",
                "        password: Admin@9000",
                "        verify_tls: false",
            ]
        ),
        encoding="utf-8",
    )
    config = SREAgentConfig.model_validate(
        {
            "ontology": {
                "discovery": {
                    "mode": "hybrid",
                    "live_inventory_path": str(inventory),
                    "auto_discovery": False,
                }
            }
        }
    )

    targets = _load_redfish_preauth_targets(config)
    assert len(targets) == 1
    assert targets[0]["bmc_host"] == "10.0.0.1"
    assert targets[0]["username"] == "admin"
    assert targets[0]["password"] == "Admin@9000"


def test_create_app_preauthenticates_redfish_channel_on_startup_hybrid_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Test that hybrid mode also pre-authenticates BMC credentials on startup."""
    monkeypatch.setenv("JWT_SECRET", "secret")
    inventory = tmp_path / "live_inventory.yaml"
    inventory.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-01",
                "      redfish:",
                "        bmc_host: 10.0.0.1",
                "        username: admin",
                "        password: Admin@9000",
                "        verify_tls: false",
            ]
        ),
        encoding="utf-8",
    )
    fake_redfish = _FakeRedfishChannel()
    context = ToolExecutionContext(channels={"redfish": fake_redfish})
    config = SREAgentConfig.model_validate(
        {
            "global": {"aidc_id": "test-aidc"},
            "ontology": {
                "db_path": str(tmp_path / "ontology.db"),
                "discovery": {
                    "mode": "hybrid",
                    "auto_discovery": False,
                    "live_inventory_path": str(inventory),
                },
            },
            "memory": {"db_dir": str(tmp_path / "memory")},
        }
    )

    with TestClient(create_app(config=config, execution_context=context)) as client:
        services = client.app.state.services
        assert len(fake_redfish.auth_calls) == 1
        assert services.tool_channel_status["redfish"]["health"] == "ready"
        assert services.tool_channel_status["redfish"]["mode"] == "channel+preauth"
