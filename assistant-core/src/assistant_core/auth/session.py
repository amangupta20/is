"""Session token primitives for assistant-core."""

from __future__ import annotations

import hashlib
import hmac
import time

SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days
CLOCK_SKEW_SECONDS = 60


def create_session_token(secret: str, timestamp: int | None = None) -> str:
    """Generate a signed cryptographic session token."""
    ts = int(time.time()) if timestamp is None else timestamp
    payload = f"session:{ts}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{signature}"


def verify_session_token(
    token: str,
    secret: str,
    max_age_seconds: int = SESSION_MAX_AGE_SECONDS,
    clock_skew_seconds: int = CLOCK_SKEW_SECONDS,
) -> bool:
    """Verify a signed cryptographic session token using constant-time comparison."""
    if not token or not secret:
        return False
    parts = token.strip().split(".")
    if len(parts) != 2:
        return False
    ts_str, signature = parts
    try:
        ts = int(ts_str)
    except ValueError:
        return False

    current_time = int(time.time())
    if current_time - ts > max_age_seconds:
        return False
    if ts > current_time + clock_skew_seconds:
        return False

    payload = f"session:{ts_str}"
    expected_signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected_signature)
