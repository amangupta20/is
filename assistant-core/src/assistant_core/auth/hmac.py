"""Canonical request HMAC helpers."""

from __future__ import annotations

import hashlib
import hmac


def canonical_request(method: str, path: str, timestamp: str, body: bytes) -> str:
    """Build the exact string signed for an inbound request."""
    digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{digest}"


def sign_request(secret: str, method: str, path: str, timestamp: str, body: bytes) -> str:
    """Return a lowercase hexadecimal HMAC-SHA256 request signature."""
    request = canonical_request(method, path, timestamp, body)
    return hmac.new(secret.encode(), request.encode(), hashlib.sha256).hexdigest()


def verify_request(
    secret: str,
    signature: str,
    method: str,
    path: str,
    timestamp: str,
    body: bytes,
) -> bool:
    """Verify a request signature using constant-time comparison."""
    return hmac.compare_digest(signature, sign_request(secret, method, path, timestamp, body))
