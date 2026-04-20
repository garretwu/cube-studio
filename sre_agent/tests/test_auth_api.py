from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sre_agent.api.middleware import install_middlewares
from sre_agent.api.routes import build_api_router
from sre_agent.auth.jwt import CurrentUser, encode_access_token, encode_refresh_token, resolve_jwt_settings
from sre_agent.config import SREAgentConfig


class _DummyServices:
    pass


def _build_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, str]:
    monkeypatch.setenv("JWT_SECRET", "secret")
    cfg = SREAgentConfig()
    settings = resolve_jwt_settings(cfg.auth)
    app = FastAPI()
    app.state.config = cfg
    app.state.jwt_settings = settings
    app.state.server_boot_id = "boot-abc"
    app.state.services = _DummyServices()
    install_middlewares(app)
    app.include_router(build_api_router())
    return app, "boot-abc"


def test_auth_token_and_refresh_endpoints_issue_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = _build_app(monkeypatch)
    settings = app.state.jwt_settings
    user = CurrentUser(user_id="u1", username="alice", role="operator")
    access = encode_access_token(user, settings, expire_seconds=120)
    refresh = encode_refresh_token(user, settings, expire_seconds=7200)

    client = TestClient(app)

    token_response = client.post("/api/auth/token", headers={"Authorization": f"Bearer {access}"})
    assert token_response.status_code == 200
    token_payload = token_response.json()["data"]
    assert token_payload["access_token"]
    assert token_payload["refresh_token"]
    assert token_payload["token_type"] == "Bearer"
    assert token_payload["server_boot_id"] == "boot-abc"

    refresh_response = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refresh_response.status_code == 200
    refresh_payload = refresh_response.json()["data"]
    assert refresh_payload["access_token"]
    assert refresh_payload["refresh_token"]


def test_auth_status_and_boot_id_header_present(monkeypatch: pytest.MonkeyPatch) -> None:
    app, boot_id = _build_app(monkeypatch)
    settings = app.state.jwt_settings
    user = CurrentUser(user_id="u1", username="alice", role="operator")
    access = encode_access_token(user, settings, expire_seconds=120)

    client = TestClient(app)
    response = client.get("/api/auth/status", headers={"Authorization": f"Bearer {access}"})

    assert response.status_code == 200
    assert response.headers.get("x-server-boot-id") == boot_id
    payload = response.json()["data"]
    assert payload["server_boot_id"] == boot_id
    assert payload["auth_error_kind"] in {"unknown", "expired", "invalid_signature", "missing"}


def test_auth_bootstrap_endpoint_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SRE_DEMO_AUTO_BOOTSTRAP_ENABLED", raising=False)
    app, _ = _build_app(monkeypatch)
    client = TestClient(app)

    response = client.post("/api/auth/bootstrap")

    assert response.status_code == 404


def test_auth_bootstrap_endpoint_issues_reusable_tokens_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_DEMO_AUTO_BOOTSTRAP_ENABLED", "true")
    app, boot_id = _build_app(monkeypatch)
    client = TestClient(app)

    bootstrap_response = client.post("/api/auth/bootstrap")
    assert bootstrap_response.status_code == 200
    payload = bootstrap_response.json()["data"]
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["server_boot_id"] == boot_id

    status_response = client.get(
        "/api/auth/status",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()["data"]
    assert status_payload["auth_error_kind"] == "unknown"
