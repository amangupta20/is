"""Shared API dependencies."""

import time

from fastapi import HTTPException, Request, status

from assistant_core.auth.hmac import verify_request


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
