"""Deterministic paragraph-first splitting of captured native messages."""

from hashlib import sha256

from assistant_core.conversation.schemas import PassageDraft
from assistant_core.turns.models import CompletedTurn

MAX_PASSAGE_CHARS = 4_000
PASSAGE_OVERLAP_CHARS = 400
CHUNKING_VERSION = "conversation-v1"


def chunk_message(text: str) -> tuple[str, ...]:
    """Return exact nonblank substrings bounded for retrieval and embedding."""
    if not text.strip():
        return ()
    if len(text) <= MAX_PASSAGE_CHARS:
        return (text,)

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + MAX_PASSAGE_CHARS, len(text))
        if end < len(text):
            paragraph_end = text.rfind("\n\n", start + PASSAGE_OVERLAP_CHARS, end)
            if paragraph_end >= 0:
                end = paragraph_end + 2
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - PASSAGE_OVERLAP_CHARS)
    return tuple(chunks)


def _message_passages(
    *,
    role: str,
    role_order: int,
    native_message_id: str,
    content: str,
) -> tuple[PassageDraft, ...]:
    """Attach stable message provenance and exact hashes to bounded chunks."""
    return tuple(
        PassageDraft(
            role="user" if role == "user" else "assistant",
            role_order=role_order,
            native_message_id=native_message_id,
            chunk_ordinal=chunk_ordinal,
            content=chunk,
            content_sha256=sha256(chunk.encode("utf-8")).hexdigest(),
        )
        for chunk_ordinal, chunk in enumerate(chunk_message(content))
    )


def build_turn_passages(turn: CompletedTurn) -> tuple[PassageDraft, ...]:
    """Build deterministic role-labelled passages for one completed turn."""
    return _message_passages(
        role="user",
        role_order=0,
        native_message_id=turn.native_user_message_id,
        content=turn.user_content,
    ) + _message_passages(
        role="assistant",
        role_order=1,
        native_message_id=turn.native_assistant_message_id,
        content=turn.assistant_content,
    )
