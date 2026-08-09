"""The signed, bounded companion context endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from assistant_core.api.dependencies import require_adapter_signature

router = APIRouter(prefix="/v1", tags=["context"])


class ContextRequest(BaseModel):
    """The bounded native-chat context request contract."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1)
    native_chat_id: str | None = Field(default=None, max_length=200)
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
async def assemble_context(_: ContextRequest) -> ContextResponse:
    """Return no context until the persistent memory layer is introduced."""
    return ContextResponse(context_text="", token_estimate=0, sources=[], degraded=False)
