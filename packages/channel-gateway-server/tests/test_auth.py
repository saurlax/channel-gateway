"""Management session lifecycle, credential boundaries, and browser protections."""

from pathlib import Path

import pytest
from channel_gateway_server.app import create_app
from channel_gateway_server.auth import COOKIE_NAME, SESSION_SECONDS
from channel_gateway_server.models import Settings
from fastapi.testclient import TestClient

PASSWORD = "test-admin-password-0123456789"


@pytest.fixture
def console(tmp_path: Path):
    app = create_app(
        settings=Settings(admin_username="admin", admin_password=PASSWORD, data_dir=tmp_path, worker_enabled=False)
    )
    with TestClient(app) as client:
        yield client


def login(client, **credentials):
    return client.post(
        "/api/v1/auth/login",
        headers={"X-Channel-Gateway-Request": "1"},
        json={"username": "admin", "password": PASSWORD, **credentials},
    )


def test_login_restores_session_and_logout_revokes_copied_cookie(console):
    assert console.get("/api/v1/connections").status_code == 401
    assert console.get("/api/v1/auth/session").status_code == 401
    response = login(console)
    assert response.status_code == 200
    assert response.json()["username"] == "admin" and "csrf_token" in response.json()
    assert PASSWORD not in response.text
    cookie = console.cookies.get(COOKIE_NAME)
    assert cookie not in response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "Path=/api/v1" in response.headers["set-cookie"]
    assert console.get("/api/v1/auth/session").json() == response.json()
    assert console.get("/api/v1/connections").status_code == 200
    csrf = response.json()["csrf_token"]
    assert console.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert console.get("/api/v1/connections").status_code == 401
    console.cookies.set(COOKIE_NAME, cookie, path="/api/v1")
    assert console.get("/api/v1/auth/session").status_code == 401
    # Replaying the revoked session cannot regain management authority.
    assert (
        console.post(
            "/api/v1/connections", headers={"X-CSRF-Token": csrf}, json={"name": "MCP", "kind": "mcp"}
        ).status_code
        == 401
    )


def test_cookie_session_cannot_modify_without_csrf_or_from_another_origin(console):
    csrf = login(console).json()["csrf_token"]
    body = {"name": "Codex", "kind": "mcp"}
    for headers in ({}, {"X-CSRF-Token": "wrong"}, {"X-CSRF-Token": csrf, "Origin": "https://other.test"}):
        assert console.post("/api/v1/connections", json=body, headers=headers).status_code == 403
    assert console.get("/api/v1/auth/session", headers={"Origin": "https://other.test"}).status_code == 403
    assert console.get("/api/v1/connections").json() == []
    assert (
        console.post(
            "/api/v1/connections", json=body, headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"}
        ).status_code
        == 201
    )
    assert console.post("/api/v1/auth/logout").status_code == 403


def test_login_checks_origin_and_custom_header_before_creating_a_session(console):
    body = {"username": "admin", "password": PASSWORD}
    assert console.post("/api/v1/auth/login", json=body).status_code == 403
    assert (
        console.post(
            "/api/v1/auth/login", headers={"X-Channel-Gateway-Request": "1", "Origin": "https://other.test"}, json=body
        ).status_code
        == 403
    )
    assert console.cookies.get(COOKIE_NAME) is None
    assert console.get("/api/v1/connections").status_code == 401


def test_login_failures_do_not_disclose_values_and_are_rate_limited(console):
    for credentials in ({"username": "unknown"}, {"password": "wrong-password"}):
        response = login(console, **credentials)
        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect username or password"}
        assert console.cookies.get(COOKIE_NAME) is None
    malformed = login(console, password="validation-secret" * 300)
    assert malformed.status_code == 422 and "validation-secret" not in malformed.text
    for _ in range(3):
        assert login(console, password="wrong-password").status_code == 401
    limited = login(console)
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
    assert console.cookies.get(COOKIE_NAME) is None


def test_expiration_and_relogin_do_not_reuse_sessions(console, monkeypatch):
    from channel_gateway_server import auth

    csrf = login(console).json()["csrf_token"]
    original = console.cookies.get(COOKIE_NAME)
    assert login(console).json()["csrf_token"] != csrf
    replacement = console.cookies.get(COOKIE_NAME)
    assert replacement != original
    # A second login invalidates the original cookie even before expiry.
    assert console.get("/api/v1/auth/session", headers={"Cookie": f"{COOKIE_NAME}={original}"}).status_code == 401
    now = auth.monotonic()
    monkeypatch.setattr(auth, "monotonic", lambda: now + SESSION_SECONDS + 1)
    assert console.get("/api/v1/auth/session").status_code == 401
    assert console.get("/api/v1/connections").status_code == 401


def test_https_cookie_and_session_do_not_grant_mcp_access(tmp_path):
    settings = Settings(admin_username="admin", admin_password=PASSWORD, data_dir=tmp_path, worker_enabled=False)
    with TestClient(create_app(settings=settings), base_url="https://testserver") as client:
        response = login(client)
        assert "Secure" in response.headers["set-cookie"]
        assert client.post("/mcp/", json={}).status_code == 401
        assert client.get("/api/v1/connections").status_code == 200
        # Passwords and CSRF tokens are not MCP capability credentials.
        for credential in (PASSWORD, response.json()["csrf_token"]):
            assert client.post("/mcp/", headers={"Authorization": "Bearer " + credential}, json={}).status_code == 401
    with TestClient(create_app(settings=settings), base_url="https://testserver") as restarted:
        assert (
            restarted.get(
                "/api/v1/auth/session", headers={"Cookie": f"{COOKIE_NAME}={client.cookies.get(COOKIE_NAME)}"}
            ).status_code
            == 401
        )


def test_settings_use_only_the_three_ac_environment_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("CG_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("CG_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("CG_DATA_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert (
        settings.admin_username == "operator" and settings.admin_password == PASSWORD and settings.data_dir == tmp_path
    )
    assert PASSWORD not in repr(settings)
    monkeypatch.delenv("CG_ADMIN_USERNAME")
    monkeypatch.delenv("CG_ADMIN_PASSWORD")
    monkeypatch.setenv("CHANNEL_GATEWAY_ADMIN_TOKEN", "legacy-not-supported")
    monkeypatch.setenv("CG_ADMIN_TOKEN", "legacy-not-supported")
    with TestClient(create_app(settings=Settings.from_env())) as client:
        assert client.get("/api/v1/status").json()["management_configured"] is False
        assert client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 503
        assert (
            client.get("/api/v1/connections", headers={"Authorization": "Bearer legacy-not-supported"}).status_code
            == 503
        )


@pytest.mark.parametrize(
    "username,password", [("admin", None), (None, PASSWORD), ("admin", "x" * 7), ("admin", "x" * 4097)]
)
def test_invalid_administrator_configuration_fails_startup(username, password):
    with pytest.raises(ValueError):
        create_app(settings=Settings(admin_username=username, admin_password=password))


@pytest.mark.parametrize("url", ["http://127.0.0.1", "https://gateway.test"])
def test_eight_character_password_works_without_address_special_cases(tmp_path, url):
    settings = Settings(admin_username="admin", admin_password="12345678", data_dir=tmp_path, worker_enabled=False)
    with TestClient(create_app(settings=settings), base_url=url) as client:
        assert login(client, password="12345678").status_code == 200
        assert client.get("/api/v1/connections").status_code == 200
