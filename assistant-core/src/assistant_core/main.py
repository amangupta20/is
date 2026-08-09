"""Runnable FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from assistant_core.api.routes.context import router as context_router
from assistant_core.api.routes.health import router as health_router
from assistant_core.config import Settings
from assistant_core.db.session import create_database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the assistant-core API application."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await application.state.engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.engine, app.state.session_factory = create_database(resolved_settings.database_url)
    app.include_router(context_router)
    app.include_router(health_router)
    return app


app = create_app()
