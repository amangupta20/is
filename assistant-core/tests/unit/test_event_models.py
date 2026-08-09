"""Metadata tests for identity, event inbox, and queued job records."""

from sqlalchemy.dialects.postgresql import JSONB, UUID

from assistant_core.events.models import EventInbox
from assistant_core.identity.models import UserIdentity
from assistant_core.jobs.models import Job


def test_models_define_only_the_required_columns_in_assistant_schema() -> None:
    """Each durable record has the exact slice columns and target schema."""
    assert UserIdentity.__table__.schema == "assistant_core"
    assert EventInbox.__table__.schema == "assistant_core"
    assert Job.__table__.schema == "assistant_core"
    assert list(UserIdentity.__table__.columns.keys()) == ["id", "native_user_id", "created_at"]
    assert list(EventInbox.__table__.columns.keys()) == [
        "event_id",
        "event_type",
        "user_id",
        "native_chat_id",
        "native_message_id",
        "occurred_at",
        "received_at",
        "payload",
    ]
    assert list(Job.__table__.columns.keys()) == [
        "id",
        "identity_key",
        "kind",
        "status",
        "payload",
        "attempts",
        "available_at",
        "claimed_at",
        "completed_at",
        "last_error_code",
    ]


def test_identity_and_event_columns_enforce_the_contract() -> None:
    """Identity and inbox types, keys, bounds, and defaults match the wire contract."""
    identity = UserIdentity.__table__.c
    event = EventInbox.__table__.c

    assert isinstance(identity.id.type, UUID)
    assert identity.id.primary_key and identity.id.default is not None
    assert identity.native_user_id.type.length == 200
    assert identity.native_user_id.unique and not identity.native_user_id.nullable
    assert identity.created_at.type.timezone and identity.created_at.server_default is not None

    assert event.event_id.primary_key and event.event_id.type.length == 200
    assert event.event_type.type.length == 120 and event.event_type.index
    assert not event.user_id.nullable
    assert next(iter(event.user_id.foreign_keys)).target_fullname == "assistant_core.user_identity.id"
    assert event.native_chat_id.type.length == 200 and event.native_chat_id.nullable
    assert event.native_message_id.type.length == 200 and event.native_message_id.nullable
    assert event.occurred_at.type.timezone and not event.occurred_at.nullable
    assert event.received_at.type.timezone and event.received_at.server_default is not None
    assert isinstance(event.payload.type, JSONB) and not event.payload.nullable


def test_job_columns_enforce_queue_defaults_and_bounds() -> None:
    """Queued jobs have stable identity, payload, timing, status, and attempt semantics."""
    job = Job.__table__.c

    assert isinstance(job.id.type, UUID)
    assert job.id.primary_key and job.id.default is not None
    assert job.identity_key.type.length == 300 and job.identity_key.unique
    assert job.kind.type.length == 120 and job.kind.index
    assert job.status.type.length == 30 and job.status.index
    assert job.status.default is not None and job.status.default.arg == "queued"
    assert job.status.server_default is not None
    assert isinstance(job.payload.type, JSONB) and not job.payload.nullable
    assert job.attempts.default is not None and job.attempts.default.arg == 0
    assert job.attempts.server_default is not None
    assert job.available_at.type.timezone and job.available_at.server_default is not None
    assert job.claimed_at.type.timezone and job.claimed_at.nullable
    assert job.completed_at.type.timezone and job.completed_at.nullable
    assert job.last_error_code.type.length == 120 and job.last_error_code.nullable
