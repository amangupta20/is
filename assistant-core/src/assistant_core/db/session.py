"""Managed async SQLAlchemy engine and session construction."""

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_async_database_url(database_url: str) -> str:
    """Select asyncpg when a PostgreSQL URL does not name an async driver."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    return database_url


def create_database(
    database_url: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create a pre-ping async engine and non-expiring session factory."""
    connect_args: dict[str, Any] = {}
    if make_async_database_url(database_url).startswith("postgresql+asyncpg"):
        # Supavisor/pgbouncer transaction pooling recycles server connections,
        # so asyncpg's cached prepared statements intermittently vanish.
        connect_args["statement_cache_size"] = 0

    engine = create_async_engine(
        make_async_database_url(database_url),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args=connect_args,
    )
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    return engine, session_factory
