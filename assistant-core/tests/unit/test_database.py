"""Tests for database construction and metadata boundaries."""

import anyio
from sqlalchemy import Column, Integer, MetaData, Table

from assistant_core.db.base import NAMING_CONVENTION, Base
from assistant_core.db.migration_filter import include_object
from assistant_core.db.session import create_database


def test_base_metadata_is_scoped_and_has_stable_constraint_names() -> None:
    """All future models default to the assistant schema with deterministic names."""
    assert Base.metadata.schema == "assistant_core"
    assert Base.metadata.naming_convention == NAMING_CONVENTION
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}


def test_database_factory_uses_asyncpg_pre_ping_and_non_expiring_sessions() -> None:
    """Database construction applies the required async engine/session policy."""
    engine, session_factory = create_database(
        "postgresql://fixture-user:fixture-password@localhost/fixture-database"
    )

    try:
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.pool._pre_ping is True
        assert session_factory.kw["expire_on_commit"] is False
    finally:
        anyio.run(engine.dispose)


def test_alembic_object_filter_rejects_metadata_tables_outside_assistant_schema() -> None:
    """Autogenerate accepts only target-metadata tables in assistant_core."""
    assistant_table = Table(
        "allowed",
        MetaData(schema="assistant_core"),
        Column("id", Integer, primary_key=True),
    )
    public_table = Table(
        "rejected_public",
        MetaData(schema="public"),
        Column("id", Integer, primary_key=True),
    )
    other_table = Table(
        "rejected_other",
        MetaData(schema="other_schema"),
        Column("id", Integer, primary_key=True),
    )

    assert include_object(assistant_table, assistant_table.name, "table", False, None)
    assert not include_object(public_table, public_table.name, "table", False, None)
    assert not include_object(other_table, other_table.name, "table", False, None)
