"""Contract tests for signed event request and response schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from assistant_core.events.schemas import EventAccepted, EventEnvelope


def valid_event(**changes: object) -> dict[str, object]:
    """Return a valid event envelope with selected fields replaced."""
    event: dict[str, object] = {
        "schema_version": 1,
        "event_id": "event-1",
        "event_type": "chat.completed",
        "native_user_id": "user-1",
        "native_chat_id": "chat-1",
        "native_message_id": "message-1",
        "occurred_at": datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        "payload": {"source": "openwebui"},
    }
    event.update(changes)
    return event


def test_event_contract_accepts_version_one_and_response_shape() -> None:
    """Version one parses and the acceptance response contains only retry state."""
    event = EventEnvelope.model_validate(valid_event())
    response = EventAccepted(event_id=event.event_id, duplicate=False)

    assert event.schema_version == 1
    assert response.model_dump() == {"event_id": "event-1", "duplicate": False}


@pytest.mark.parametrize("schema_version", [0, 2, "1"])
def test_event_contract_rejects_unsupported_schema_versions(schema_version: object) -> None:
    """Only integer schema version one is accepted."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(schema_version=schema_version))


def test_event_contract_forbids_extra_fields() -> None:
    """Unknown wire fields are rejected instead of silently discarded."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(unexpected=True))


def test_event_contract_rejects_naive_timestamp() -> None:
    """Event occurrence timestamps must identify a timezone."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(
            valid_event(occurred_at=datetime(2026, 8, 9, 12, 0))  # noqa: DTZ001
        )


@pytest.mark.parametrize("field", ["event_id", "event_type", "native_user_id"])
def test_event_contract_rejects_empty_required_identity(field: str) -> None:
    """Event, type, and native-user identifiers cannot be empty."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(**{field: ""}))


@pytest.mark.parametrize(
    ("field", "length"),
    [
        ("event_id", 201),
        ("event_type", 121),
        ("native_user_id", 201),
        ("native_chat_id", 201),
        ("native_message_id", 201),
    ],
)
def test_event_contract_rejects_identifiers_over_their_bounds(field: str, length: int) -> None:
    """Every wire identifier stays within its database column bound."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(**{field: "x" * length}))


def test_event_contract_requires_a_json_object_payload() -> None:
    """JSON scalars and arrays are not event payload objects."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(payload=["not", "an", "object"]))


def test_event_contract_allows_empty_optional_native_ids() -> None:
    """Optional chat and message identifiers are bounded but need not be populated."""
    event = EventEnvelope.model_validate(
        valid_event(native_chat_id="", native_message_id="")
    )

    assert event.native_chat_id == ""
    assert event.native_message_id == ""


def test_event_contract_rejects_payload_over_one_megabyte() -> None:
    """The compact UTF-8 JSON encoding is limited to one million bytes."""
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(valid_event(payload={"value": "x" * 1_000_000}))
