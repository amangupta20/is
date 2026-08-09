"""Tests for database construction and metadata boundaries."""

import anyio

from assistant_core.db.base import NAMING_CONVENTION, Base
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
