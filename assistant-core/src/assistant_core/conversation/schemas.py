"""Immutable internal values for bounded conversation passages."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ConversationRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class PassageDraft:
    """One exact bounded substring of a native conversation message."""

    role: ConversationRole
    role_order: int
    native_message_id: str
    chunk_ordinal: int
    content: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PassageMaterialization:
    """Content-free counts and IDs produced by lexical materialization."""

    new_segments: int
    reused_segments: int
    new_references: int
    missing_embedding_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class ConversationHit:
    """One owner-scoped conversation reference ranked for retrieval."""

    source_id: uuid.UUID
    content: str
    role: ConversationRole
    native_chat_id: str
    native_message_id: str
    occurred_at: datetime
    score: float


@dataclass(frozen=True, slots=True)
class ConversationNeighbor:
    """One bounded adjacent passage around a selected source."""

    role: ConversationRole
    content: str
    native_chat_id: str
    native_message_id: str


@dataclass(frozen=True, slots=True)
class ConversationRead:
    """A selected passage and at most its immediate active neighbors."""

    selected: ConversationHit
    neighbors: tuple[ConversationNeighbor, ...]


@dataclass(frozen=True, slots=True)
class TombstoneResult:
    """Content-free counts from one idempotent native-chat tombstone."""

    reference_count: int
    turn_count: int
    orphan_segment_count: int
