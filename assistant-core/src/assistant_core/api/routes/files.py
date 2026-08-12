"""Signed file materialization for Library uploads."""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.conversation.embedder import get_conversation_embedder
from assistant_core.files.repository import materialize_file
from assistant_core.identity.models import UserIdentity

router = APIRouter(prefix="/v1/files", tags=["files"])
LOGGER = structlog.get_logger("assistant_core.files")


class FileMaterializeRequest(BaseModel):
    """Per-user file content to index."""

    model_config = ConfigDict(extra="forbid")

    native_user_id: str = Field(min_length=1, max_length=200)
    native_file_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=500000)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)


class FileMaterializeResponse(BaseModel):
    """Metadata-only materialization result."""

    model_config = ConfigDict(extra="forbid")

    native_file_id: str
    new_segments: int
    reused_segments: int
    new_references: int
    embedded: int = 0


@router.post(
    "/materialize",
    dependencies=[Depends(require_adapter_signature)],
    response_model=FileMaterializeResponse,
)
async def materialize_file_route(
    body: FileMaterializeRequest, request: Request
) -> FileMaterializeResponse:
    """Hash, chunk and store a file without exposing its text in logs."""
    async with request.app.state.session_factory() as session:
        # Resolve or create user
        from sqlalchemy.dialects.postgresql import insert

        ident_insert = (
            insert(UserIdentity)
            .values(native_user_id=body.native_user_id)
            .on_conflict_do_update(
                index_elements=[UserIdentity.native_user_id],
                set_={"native_user_id": body.native_user_id},
            )
            .returning(UserIdentity.id)
        )
        user_id = (await session.execute(ident_insert)).scalar_one()

        counts = await materialize_file(
            session,
            user_id=user_id,
            native_file_id=body.native_file_id,
            content=body.content,
        )
        await session.commit()

        # Try to embed missing segments synchronously, fail open to lexical
        embedded = 0
        try:
            from sqlalchemy import select as sel

            from assistant_core.files.models import FileSegment

            # Find segments for this file that lack embedding
            seg_ids = (
                await session.execute(
                    sel(FileSegment.id).where(
                        FileSegment.user_id == user_id,
                        FileSegment.embedding.is_(None),
                    )
                )
            ).scalars().all()
            # Limit to first few for this request to keep latency bounded
            for seg_id in list(seg_ids)[:4]:
                content = (
                    await session.execute(
                        sel(FileSegment.content).where(FileSegment.id == seg_id)
                    )
                ).scalar_one_or_none()
                if content is None:
                    continue
                try:
                    embedder = get_conversation_embedder(request.app.state.settings)
                    vec = embedder.embed_one(content)
                    from assistant_core.files.models import FileSegment as FS

                    await session.execute(
                        select(FS).where(FS.id == seg_id)
                    )
                    # Use direct update for file segment
                    from sqlalchemy import update

                    await session.execute(
                        update(FileSegment)
                        .where(FileSegment.id == seg_id)
                        .values(
                            embedding=vec,
                            embedding_model=request.app.state.settings.embedding_model,
                            embedding_dimension=request.app.state.settings.embedding_dimension,
                            embedding_version="openai-compatible-v1",
                            embedded_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()
                    embedded += 1
                except Exception:  # noqa: BLE001 - embedding fail open
                    await session.rollback()
                    continue
        except Exception:  # noqa: BLE001, S110 - top-level file materialize must not affect response
            pass

        LOGGER.info(
            "file_materialize_completed",
            native_user_id=body.native_user_id,
            native_file_id=body.native_file_id,
            new_segments=counts["new_segments"],
            reused_segments=counts["reused_segments"],
            new_references=counts["new_references"],
            embedded=embedded,
        )
        return FileMaterializeResponse(
            native_file_id=body.native_file_id,
            new_segments=counts["new_segments"],
            reused_segments=counts["reused_segments"],
            new_references=counts["new_references"],
            embedded=embedded,
        )
