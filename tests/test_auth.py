from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import (
    verify_admin_token,
    verify_app_or_admin_token,
    verify_shortcut_token,
)


def create_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.dependency_overrides[get_settings] = lambda: Settings(
        app_api_token="shortcut-token",
        admin_password="admin-token",
    )

    @test_app.get(
        "/shortcut",
        dependencies=[Depends(verify_shortcut_token)],
    )
    def shortcut() -> dict[str, bool]:
        return {"success": True}

    @test_app.get(
        "/admin",
        dependencies=[Depends(verify_admin_token)],
    )
    def admin() -> dict[str, bool]:
        return {"success": True}

    @test_app.get(
        "/shared",
        dependencies=[Depends(verify_app_or_admin_token)],
    )
    def shared() -> dict[str, bool]:
        return {"success": True}

    return test_app


def test_each_token_only_allows_its_own_scope() -> None:
    with TestClient(create_test_app()) as client:
        shortcut_allowed = client.get(
            "/shortcut",
            headers={"Authorization": "Bearer shortcut-token"},
        )
        shortcut_denied_admin = client.get(
            "/admin",
            headers={"Authorization": "Bearer shortcut-token"},
        )
        admin_allowed = client.get(
            "/admin",
            headers={"Authorization": "Bearer admin-token"},
        )
        admin_denied_shortcut = client.get(
            "/shortcut",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert shortcut_allowed.status_code == 200
    assert shortcut_denied_admin.status_code == 401
    assert admin_allowed.status_code == 200
    assert admin_denied_shortcut.status_code == 401


def test_invalid_token_returns_401() -> None:
    with TestClient(create_test_app()) as client:
        response = client.get(
            "/admin",
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_shared_scope_allows_app_and_admin_tokens() -> None:
    with TestClient(create_test_app()) as client:
        app_response = client.get(
            "/shared",
            headers={"Authorization": "Bearer shortcut-token"},
        )
        admin_response = client.get(
            "/shared",
            headers={"Authorization": "Bearer admin-token"},
        )

    assert app_response.status_code == 200
    assert admin_response.status_code == 200


def test_missing_token_returns_401() -> None:
    with TestClient(create_test_app()) as client:
        response = client.get("/shortcut")

    assert response.status_code == 401


def test_default_password_replaces_old_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "old-admin-token")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setattr("app.config.load_dotenv", lambda: None)
    test_app = create_test_app()
    test_app.dependency_overrides[get_settings] = Settings.from_environment

    with TestClient(test_app) as client:
        assert client.get(
            "/admin", headers={"Authorization": "Bearer 12356"}
        ).status_code == 200
        assert client.get(
            "/admin", headers={"Authorization": "Bearer old-admin-token"}
        ).status_code == 401
