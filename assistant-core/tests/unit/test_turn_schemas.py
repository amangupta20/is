"""Strict validation contracts for completed-turn payloads."""

from copy import deepcopy
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from assistant_core.turns.schemas import CompletedTurnPayload


def digest(content: str) -> str:
    """Hash exact UTF-8 message content."""
    return sha256(content.encode("utf-8")).hexdigest()


def valid_payload() -> dict[str, Any]:
    """Return a fresh payload containing Unicode and an empty assistant response."""
    user_content = "  नमस्ते, café 👋  "
    assistant_content = ""
    return {
        "source": "openwebui_outlet_filter",
        "user_message": {
            "id": "user-message-1",
            "role": "user",
            "content": user_content,
            "sha256": digest(user_content),
            "timestamp": 1_800_000_000,
        },
        "assistant_message": {
            "id": "assistant-message-1",
            "role": "assistant",
            "content": assistant_content,
            "sha256": digest(assistant_content),
        },
    }


def test_completed_turn_payload_accepts_exact_unicode_content_and_empty_assistant() -> None:
    """Validation preserves exact content while allowing a valid empty response."""
    raw = valid_payload()

    payload = CompletedTurnPayload.model_validate(raw)

    assert payload.source == "openwebui_outlet_filter"
    assert payload.user_message.role == "user"
    assert payload.assistant_message.role == "assistant"
    assert payload.user_message.content == raw["user_message"]["content"]
    assert payload.assistant_message.content == ""
    assert sha256(payload.user_message.content.encode()).hexdigest() == (
        payload.user_message.sha256
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": "field"}),
        lambda payload: payload["user_message"].update({"unexpected": "field"}),
        lambda payload: payload.update({"source": "other"}),
        lambda payload: payload["user_message"].update({"role": "assistant"}),
        lambda payload: payload["assistant_message"].update({"role": "user"}),
        lambda payload: payload["user_message"].update({"id": ""}),
        lambda payload: payload["assistant_message"].update({"id": " "}),
        lambda payload: payload["user_message"].update({"id": "x" * 201}),
        lambda payload: payload["user_message"].update({"content": "  \t\n"}),
        lambda payload: payload["user_message"].update({"sha256": "A" * 64}),
        lambda payload: payload["assistant_message"].update({"sha256": "0" * 64}),
        lambda payload: payload["user_message"].update({"timestamp": -1}),
        lambda payload: payload["user_message"].update({"timestamp": True}),
        lambda payload: payload["assistant_message"].update({"timestamp": False}),
    ],
)
def test_completed_turn_payload_rejects_noncanonical_or_untrusted_values(
    mutation: Any,
) -> None:
    """Roles, IDs, hashes, timestamps, content, and field sets are strict."""
    raw = valid_payload()
    mutation(raw)

    with pytest.raises(ValidationError):
        CompletedTurnPayload.model_validate(raw)


@pytest.mark.parametrize(
    ("message_key", "field"),
    [
        ("user_message", "id"),
        ("user_message", "role"),
        ("user_message", "content"),
        ("user_message", "sha256"),
        ("assistant_message", "id"),
        ("assistant_message", "role"),
        ("assistant_message", "content"),
        ("assistant_message", "sha256"),
    ],
)
def test_completed_turn_payload_requires_every_message_field(message_key: str, field: str) -> None:
    """Neither native message may omit identity, role, content, or digest."""
    raw = valid_payload()
    del raw[message_key][field]

    with pytest.raises(ValidationError):
        CompletedTurnPayload.model_validate(raw)


def test_completed_turn_payload_is_deeply_immutable() -> None:
    """Validated event data cannot be mutated before persistence."""
    payload = CompletedTurnPayload.model_validate(deepcopy(valid_payload()))

    with pytest.raises(ValidationError):
        payload.user_message.content = "changed"
