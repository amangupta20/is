"""Signed, redacted operational status endpoint."""

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.jobs.models import Job

router = APIRouter(prefix="/v1")


class StatusRequest(BaseModel):
    """Stable native identity accompanying a status request."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)


class StatusResponse(BaseModel):
    """Intentionally narrow instance-wide queue summary."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    queued_jobs: int = Field(ge=0)
    dead_jobs: int = Field(ge=0)


@router.post(
    "/status",
    dependencies=[Depends(require_adapter_signature)],
    response_model=StatusResponse,
)
async def status(request: Request, _body: StatusRequest) -> StatusResponse:
    """Return aggregate queued and dead job counts without stored data."""
    async with request.app.state.session_factory() as session:
        queued_result = await session.execute(
            select(func.count()).select_from(Job).where(Job.status == "queued")
        )
        dead_result = await session.execute(
            select(func.count()).select_from(Job).where(Job.status == "dead")
        )
    return StatusResponse(
        status="ok",
        queued_jobs=queued_result.scalar_one(),
        dead_jobs=dead_result.scalar_one(),
    )
