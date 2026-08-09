"""Durable completed-turn persistence model."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class CompletedTurn(Base):
    """One strictly validated, completed native chat turn."""

    __tablename__ = "completed_turn"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
    )
    native_chat_id: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )
    native_user_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    native_assistant_message_id: Mapped[str] = mapped_column(
        String(200), nullable=False
    )
    user_content: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_content: Mapped[str] = mapped_column(Text, nullable=False)
    user_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    assistant_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
