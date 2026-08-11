"""Idempotent persistence for strictly validated completed turns."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.events.models import EventInbox
from assistant_core.turns.models import CompletedTurn
from assistant_core.turns.schemas import CompletedTurnPayload

INVALID_TURN_PAYLOAD_ERROR = "invalid_turn_payload"


class InvalidTurnPayloadError(ValueError):
    """Raised without payload details when completed-turn data is invalid."""


def _validated_payload(event: EventInbox) -> CompletedTurnPayload:
    """Validate payload and envelope provenance behind one safe error boundary."""
    try:
        payload = CompletedTurnPayload.model_validate(event.payload)
        if event.event_type != "turn.completed.v1":
            raise ValueError
        if not event.native_chat_id or not event.native_chat_id.strip():
            raise ValueError
        if not event.native_message_id or not event.native_message_id.strip():
            raise ValueError
        if payload.assistant_message.id != event.native_message_id:
            raise ValueError
    except (TypeError, ValueError, ValidationError):
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR) from None
    return payload


async def materialize_completed_turn(
    session: AsyncSession, event: EventInbox
) -> bool:
    """Insert a completed turn once, returning false for an event replay."""
    payload = _validated_payload(event)
    statement = (
        insert(CompletedTurn)
        .values(
            event_id=event.event_id,
            user_id=event.user_id,
            native_chat_id=event.native_chat_id,
            native_user_message_id=payload.user_message.id,
            native_assistant_message_id=payload.assistant_message.id,
            user_content=payload.user_message.content,
            assistant_content=payload.assistant_message.content,
            user_content_sha256=payload.user_message.sha256,
            assistant_content_sha256=payload.assistant_message.sha256,
            occurred_at=event.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[CompletedTurn.event_id])
        .returning(CompletedTurn.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    return inserted_id is not None


async def get_completed_turn_for_event(
    session: AsyncSession, event_id: str
) -> CompletedTurn:
    """Load the completed turn for one already-validated inbox event."""
    statement = select(CompletedTurn).where(CompletedTurn.event_id == event_id)
    turn = (await session.execute(statement)).scalar_one_or_none()
    if not isinstance(turn, CompletedTurn):
        raise InvalidTurnPayloadError(INVALID_TURN_PAYLOAD_ERROR)
    return turn
