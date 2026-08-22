"""Runnable FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from assistant_core.api.routes.admin import router as admin_router
from assistant_core.api.routes.artifacts import router as artifacts_router
from assistant_core.api.routes.context import router as context_router
from assistant_core.api.routes.events import router as events_router
from assistant_core.api.routes.health import router as health_router
from assistant_core.api.routes.inspection import router as inspection_router
from assistant_core.api.routes.personal_context import router as personal_context_router
from assistant_core.api.routes.status import router as status_router
from assistant_core.config import Settings
from assistant_core.db.session import create_database
from assistant_core.observability import setup_observability

UI_DIR = Path(__file__).parent / "ui"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the assistant-core API application."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            if application.state.tracing_runtime is not None:
                application.state.tracing_runtime.start()
            yield
        finally:
            try:
                if application.state.tracing_runtime is not None:
                    application.state.tracing_runtime.shutdown()
            finally:
                await application.state.engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.engine, app.state.session_factory = create_database(resolved_settings.database_url)
    setup_observability(app, resolved_settings)
    app.include_router(admin_router)
    app.include_router(artifacts_router)
    app.include_router(context_router)
    app.include_router(events_router)
    app.include_router(health_router)
    app.include_router(inspection_router)
    app.include_router(personal_context_router)
    app.include_router(status_router)

    if UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_dashboard_ui() -> FileResponse:
            response = FileResponse(str(UI_DIR / "index.html"))
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    return app
