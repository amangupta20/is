"""Tests for the signed, no-op context endpoint."""

import time

import anyio
import httpx
from fastapi import FastAPI

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.main import create_app


async def post_context(app: FastAPI) -> httpx.Response:
    """Call the context endpoint with a valid signed request."""
    body = b'{"native_user_id":"user-1"}'
    timestamp = str(int(time.time()))
    signature = sign_request("a" * 32, "POST", "/v1/context", timestamp, body)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/context",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Assistant-Timestamp": timestamp,
                "X-Assistant-Signature": signature,
            },
        )


async def send_context(
    app: FastAPI,
    *,
    body: bytes = b'{"native_user_id":"user-1"}',
    timestamp: str | None = None,
    signature_method: str = "POST",
    signature_path: str = "/v1/context",
    signature_body: bytes | None = None,
) -> httpx.Response:
    """Call the context endpoint with controllable signature values."""
    signed_body = signature_body if signature_body is not None else body
    signed_timestamp = timestamp or str(int(time.time()))
    signature = sign_request(
        "a" * 32, signature_method, signature_path, signed_timestamp, signed_body
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(
            "/v1/context",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Assistant-Timestamp": signed_timestamp,
                "X-Assistant-Signature": signature,
            },
        )


def test_context_returns_the_empty_contract() -> None:
    """A valid signed request receives the deliberately empty context response."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(post_context, app)

    assert response.status_code == 200
    assert response.json() == {
        "context_text": "",
        "token_estimate": 0,
        "sources": [],
        "degraded": False,
    }


def test_context_default_budget_is_300000_in_the_exact_signed_payload() -> None:
    """Omitting max_tokens uses the policy default without changing the empty response."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(post_context, app)

    assert response.status_code == 200
    assert response.json() == {
        "context_text": "",
        "token_estimate": 0,
        "sources": [],
        "degraded": False,
    }


def test_context_rejects_missing_or_invalid_signatures() -> None:
    """Missing, body-modified, method-modified, and path-modified requests are unauthorized."""
    app = create_app(Settings(hmac_secret="a" * 32))

    async def requests() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            missing = await client.post("/v1/context", content=b'{"native_user_id":"user-1"}')
        changed_body = await send_context(
            app,
            body=b'{"native_user_id":"user-2"}',
            signature_body=b'{"native_user_id":"user-1"}',
        )
        changed_method = await send_context(app, signature_method="GET")
        changed_path = await send_context(app, signature_path="/other")
        return [missing, changed_body, changed_method, changed_path]

    responses = anyio.run(requests)

    assert [response.status_code for response in responses] == [401, 401, 401, 401]


def test_context_rejects_malformed_stale_and_future_timestamps() -> None:
    """Only current integer Unix timestamps are accepted."""
    app = create_app(Settings(hmac_secret="a" * 32, request_clock_skew_seconds=1))

    malformed = anyio.run(lambda: send_context(app, timestamp="not-a-timestamp"))
    stale = anyio.run(lambda: send_context(app, timestamp=str(int(time.time()) - 2)))
    future = anyio.run(lambda: send_context(app, timestamp=str(int(time.time()) + 2)))

    assert malformed.status_code == 401
    assert stale.status_code == 401
    assert future.status_code == 401


def test_context_validates_the_bounded_request_schema() -> None:
    """The endpoint disallows extra and out-of-range context request data."""
    app = create_app(Settings(hmac_secret="a" * 32))

    extra = anyio.run(lambda: send_context(app, body=b'{"native_user_id":"user-1","extra":true}'))
    empty_user = anyio.run(lambda: send_context(app, body=b'{"native_user_id":""}'))
    excessive_text = anyio.run(
        lambda: send_context(
            app,
            body=(b'{"native_user_id":"user-1","request_text":"' + b"x" * 16001 + b'"}'),
        )
    )
    excessive_tokens = anyio.run(
        lambda: send_context(app, body=b'{"native_user_id":"user-1","max_tokens":500001}')
    )
    negative_tokens = anyio.run(
        lambda: send_context(app, body=b'{"native_user_id":"user-1","max_tokens":-1}')
    )
    long_chat_id = anyio.run(
        lambda: send_context(
            app,
            body=(b'{"native_user_id":"user-1","native_chat_id":"' + b"c" * 201 + b'"}'),
        )
    )
    long_message_id = anyio.run(
        lambda: send_context(
            app,
            body=(b'{"native_user_id":"user-1","native_message_id":"' + b"m" * 201 + b'"}'),
        )
    )

    assert [
        response.status_code
        for response in [
            extra,
            empty_user,
            excessive_text,
            excessive_tokens,
            negative_tokens,
            long_chat_id,
            long_message_id,
        ]
    ] == [422, 422, 422, 422, 422, 422, 422]


def test_context_accepts_the_exact_configured_budget_cap() -> None:
    """The companion accepts the maximum configured temporary context budget."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(
        lambda: send_context(app, body=b'{"native_user_id":"user-1","max_tokens":500000}')
    )

    assert response.status_code == 200
