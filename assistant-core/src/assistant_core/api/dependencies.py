"""Shared API dependencies."""

import time

from fastapi import HTTPException, Request, status

from assistant_core.auth.hmac import verify_request
from assistant_core.auth.session import verify_session_token


def extract_session_token(request: Request) -> str | None:
    """Extract session token from x-assistant-session header or assistant_session cookie."""
    auth_header = request.headers.get("x-assistant-session")
    if auth_header:
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        return auth_header.strip()
    cookie_token = request.cookies.get("assistant_session")
    if cookie_token:
        return cookie_token.strip()
    return None


async def require_session_auth(request: Request) -> None:
    """Require a valid session token from header or cookie."""
    token = extract_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing session token"
        )
    settings = request.app.state.settings
    if not verify_session_token(token, settings.hmac_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired session token"
        )


async def require_adapter_signature(request: Request) -> None:
    """Require a current, valid HMAC signature from an Open WebUI adapter."""
    timestamp = request.headers.get("x-assistant-timestamp", "")
    signature = request.headers.get("x-assistant-signature", "")
    try:
        request_timestamp = int(timestamp)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid request timestamp"
        ) from exc

    settings = request.app.state.settings
    if abs(time.time() - request_timestamp) > settings.request_clock_skew_seconds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="expired request timestamp"
        )

    if not signature or not verify_request(
        settings.hmac_secret,
        signature,
        request.method,
        request.url.path,
        timestamp,
        await request.body(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid request signature"
        )


async def require_session_or_signature(request: Request) -> None:
    """Require either a valid session token OR a valid adapter HMAC signature."""
    settings = request.app.state.settings

    # 1. Check session auth
    session_token = extract_session_token(request)
    if session_token and verify_session_token(session_token, settings.hmac_secret):
        return

    # 2. Check HMAC signature auth
    timestamp = request.headers.get("x-assistant-timestamp", "")
    signature = request.headers.get("x-assistant-signature", "")
    if timestamp and signature:
        try:
            request_timestamp = int(timestamp)
            if abs(time.time() - request_timestamp) <= settings.request_clock_skew_seconds:
                body = await request.body()
                if verify_request(
                    settings.hmac_secret,
                    signature,
                    request.method,
                    request.url.path,
                    timestamp,
                    body,
                ):
                    return
        except ValueError:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required (valid session or signature)",
    )
