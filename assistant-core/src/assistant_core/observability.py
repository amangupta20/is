"""Redacted observability configuration for assistant-core."""

import logging
import re
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import make_asgi_app
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from assistant_core.config import Settings

CORRELATION_HEADER = "x-correlation-id"
CORRELATION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
MAX_DURATION_MS = 86_400_000.0
HTTP_LOGGER = structlog.get_logger("assistant_core.http")


def configure_logging(log_level: str) -> None:
    """Configure stdlib logging as the sink for Structlog JSON events."""
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=resolved_level, format="%(message)s")
    logging.getLogger().setLevel(resolved_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def _correlation_id(candidate: str | None) -> str:
    if candidate is not None and CORRELATION_ID_PATTERN.fullmatch(candidate):
        return candidate
    return str(uuid4())


class CorrelationIdMiddleware:
    """Bind one safe correlation ID around each HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _correlation_id(Headers(scope=scope).get(CORRELATION_HEADER))
        started_at = perf_counter()
        status_code = 500
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_correlation_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_correlation_id)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            elapsed_ms = min(max((perf_counter() - started_at) * 1000, 0.0), MAX_DURATION_MS)
            try:
                HTTP_LOGGER.info(
                    "http_request_completed",
                    http_method=scope["method"],
                    http_path=route_path,
                    status_code=status_code,
                    duration_ms=round(elapsed_ms, 3),
                )
            finally:
                structlog.contextvars.clear_contextvars()


def configure_tracing(app: FastAPI, settings: Settings) -> TracerProvider | None:
    """Enable OTLP tracing only when an explicit endpoint is configured."""
    if settings.otlp_endpoint is None or not settings.otlp_endpoint.strip():
        return None

    resource = Resource.create(
        {
            "service.name": "assistant-core",
            "deployment.environment.name": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return provider


def setup_observability(app: FastAPI, settings: Settings) -> None:
    """Attach request observability before application routes are registered."""
    configure_logging(settings.log_level)
    app.add_middleware(CorrelationIdMiddleware)
    app.mount("/metrics", make_asgi_app(), name="metrics")
    app.state.tracer_provider = configure_tracing(app, settings)
