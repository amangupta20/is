"""Event inbox persistence model."""

import uuid
from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class EventInbox(Base):
    """A durable native event accepted for processing."""

    __tablename__ = "event_inbox"

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistant_core.user_identity.id"),
        nullable=False,
    )
    native_chat_id: Mapped[str | None] = mapped_column(String(200))
    native_project_id: Mapped[str | None] = mapped_column(String(200))
    native_folder_id: Mapped[str | None] = mapped_column(String(200))
    native_message_id: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    payload: Mapped[dict[str, JsonValue]] = mapped_column(JSONB, nullable=False)
