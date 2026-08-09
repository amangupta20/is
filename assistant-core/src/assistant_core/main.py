"""Runnable FastAPI application factory."""

from fastapi import FastAPI

from assistant_core.api.routes.context import router as context_router
from assistant_core.api.routes.health import router as health_router
from assistant_core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the assistant-core API application."""
    app = FastAPI()
    app.state.settings = settings or Settings()
    app.include_router(context_router)
    app.include_router(health_router)
    return app


app = create_app()
