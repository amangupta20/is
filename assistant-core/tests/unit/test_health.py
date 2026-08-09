"""Tests for public health endpoints."""

import asyncio

import anyio
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from assistant_core.config import Settings
from assistant_core.main import create_app


async def request_liveness(app: FastAPI) -> httpx.Response:
    """Call the application without a network listener."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/health/live")


async def request_readiness(
    app: FastAPI,
    *,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    """Call the readiness endpoint without a network listener."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/health/ready")


class SuccessfulConnection:
    """Record the statement executed by a readiness probe."""

    def __init__(self) -> None:
        self.statement = ""

    async def execute(self, statement: object) -> None:
        """Capture a SQLAlchemy statement without contacting a database."""
        self.statement = str(statement)


class SuccessfulConnectionContext:
    """Provide an async context manager around a fake connection."""

    def __init__(self, connection: SuccessfulConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> SuccessfulConnection:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class SuccessfulEngine:
    """Supply a successful connection to the readiness route."""

    def __init__(self) -> None:
        self.connection = SuccessfulConnection()

    def connect(self) -> SuccessfulConnectionContext:
        return SuccessfulConnectionContext(self.connection)


class FailingConnectionContext:
    """Fail when readiness attempts to enter a database connection."""

    async def __aenter__(self) -> None:
        raise SQLAlchemyError("driver detail must stay private")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class FailingEngine:
    """Supply a connection that cannot be established."""

    def connect(self) -> FailingConnectionContext:
        return FailingConnectionContext()


class NetworkFailingConnectionContext:
    """Raise a realistic non-SQLAlchemy connection failure."""

    async def __aenter__(self) -> None:
        raise ConnectionRefusedError("private-db-host:6543 refused secret-password")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class NetworkFailingEngine:
    """Supply a connection refused before SQLAlchemy wraps the error."""

    def connect(self) -> NetworkFailingConnectionContext:
        return NetworkFailingConnectionContext()


class CancelledConnectionContext:
    """Cancel a readiness probe while it opens a connection."""

    async def __aenter__(self) -> None:
        raise asyncio.CancelledError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class CancelledEngine:
    """Supply a connection attempt that is cancelled."""

    def connect(self) -> CancelledConnectionContext:
        return CancelledConnectionContext()


class DisposableEngine(SuccessfulEngine):
    """Track application-lifespan disposal."""

    def __init__(self) -> None:
        super().__init__()
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def test_liveness_returns_only_ok_status() -> None:
    """Liveness is public and never exposes settings."""
    app = create_app(Settings(hmac_secret="a" * 32))

    app.state.engine = FailingEngine()

    response = anyio.run(request_liveness, app)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "hmac" not in response.text.lower()


def test_readiness_executes_select_one_and_returns_exact_contract() -> None:
    """Readiness succeeds only after executing a minimal database probe."""
    app = create_app(Settings(hmac_secret="a" * 32))
    engine = SuccessfulEngine()
    app.state.engine = engine

    response = anyio.run(request_readiness, app)

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert engine.connection.statement == "select 1"


def test_readiness_returns_generic_service_unavailable_on_database_failure() -> None:
    """Readiness never exposes connection or driver details."""
    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.engine = FailingEngine()

    response = anyio.run(request_readiness, app)

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert "driver detail" not in response.text


def test_readiness_returns_generic_service_unavailable_on_network_failure() -> None:
    """Raw network failures receive the same detail-free readiness contract."""
    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.engine = NetworkFailingEngine()

    response = anyio.run(lambda: request_readiness(app, raise_app_exceptions=False))

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert "private-db-host" not in response.text
    assert "secret-password" not in response.text


def test_readiness_does_not_convert_cancellation_to_service_unavailable() -> None:
    """Task cancellation continues to propagate through readiness."""
    app = create_app(Settings(hmac_secret="a" * 32))
    app.state.engine = CancelledEngine()

    with pytest.raises(asyncio.CancelledError):
        anyio.run(request_readiness, app)


def test_application_lifespan_disposes_the_engine() -> None:
    """Application shutdown releases the async engine pool."""
    app = create_app(Settings(hmac_secret="a" * 32))
    engine = DisposableEngine()
    app.state.engine = engine

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert not engine.disposed

    anyio.run(run_lifespan)

    assert engine.disposed
