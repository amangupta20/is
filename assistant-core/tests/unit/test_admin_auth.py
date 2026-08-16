"""Unit tests for admin authentication and session handling."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from assistant_core.auth.admin import (
    SESSION_COOKIE_NAME,
    create_admin_session_token,
    require_admin_session,
    verify_admin_credentials,
    verify_admin_session_token,
)
from assistant_core.config import Settings


def test_token_creation_and_verification() -> None:
    secret = "secret-token-key-123456789012345678"
    token = create_admin_session_token(secret, ttl_seconds=60)
    assert verify_admin_session_token(token, secret) is True
    assert verify_admin_session_token(token, "wrong-secret") is False
    assert verify_admin_session_token("invalid:format", secret) is False
    assert verify_admin_session_token("admin:invalid:sig", secret) is False


def test_token_expiration() -> None:
    secret = "secret-token-key-123456789012345678"
    expired_token = create_admin_session_token(secret, ttl_seconds=-10)
    assert verify_admin_session_token(expired_token, secret) is False


def test_verify_admin_credentials() -> None:
    settings = Settings(hmac_secret="primary-hmac-secret-12345678901234")
    assert verify_admin_credentials("primary-hmac-secret-12345678901234", settings) is True
    assert verify_admin_credentials("wrong-secret", settings) is False


def test_require_admin_session_dependency() -> None:
    secret = "my-admin-secret-123456789012345678"
    settings = Settings(hmac_secret=secret)

    app = FastAPI()
    app.state.settings = settings

    @app.get("/protected")
    async def protected_route(request: Request) -> JSONResponse:
        await require_admin_session(request)
        return JSONResponse({"status": "ok"})

    client = TestClient(app)

    # 1. Unauthenticated request -> 401
    resp = client.get("/protected")
    assert resp.status_code == 401

    # 2. Valid cookie -> 200
    valid_token = create_admin_session_token(secret, ttl_seconds=3600)
    client.cookies.set(SESSION_COOKIE_NAME, valid_token)
    resp = client.get("/protected")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    # 3. Valid Bearer header -> 200
    client.cookies.clear()
    resp = client.get("/protected", headers={"Authorization": f"Bearer {valid_token}"})
    assert resp.status_code == 200

    # 4. Valid raw secret in X-Admin-Token -> 200
    resp = client.get("/protected", headers={"X-Admin-Token": secret})
    assert resp.status_code == 200
