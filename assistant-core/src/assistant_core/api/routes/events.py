"""Signed event-ingestion HTTP route."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.events.schemas import EventAccepted, EventEnvelope
from assistant_core.events.service import ingest_event
from assistant_core.turns.schemas import OversizedTurnPayload

router = APIRouter()
MAX_COMPLETED_TURN_REQUEST_BYTES = 524_288
COMPLETED_TURN_TOO_LARGE_ERROR = "turn_completed_request_too_large"
INVALID_OVERSIZED_TURN_ERROR = "invalid_turn_oversized_payload"


def _looks_like_oversized_turn(document: dict[object, object]) -> bool:
    """Recognize an oversized marker even when its event type is malformed."""
    event_type = document.get("event_type")
    if event_type == "turn.oversized.v1":
        return True
    if isinstance(event_type, str) and event_type.startswith("turn.oversized"):
        return True

    payload = document.get("payload")
    if not isinstance(payload, dict):
        return False
    if payload.get("source") == "openwebui_outlet_filter":
        for key in ("user_message", "assistant_message"):
            message = payload.get(key)
            if isinstance(message, dict) and "content_bytes" in message:
                return True
    return False


async def require_turn_event_contract(request: Request) -> None:
    """Reject unsafe turn bodies after HMAC but before FastAPI body validation."""
    raw_body = await request.body()
    try:
        document = json.loads(raw_body)
    except (TypeError, ValueError):
        return
    if not isinstance(document, dict):
        return

    event_type = document.get("event_type")
    if event_type == "turn.completed.v1":
        if len(raw_body) > MAX_COMPLETED_TURN_REQUEST_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=COMPLETED_TURN_TOO_LARGE_ERROR,
            )
        return
    if not _looks_like_oversized_turn(document):
        return

    try:
        envelope = EventEnvelope.model_validate(document)
        if type(document.get("schema_version")) is not int:
            raise ValueError
        if envelope.event_type != "turn.oversized.v1":
            raise ValueError
        for field in (
            "event_id",
            "event_type",
            "native_user_id",
            "native_chat_id",
            "native_message_id",
            "occurred_at",
        ):
            raw_value = document.get(field)
            if type(raw_value) is not str or not raw_value.strip():
                raise ValueError
        payload = OversizedTurnPayload.model_validate(envelope.payload)
        if payload.assistant_message.id != envelope.native_message_id:
            raise ValueError
    except (TypeError, ValueError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=INVALID_OVERSIZED_TURN_ERROR,
        ) from None


async def _validate_turn_event(request: Request, event: EventEnvelope) -> None:
    """Enforce turn-specific trust boundaries before database persistence."""
    if event.event_type == "turn.completed.v1":
        if len(await request.body()) > MAX_COMPLETED_TURN_REQUEST_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=COMPLETED_TURN_TOO_LARGE_ERROR,
            )
        return

    if event.event_type != "turn.oversized.v1":
        return

    try:
        payload = OversizedTurnPayload.model_validate(event.payload)
        if not event.native_chat_id or not event.native_chat_id.strip():
            raise ValueError
        if not event.native_message_id or not event.native_message_id.strip():
            raise ValueError
        if payload.assistant_message.id != event.native_message_id:
            raise ValueError
    except (TypeError, ValueError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=INVALID_OVERSIZED_TURN_ERROR,
        ) from None


@router.post(
    "/v1/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventAccepted,
    dependencies=[
        Depends(require_adapter_signature),
        Depends(require_turn_event_contract),
    ],
)
async def accept_event(request: Request, event: EventEnvelope) -> EventAccepted:
    """Atomically persist a signed event and its queued processing job."""
    await _validate_turn_event(request, event)
    async with request.app.state.session_factory() as session, session.begin():
        duplicate = await ingest_event(session, event)
    return EventAccepted(event_id=event.event_id, duplicate=duplicate)
