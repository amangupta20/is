"""Admin session and token authentication for the management web interface."""

import hashlib
import hmac
import time
from typing import Final

from fastapi import HTTPException, Request, status

from assistant_core.config import Settings

SESSION_COOKIE_NAME: Final[str] = "assistant_admin_session"
DEFAULT_SESSION_TTL_SECONDS: Final[int] = 86400 * 7  # 7 days


def _get_signing_key(settings: Settings) -> str:
    """Return configured admin token or hmac secret for session signing."""
    if settings.admin_token is not None:
        return settings.admin_token.get_secret_value()
    return settings.hmac_secret


def create_admin_session_token(secret: str, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> str:
    """Create a signed timestamped session token."""
    expiry = int(time.time()) + ttl_seconds
    payload = f"admin:{expiry}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_admin_session_token(token: str, secret: str) -> bool:
    """Verify validity and signature of a session token."""
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "admin":
        return False

    try:
        expiry = int(parts[1])
    except ValueError:
        return False

    if time.time() > expiry:
        return False

    payload = f"admin:{expiry}"
    expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(parts[2], expected_sig)


def verify_admin_credentials(provided_token: str, settings: Settings) -> bool:
    """Validate a provided admin password or token against configuration."""
    expected = _get_signing_key(settings)
    return hmac.compare_digest(provided_token.strip(), expected.strip())


async def require_admin_session(request: Request) -> None:
    """FastAPI dependency requiring a valid admin session cookie or header."""
    settings: Settings = request.app.state.settings
    signing_key = _get_signing_key(settings)

    # 1. Check Authorization Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if verify_admin_session_token(token, signing_key) or verify_admin_credentials(
            token, settings
        ):
            return

    # 2. Check X-Admin-Token header
    admin_token_header = request.headers.get("X-Admin-Token")
    if admin_token_header and (
        verify_admin_session_token(admin_token_header, signing_key)
        or verify_admin_credentials(admin_token_header, settings)
    ):
        return

    # 3. Check session cookie
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie and verify_admin_session_token(cookie, signing_key):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="admin session required",
    )
