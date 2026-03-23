from __future__ import annotations

import os

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from sre_agent.auth.jwt import CurrentUser, decode_token, encode_token, resolve_jwt_settings
from sre_agent.auth.rbac import require_role, role_allows_safety_level
from sre_agent.models.common import SafetyLevel


class TestAuthUnit:
    def test_unit_encodes_and_decodes_jwt_when_secret_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)
        user = decode_token(token, settings)

        assert user.username == "alice"
        assert user.role == "operator"

    def test_unit_maps_roles_to_safety_levels_when_checked(self) -> None:
        assert role_allows_safety_level("viewer", SafetyLevel.READONLY) is True
        assert role_allows_safety_level("viewer", SafetyLevel.HIGH) is False
        assert role_allows_safety_level("admin", SafetyLevel.CRITICAL) is True


class TestAuthIntegration:
    def test_integration_rest_depends_allows_authorized_role_when_token_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        token = encode_token(CurrentUser(user_id="u1", username="alice", role="operator"), settings)

        app = FastAPI()
        app.state.jwt_settings = settings

        @app.get("/protected")
        async def protected(user=pytest.importorskip("fastapi").Depends(require_role("operator", "admin"))):  # type: ignore[attr-defined]
            return {"username": user.username}

        client = TestClient(app)
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        assert response.json()["username"] == "alice"

    def test_integration_rejects_invalid_token_when_rest_dependency_runs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()

        app = FastAPI()
        app.state.jwt_settings = settings

        @app.get("/protected")
        async def protected(user=pytest.importorskip("fastapi").Depends(require_role("operator"))):  # type: ignore[attr-defined]
            return {"username": user.username}

        client = TestClient(app)
        response = client.get("/protected", headers={"Authorization": "Bearer bad-token"})

        assert response.status_code == 401


class TestAuthE2E:
    def test_e2e_role_matrix_blocks_viewer_and_allows_admin_when_routes_protected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        viewer_token = encode_token(CurrentUser(user_id="u1", username="viewer", role="viewer"), settings)
        admin_token = encode_token(CurrentUser(user_id="u2", username="admin", role="admin"), settings)

        app = FastAPI()
        app.state.jwt_settings = settings

        @app.get("/admin")
        async def admin_only(user=pytest.importorskip("fastapi").Depends(require_role("admin"))):  # type: ignore[attr-defined]
            return {"username": user.username}

        client = TestClient(app)

        viewer_response = client.get("/admin", headers={"Authorization": f"Bearer {viewer_token}"})
        admin_response = client.get("/admin", headers={"Authorization": f"Bearer {admin_token}"})

        assert viewer_response.status_code == 403
        assert admin_response.status_code == 200

    def test_e2e_websocket_auth_closes_without_token_when_connecting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("JWT_SECRET", "secret")
        settings = resolve_jwt_settings()
        app = FastAPI()
        app.state.jwt_settings = settings

        @app.websocket("/ws")
        async def ws_endpoint(websocket: WebSocket) -> None:
            from sre_agent.auth.jwt import ws_authenticate

            await ws_authenticate(websocket)
            await websocket.accept()

        client = TestClient(app)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass
