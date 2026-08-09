"""Signed event-ingestion HTTP route."""

from fastapi import APIRouter, Depends, Request, status

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.events.schemas import EventAccepted, EventEnvelope
from assistant_core.events.service import ingest_event

router = APIRouter()


@router.post(
    "/v1/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EventAccepted,
    dependencies=[Depends(require_adapter_signature)],
)
async def accept_event(request: Request, event: EventEnvelope) -> EventAccepted:
    """Atomically persist a signed event and its queued processing job."""
    async with request.app.state.session_factory() as session, session.begin():
        duplicate = await ingest_event(session, event)
    return EventAccepted(event_id=event.event_id, duplicate=duplicate)
