"""Tests for public liveness."""

import anyio
import httpx
from fastapi import FastAPI

from assistant_core.config import Settings
from assistant_core.main import create_app


async def request_liveness(app: FastAPI) -> httpx.Response:
    """Call the application without a network listener."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/health/live")


def test_liveness_returns_only_ok_status() -> None:
    """Liveness is public and never exposes settings."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(request_liveness, app)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "hmac" not in response.text.lower()
