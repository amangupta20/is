"""Unit tests for password authentication, session token lifecycle, and dual-auth dependencies."""

import hashlib
import hmac
import json
import time

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from assistant_core.api.dependencies import (
    require_session_or_signature,
)
from assistant_core.auth.session import create_session_token, verify_session_token
from assistant_core.config import Settings
from assistant_core.main import create_app

TEST_SECRET = "unit-test-auth-secret-key-12345"


def _signed_headers(
    secret: str, method: str, path: str, body: bytes, timestamp: int | None = None
) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}\n{path}\n{ts}\n{digest}".encode()
    signature = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return {
        "content-type": "application/json",
        "x-assistant-timestamp": ts,
        "x-assistant-signature": signature,
    }


def test_login_success_with_default_hmac_secret_sets_cookie_and_returns_token() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    response = client.post("/v1/auth/login", json={"password": TEST_SECRET})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "token" in data
    token = data["token"]
    assert verify_session_token(token, TEST_SECRET) is True

    # Check cookie in response
    assert "assistant_session" in response.cookies
    assert response.cookies["assistant_session"] == token
    assert "Max-Age=604800" in response.headers.get("set-cookie", "")
    assert "HttpOnly" in response.headers.get("set-cookie", "")


def test_login_success_with_explicit_admin_password() -> None:
    settings = Settings(hmac_secret=TEST_SECRET, admin_password="custom-super-secret-password")
    app = create_app(settings)
    client = TestClient(app)

    # hmac_secret should no longer work when admin_password is set
    res_hmac = client.post("/v1/auth/login", json={"password": TEST_SECRET})
    assert res_hmac.status_code == 401

    # admin_password should work
    res_admin = client.post(
        "/v1/auth/login", json={"password": "custom-super-secret-password"}
    )
    assert res_admin.status_code == 200
    assert res_admin.json()["status"] == "ok"
    assert "token" in res_admin.json()


def test_login_invalid_password_returns_401() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    response = client.post("/v1/auth/login", json={"password": "wrong-password"})
    assert response.status_code == 401
    assert "invalid password" in response.json()["detail"]


def test_login_validation_errors() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    # Empty password
    res_empty = client.post("/v1/auth/login", json={"password": ""})
    assert res_empty.status_code == 422

    # Extra fields forbidden
    res_extra = client.post(
        "/v1/auth/login", json={"password": TEST_SECRET, "extra": "not-allowed"}
    )
    assert res_extra.status_code == 422


def test_check_auth_with_session_header() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    token = create_session_token(TEST_SECRET)
    res = client.get("/v1/auth/check", headers={"x-assistant-session": token})
    assert res.status_code == 200
    assert res.json() == {"authenticated": True}


def test_check_auth_with_session_cookie() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    token = create_session_token(TEST_SECRET)
    res = client.get("/v1/auth/check", headers={"cookie": f"assistant_session={token}"})
    assert res.status_code == 200
    assert res.json() == {"authenticated": True}


def test_check_auth_with_bearer_session_header() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    token = create_session_token(TEST_SECRET)
    res = client.get("/v1/auth/check", headers={"x-assistant-session": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json() == {"authenticated": True}


def test_check_auth_missing_or_invalid_returns_401() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    # Missing auth
    res_missing = client.get("/v1/auth/check")
    assert res_missing.status_code == 401

    # Invalid token format
    res_invalid = client.get("/v1/auth/check", headers={"x-assistant-session": "not-a-valid-token"})
    assert res_invalid.status_code == 401

    # Tampered signature
    parts = create_session_token(TEST_SECRET).split(".")
    tampered = f"{parts[0]}.{'0' * len(parts[1])}"
    res_tampered = client.get("/v1/auth/check", headers={"x-assistant-session": tampered})
    assert res_tampered.status_code == 401


def test_check_auth_expired_session_returns_401() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    # 8 days ago (> 7 days)
    old_ts = int(time.time()) - (8 * 24 * 3600)
    expired_token = create_session_token(TEST_SECRET, timestamp=old_ts)
    res = client.get("/v1/auth/check", headers={"x-assistant-session": expired_token})
    assert res.status_code == 401


def test_logout_clears_cookie() -> None:
    settings = Settings(hmac_secret=TEST_SECRET)
    app = create_app(settings)
    client = TestClient(app)

    # 1. Login to set cookie in client session
    login_res = client.post("/v1/auth/login", json={"password": TEST_SECRET})
    assert login_res.status_code == 200
    assert client.cookies.get("assistant_session") is not None

    # 2. Check session works via cookie
    check_res = client.get("/v1/auth/check")
    assert check_res.status_code == 200

    # 3. Logout
    logout_res = client.post("/v1/auth/logout")
    assert logout_res.status_code == 200
    assert logout_res.json() == {"status": "ok"}
    assert "Max-Age=0" in logout_res.headers.get("set-cookie", "")

    # 4. Cookie should now be cleared
    assert client.cookies.get("assistant_session") is None

    # 5. Subsequent check without auth fails
    check_after_logout = client.get("/v1/auth/check")
    assert check_after_logout.status_code == 401


def test_session_token_verification_helpers() -> None:
    token = create_session_token(TEST_SECRET)
    assert verify_session_token(token, TEST_SECRET) is True
    assert verify_session_token(token, "wrong-secret") is False
    assert verify_session_token("", TEST_SECRET) is False
    assert verify_session_token("invalid", TEST_SECRET) is False
    assert verify_session_token("notanumber.signature", TEST_SECRET) is False

    # Future token beyond clock skew
    future_ts = int(time.time()) + 1000
    future_token = create_session_token(TEST_SECRET, timestamp=future_ts)
    assert verify_session_token(future_token, TEST_SECRET) is False


def test_require_session_or_signature_dependency() -> None:
    test_app = FastAPI()
    settings = Settings(hmac_secret=TEST_SECRET)
    test_app.state.settings = settings

    @test_app.post("/protected", dependencies=[Depends(require_session_or_signature)])
    async def protected_route() -> dict[str, str]:
        return {"status": "authorized"}

    client = TestClient(test_app)
    body = json.dumps({"test": "data"}).encode()

    # 1. Unauthenticated request -> 401
    res_unauth = client.post("/protected", content=body)
    assert res_unauth.status_code == 401

    # 2. Session header -> 200
    session_token = create_session_token(TEST_SECRET)
    res_session = client.post(
        "/protected", content=body, headers={"x-assistant-session": session_token}
    )
    assert res_session.status_code == 200
    assert res_session.json() == {"status": "authorized"}

    # 3. Session cookie -> 200
    res_cookie = client.post(
        "/protected", content=body, headers={"cookie": f"assistant_session={session_token}"}
    )
    assert res_cookie.status_code == 200
    assert res_cookie.json() == {"status": "authorized"}

    # 4. HMAC signature header -> 200
    hmac_headers = _signed_headers(TEST_SECRET, "POST", "/protected", body)
    res_hmac = client.post("/protected", content=body, headers=hmac_headers)
    assert res_hmac.status_code == 200
    assert res_hmac.json() == {"status": "authorized"}

    # 5. Invalid HMAC signature -> 401
    bad_hmac_headers = dict(hmac_headers)
    bad_hmac_headers["x-assistant-signature"] = "0" * 64
    res_bad_hmac = client.post("/protected", content=body, headers=bad_hmac_headers)
    assert res_bad_hmac.status_code == 401
