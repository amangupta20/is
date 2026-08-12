"""Tests for the signed, no-op context endpoint."""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Self

import anyio
import httpx
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from assistant_core.api.routes.context import ContextRequest
from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.main import create_app
from assistant_core.memory.models import ChatProfileSnapshot, MemoryRecord


class _ProfileResult:
    """Return one prearranged scalar or scalar collection."""

    def __init__(self, value: object = None, values: list[object] | None = None) -> None:
        self.value = value
        self.values = values or []

    def scalar_one(self) -> object:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> object | None:
        return self.value

    def scalars(self) -> "_ProfileResult":
        return self

    def all(self) -> list[object]:
        return self.values


class _ProfileSession:
    """Provide deterministic results for three profile requests."""

    def __init__(self, results: list[_ProfileResult]) -> None:
        self.results = results
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _ProfileResult:
        self.statements.append(statement)
        return self.results.pop(0)

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


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


def test_context_freezes_one_profile_per_chat_and_new_chat_sees_later_memory() -> None:
    """A chat reuses exact bytes while a new chat receives current eligible memory."""
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    first_memory = MemoryRecord(
        id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        user_id=user_id,
        key="style.response",
        category="preference",
        statement="Use direct answers.",
    )
    later_memory = MemoryRecord(
        id=uuid.UUID("00000000-0000-0000-0000-000000000012"),
        user_id=user_id,
        key="style.explanations",
        category="instruction",
        statement="Explain technical decisions in detail.",
    )
    chat_one = ChatProfileSnapshot(
        id=uuid.UUID("00000000-0000-0000-0000-000000000021"),
        user_id=user_id,
        native_chat_id="chat-1",
        rendered_text="<user_profile>\n- Use direct answers.\n</user_profile>",
        source_memory_ids=[first_memory.id],
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    chat_two = ChatProfileSnapshot(
        id=uuid.UUID("00000000-0000-0000-0000-000000000022"),
        user_id=user_id,
        native_chat_id="chat-2",
        rendered_text=(
            "<user_profile>\n- Explain technical decisions in detail.\n"
            "- Use direct answers.\n</user_profile>"
        ),
        source_memory_ids=[later_memory.id, first_memory.id],
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    session = _ProfileSession(
        [
            _ProfileResult(user_id),
            _ProfileResult(None),
            _ProfileResult(values=[first_memory]),
            _ProfileResult(chat_one),
            _ProfileResult(user_id),
            _ProfileResult(chat_one),
            _ProfileResult(user_id),
            _ProfileResult(None),
            _ProfileResult(values=[later_memory, first_memory]),
            _ProfileResult(chat_two),
        ]
    )
    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.session_factory = lambda: session

    first = anyio.run(
        lambda: send_context(
            app,
            body=b'{"native_user_id":"user-1","native_chat_id":"chat-1"}',
        )
    )
    repeated = anyio.run(
        lambda: send_context(
            app,
            body=b'{"native_user_id":"user-1","native_chat_id":"chat-1"}',
        )
    )
    second_chat = anyio.run(
        lambda: send_context(
            app,
            body=b'{"native_user_id":"user-1","native_chat_id":"chat-2"}',
        )
    )

    assert first.json() == repeated.json()
    assert first.json()["context_text"] == chat_one.rendered_text
    assert first.json()["sources"] == [
        {"source_type": "memory", "source_id": str(first_memory.id), "label": "profile"}
    ]
    assert second_chat.json()["context_text"] == chat_two.rendered_text
    assert [source["source_id"] for source in second_chat.json()["sources"]] == [
        str(later_memory.id),
        str(first_memory.id),
    ]
    first_snapshot_insert = session.statements[3].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    second_snapshot_insert = session.statements[9].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    first_memory_query = session.statements[2].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    second_memory_query = session.statements[8].compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    assert "completed_turn.tombstoned_at IS NULL" in str(first_memory_query)
    assert "completed_turn.tombstoned_at IS NULL" in str(second_memory_query)
    assert first_snapshot_insert.params["rendered_text"] == chat_one.rendered_text
    assert first_snapshot_insert.params["source_memory_ids"] == [first_memory.id]
    assert second_snapshot_insert.params["rendered_text"] == chat_two.rendered_text
    assert second_snapshot_insert.params["source_memory_ids"] == [
        later_memory.id,
        first_memory.id,
    ]
    assert session.results == []


def test_context_request_defaults_to_the_configured_300000_budget() -> None:
    """Omitting max_tokens parses to the policy budget before endpoint handling."""
    request = ContextRequest.model_validate({"native_user_id": "user-1"})

    assert request.max_tokens == 300_000


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
