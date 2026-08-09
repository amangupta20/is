"""Transactional event ingestion service."""

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.events.models import EventInbox
from assistant_core.events.schemas import EventEnvelope
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job


async def ingest_event(session: AsyncSession, event: EventEnvelope) -> bool:
    """Persist an event and report whether it was already present."""
    identity_insert = insert(UserIdentity).values(
        id=uuid.uuid4(),
        native_user_id=event.native_user_id,
    )
    identity_upsert = identity_insert.on_conflict_do_update(
        index_elements=[UserIdentity.native_user_id],
        set_={"native_user_id": identity_insert.excluded.native_user_id},
    ).returning(UserIdentity.id)
    identity_id = (await session.execute(identity_upsert)).scalar_one()

    inbox_insert = (
        insert(EventInbox)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            user_id=identity_id,
            native_chat_id=event.native_chat_id,
            native_message_id=event.native_message_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )
        .on_conflict_do_nothing(index_elements=[EventInbox.event_id])
        .returning(EventInbox.event_id)
    )
    inserted_event_id = (await session.execute(inbox_insert)).scalar_one_or_none()
    if inserted_event_id is None:
        return True

    await session.execute(
        insert(Job)
        .values(
            id=uuid.uuid4(),
            identity_key=f"event:{event.event_id}",
            kind="process_event",
            status="queued",
            payload={"event_id": event.event_id},
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[Job.identity_key])
    )
    return False
