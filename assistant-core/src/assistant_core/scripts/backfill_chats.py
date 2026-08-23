"""Backfill imported Open WebUI chats into durable turns, passages, and embeddings.

Reads chats from the Open WebUI API, walks each chat's active message branch,
pairs user/assistant messages into historical completed turns (deterministic
event ids make reruns idempotent), and enqueues conversation-index jobs only.
Memory extraction is deliberately skipped so history becomes searchable without
seeding spurious memories; topic episodes compile later via the normal sweep.

Usage:
    uv run python -m assistant_core.scripts.backfill_chats \
        --open-webui-url http://open-webui:8080 \
        --token "$OPEN_WEBUI_TOKEN" \
        [--native-user-id <id>] [--limit N] [--chat-id <id>] [--dry-run]
"""

import argparse
import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.config import Settings
from assistant_core.conversation.chunking import CHUNKING_VERSION
from assistant_core.db.session import create_database
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job
from assistant_core.turns.models import CompletedTurn

LOGGER = structlog.get_logger("assistant_core.scripts.backfill_chats")

EVENT_ID_PREFIX = "backfill:v1"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _message_timestamp(message: dict[str, Any], fallback: datetime) -> datetime:
    raw = message.get("timestamp")
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw, tz=UTC)
    return fallback


def active_branch_messages(history: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the chat's active branch ordered oldest-first via the currentId chain."""
    messages = history.get("messages")
    current_id = history.get("currentId")
    if not isinstance(messages, dict) or not isinstance(current_id, str):
        return []

    chain: list[dict[str, Any]] = []
    cursor: str | None = current_id
    seen: set[str] = set()
    while cursor and cursor not in seen and isinstance(messages.get(cursor), dict):
        seen.add(cursor)
        message = messages[cursor]
        if message.get("role") in ("user", "assistant"):
            chain.append(message)
        parent = message.get("parentId")
        cursor = parent if isinstance(parent, str) else None
    chain.reverse()
    return [m for m in chain if isinstance(m.get("content"), str) and m["content"].strip()]


def pair_completed_turns(
    ordered: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each user message with its immediately following assistant reply."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    index = 0
    while index < len(ordered):
        message = ordered[index]
        nxt = ordered[index + 1] if index + 1 < len(ordered) else None
        if message.get("role") == "user" and nxt is not None and nxt.get("role") == "assistant":
            pairs.append((message, nxt))
            index += 2
            continue
        index += 1
    return pairs


async def resolve_user_id(session: AsyncSession, native_user_id: str) -> uuid.UUID:
    """Resolve or create the owning UserIdentity for one native user id."""
    statement = select(UserIdentity).where(UserIdentity.native_user_id == native_user_id)
    existing = (await session.execute(statement)).scalar_one_or_none()
    if existing is not None:
        return existing.id
    user = UserIdentity(id=uuid.uuid4(), native_user_id=native_user_id)
    session.add(user)
    await session.flush()
    return user.id


async def backfill_turns_for_chat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    native_chat_id: str,
    history: dict[str, Any],
    chat_fallback_time: datetime,
) -> tuple[int, int]:
    """Insert missing turns and index jobs for one chat; returns (inserted, skipped)."""
    pairs = pair_completed_turns(active_branch_messages(history))
    inserted = 0
    skipped = 0
    for user_message, assistant_message in pairs:
        event_id = (
            f"{EVENT_ID_PREFIX}:{native_chat_id}:"
            f"{user_message.get('id', 'unknown')}:{assistant_message.get('id', 'unknown')}"
        )[:200]
        occurred_at = _message_timestamp(
            user_message,
            _message_timestamp(assistant_message, chat_fallback_time),
        )
        turn_statement = (
            insert(CompletedTurn)
            .values(
                id=uuid.uuid4(),
                event_id=event_id,
                user_id=user_id,
                native_chat_id=native_chat_id[:200],
                native_user_message_id=str(user_message.get("id", "unknown"))[:200],
                native_assistant_message_id=str(assistant_message.get("id", "unknown"))[:200],
                user_content=user_message["content"],
                assistant_content=assistant_message["content"],
                user_content_sha256=_sha256(user_message["content"]),
                assistant_content_sha256=_sha256(assistant_message["content"]),
                occurred_at=occurred_at,
            )
            .on_conflict_do_nothing(index_elements=[CompletedTurn.event_id])
            .returning(CompletedTurn.id)
        )
        turn_id = (await session.execute(turn_statement)).scalar_one_or_none()
        if turn_id is None:
            skipped += 1
            continue
        await session.execute(
            insert(Job)
            .values(
                id=uuid.uuid4(),
                identity_key=f"conversation:{turn_id}:{CHUNKING_VERSION}",
                kind="index_conversation",
                status="queued",
                payload={"turn_id": str(turn_id)},
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=[Job.identity_key])
        )
        inserted += 1
    return inserted, skipped


def fetch_chat_detail(client: httpx.Client, base_url: str, chat_id: str) -> dict[str, Any] | None:
    response = client.get(f"{base_url.rstrip('/')}/api/v1/chat/{chat_id}")
    if response.status_code != 200:
        LOGGER.warning("backfill_chat_fetch_failed", chat_id=chat_id, status=response.status_code)
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def list_chat_ids(client: httpx.Client, base_url: str) -> list[dict[str, Any]]:
    """List the token owner's chats, tolerating both list endpoints across versions."""
    response = client.get(f"{base_url.rstrip('/')}/api/v1/chats/list?page=1")
    if response.status_code != 200:
        response = client.get(f"{base_url.rstrip('/')}/api/v1/chats/")
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list):
        return [c for c in payload if isinstance(c, dict)]
    return []


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    base_url = args.open_webui_url or settings.open_webui_url
    token = args.token
    if not token and settings.open_webui_api_key:
        token = settings.open_webui_api_key.get_secret_value()
    if not token:
        raise SystemExit("an Open WebUI API token is required (--token or OPEN_WEBUI_API_KEY)")

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True) as client:
        summaries = list_chat_ids(client, base_url)
        if args.chat_id:
            summaries = [c for c in summaries if c.get("id") == args.chat_id]
        if args.limit and args.limit > 0:
            summaries = summaries[: args.limit]
        LOGGER.info("backfill_start", chat_count=len(summaries), dry_run=args.dry_run)

        engine, session_factory = create_database(settings.database_url)
        total_inserted = 0
        total_skipped = 0
        try:
            for summary in summaries:
                chat_id = str(summary.get("id") or "")
                if not chat_id:
                    continue
                detail = fetch_chat_detail(client, base_url, chat_id)
                if detail is None:
                    continue
                raw_inner = detail.get("chat")
                inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else {}
                raw_history = inner.get("history")
                history: dict[str, Any] = raw_history if isinstance(raw_history, dict) else {}
                updated_raw = detail.get("updated_at") or summary.get("updated_at")
                fallback_time = (
                    datetime.fromtimestamp(updated_raw, tz=UTC)
                    if isinstance(updated_raw, (int, float)) and updated_raw > 0
                    else datetime.now(UTC)
                )

                async with session_factory() as session:
                    native_user_id = str(detail.get("user_id") or args.native_user_id or "")
                    if not native_user_id.strip():
                        native_user_id = args.native_user_id or ""
                    if not native_user_id.strip():
                        raise SystemExit(
                            "could not resolve a native user id; pass --native-user-id"
                        )
                    user_id = await resolve_user_id(session, native_user_id)
                    if args.dry_run:
                        count = len(pair_completed_turns(active_branch_messages(history)))
                        print(f"[dry-run] {chat_id}: {count} turns would be backfilled")
                        await session.rollback()
                        continue
                    inserted, skipped = await backfill_turns_for_chat(
                        session,
                        user_id=user_id,
                        native_chat_id=chat_id,
                        history=history,
                        chat_fallback_time=fallback_time,
                    )
                    await session.commit()
                total_inserted += inserted
                total_skipped += skipped
                LOGGER.info(
                    "backfill_chat_done",
                    chat_id=chat_id,
                    title=str(summary.get("title") or ""),
                    inserted=inserted,
                    already_present=skipped,
                )
        finally:
            await engine.dispose()

    LOGGER.info(
        "backfill_complete",
        inserted=total_inserted,
        already_present=total_skipped,
    )
    print(f"Backfill complete: {total_inserted} turns indexed, {total_skipped} already present.")
    print("Embedding runs inside the worker as queued index jobs drain.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--open-webui-url", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--native-user-id", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--chat-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
