"""Source-linked explicit-memory ledger models."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class MemoryRecord(Base):
    """A durable explicit memory, retained across supersession."""

    __tablename__ = "memory_record"
    __table_args__ = (
        CheckConstraint(
            "category IN ('fact', 'preference', 'instruction', 'project', 'decision')",
            name="memory_record_category",
        ),
        CheckConstraint("kind = 'explicit'", name="memory_record_kind"),
        CheckConstraint("confidence = 1", name="memory_record_confidence"),
        CheckConstraint(
            "char_length(statement) <= 2000",
            name="memory_record_statement_length",
        ),
        CheckConstraint(
            "state IN ('active', 'superseded', 'archived')",
            name="memory_record_state",
        ),
        Index(
            "uq_memory_record_active_user_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_memory_record_user_id_key", "user_id", "key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="explicit", server_default="explicit")
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.memory_record.id"), nullable=True
    )


class MemoryEvidence(Base):
    """Exact user-message provenance for one explicit memory record."""

    __tablename__ = "memory_evidence"
    __table_args__ = (
        CheckConstraint(
            "char_length(evidence_quote) <= 1000",
            name="memory_evidence_evidence_quote_length",
        ),
        Index(
            "uq_memory_evidence_record_turn_quote",
            "memory_record_id",
            "completed_turn_id",
            "evidence_quote",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.memory_record.id"), nullable=False
    )
    completed_turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.completed_turn.id"), nullable=False
    )
    native_user_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatProfileSnapshot(Base):
    """Exact profile rendering for one native chat and its ordered memory sources."""

    __tablename__ = "chat_profile_snapshot"
    __table_args__ = (Index("uq_chat_profile_snapshot_user_chat", "user_id", "native_chat_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    native_chat_id: Mapped[str] = mapped_column(String(200), nullable=False)
    rendered_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
