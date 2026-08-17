"""The signed, bounded companion context endpoint."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.memory.profile import get_or_create_profile

router = APIRouter(prefix="/v1", tags=["context"])


class ContextRequest(BaseModel):
    """The bounded native-chat context request contract."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_project_id: str | None = Field(default=None, max_length=200)
    native_folder_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)
    request_text: str = Field(default="", max_length=16_000)
    max_tokens: int = Field(default=300_000, ge=0, le=500_000)


class ContextSource(BaseModel):
    """A source used by a future non-empty context response."""

    source_type: str
    source_id: str
    label: str


class ContextResponse(BaseModel):
    """The stable context response envelope."""

    context_text: str
    token_estimate: int
    sources: list[ContextSource]
    degraded: bool


@router.post(
    "/context",
    response_model=ContextResponse,
    dependencies=[Depends(require_adapter_signature)],
)
async def assemble_context(body: ContextRequest, request: Request) -> ContextResponse:
    """Return one cache-stable profile snapshot for a saved native chat."""
    chat_id = body.native_chat_id
    if (
        chat_id is None
        or not chat_id.strip()
        or chat_id.startswith(("temporary:", "local:", "channel:"))
    ):
        return ContextResponse(context_text="", token_estimate=0, sources=[], degraded=False)

    try:
        async with request.app.state.session_factory() as session, session.begin():
            snapshot = await get_or_create_profile(
                session,
                native_user_id=body.native_user_id,
                native_chat_id=chat_id,
            )
    except Exception:  # noqa: BLE001 - optional profile context must fail open.
        return ContextResponse(context_text="", token_estimate=0, sources=[], degraded=True)

    return ContextResponse(
        context_text=snapshot.rendered_text,
        token_estimate=(len(snapshot.rendered_text) + 3) // 4,
        sources=[
            ContextSource(
                source_type="memory",
                source_id=str(source_id),
                label="profile",
            )
            for source_id in snapshot.source_memory_ids
        ],
        degraded=False,
    )
