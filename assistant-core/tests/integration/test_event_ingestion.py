"""Live PostgreSQL tests for signed, idempotent event ingestion."""

import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.events.models import EventInbox
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.main import create_app

HMAC_SECRET = "codex-integration-hmac-secret-value"
TEST_DATABASE_URL_ENV = "ASSISTANT_TEST_DATABASE_URL"


def configured_database_url() -> str:
    """Return the explicitly configured live test URL without exposing it."""
    database_url = os.getenv(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is required for live integration tests")
    return database_url


def event_body(event_id: str, native_user_id: str) -> bytes:
    """Encode one valid test event."""
    return json.dumps(
        {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": "codex.test",
            "native_user_id": native_user_id,
            "native_chat_id": f"chat-{event_id}",
            "native_message_id": f"message-{event_id}",
            "occurred_at": "2026-08-09T12:00:00Z",
            "payload": {"source": "codex-integration"},
        },
        separators=(",", ":"),
    ).encode()


async def post_event(app: FastAPI, body: bytes, *, signed: bool = True) -> httpx.Response:
    """POST an event through the in-process application."""
    headers = {"Content-Type": "application/json"}
    if signed:
        timestamp = str(int(time.time()))
        headers.update(
            {
                "X-Assistant-Timestamp": timestamp,
                "X-Assistant-Signature": sign_request(
                    HMAC_SECRET, "POST", "/v1/events", timestamp, body
                ),
            }
        )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/v1/events", content=body, headers=headers)


async def record_counts(
    session_factory: async_sessionmaker[AsyncSession],
    event_id: str,
    native_user_id: str,
) -> tuple[int, int, int]:
    """Count only the exact test identity, event, and job rows."""
    async with session_factory() as session:
        identity_count = await session.scalar(
            select(func.count())
            .select_from(UserIdentity)
            .where(UserIdentity.native_user_id == native_user_id)
        )
        event_count = await session.scalar(
            select(func.count()).select_from(EventInbox).where(EventInbox.event_id == event_id)
        )
        job_count = await session.scalar(
            select(func.count()).select_from(Job).where(Job.identity_key == f"event:{event_id}")
        )
    return int(identity_count or 0), int(event_count or 0), int(job_count or 0)


async def cleanup_exact_rows(
    session_factory: async_sessionmaker[AsyncSession],
    event_id: str,
    native_user_id: str,
) -> None:
    """Delete only rows created for this unique test and prove they are gone."""
    async with session_factory() as session, session.begin():
        await session.execute(delete(Job).where(Job.identity_key == f"event:{event_id}"))
        await session.execute(delete(EventInbox).where(EventInbox.event_id == event_id))
        await session.execute(
            delete(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
        )
    assert await record_counts(session_factory, event_id, native_user_id) == (0, 0, 0)


@asynccontextmanager
async def app_context() -> AsyncIterator[FastAPI]:
    """Yield a live-database app and always dispose its engine."""
    app = create_app(Settings(database_url=configured_database_url(), hmac_secret=HMAC_SECRET))
    try:
        yield app
    finally:
        await app.state.engine.dispose()


async def exercise_idempotent_delivery(event_id: str, native_user_id: str) -> None:
    """Exercise first delivery and replay against the live schema."""
    async with app_context() as app:
        try:
            body = event_body(event_id, native_user_id)
            first = await post_event(app, body)
            duplicate = await post_event(app, body)

            assert first.status_code == 202
            assert first.json() == {"event_id": event_id, "duplicate": False}
            assert duplicate.status_code == 202
            assert duplicate.json() == {"event_id": event_id, "duplicate": True}
            assert await record_counts(app.state.session_factory, event_id, native_user_id) == (
                1,
                1,
                1,
            )
        finally:
            await cleanup_exact_rows(app.state.session_factory, event_id, native_user_id)


def test_live_event_ingestion_is_idempotent_and_enqueues_once() -> None:
    """A replay leaves exactly one identity, inbox row, and process-event job."""
    suffix = uuid.uuid4().hex
    anyio.run(
        exercise_idempotent_delivery,
        f"codex-test-event-{suffix}",
        f"codex-test-user-{suffix}",
    )


async def exercise_rejections(event_id: str, native_user_id: str) -> None:
    """Exercise unsigned and invalid signed requests against the live app."""
    async with app_context() as app:
        try:
            body = event_body(event_id, native_user_id)
            unsigned = await post_event(app, body, signed=False)
            invalid_body = json.loads(body)
            invalid_body["schema_version"] = 2
            invalid = await post_event(
                app,
                json.dumps(invalid_body, separators=(",", ":")).encode(),
            )

            assert unsigned.status_code == 401
            assert invalid.status_code == 422
            assert await record_counts(app.state.session_factory, event_id, native_user_id) == (
                0,
                0,
                0,
            )
        finally:
            await cleanup_exact_rows(app.state.session_factory, event_id, native_user_id)


def test_live_unsigned_and_invalid_requests_create_no_records() -> None:
    """Authentication and validation failures do not persist identities, events, or jobs."""
    suffix = uuid.uuid4().hex
    anyio.run(
        exercise_rejections,
        f"codex-test-event-{suffix}",
        f"codex-test-user-{suffix}",
    )
