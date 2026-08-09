"""Redacted observability configuration for assistant-core."""

import logging
import re
from collections.abc import Sequence
from threading import Lock
from time import perf_counter
from uuid import uuid4

import httpx
import structlog
from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import SpanContext, SpanKind, Status, TraceState
from opentelemetry.util.types import AttributeValue
from prometheus_client import (
    CollectorRegistry,
    GCCollector,
    PlatformCollector,
    ProcessCollector,
    make_asgi_app,
)
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from assistant_core.config import Settings

CORRELATION_HEADER = "x-correlation-id"
CORRELATION_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
MAX_DURATION_MS = 86_400_000.0
INTERNAL_ERROR_BODY = b'{"detail":"internal server error"}'
GENERIC_SERVER_ERROR = "unhandled server error"
SAFE_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)
HTTP_LOGGER = structlog.get_logger("assistant_core.http")


class UvicornErrorRedactionFilter(logging.Filter):
    """Replace error-level Uvicorn records with one content-free constant."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR or record.exc_info is not None:
            safe_record = logging.LogRecord(
                name="uvicorn.error",
                level=record.levelno,
                pathname="",
                lineno=0,
                msg=GENERIC_SERVER_ERROR,
                args=(),
                exc_info=None,
                func=None,
                sinfo=None,
            )
            record.__dict__.clear()
            record.__dict__.update(safe_record.__dict__)
        return True


def _snapshot_httpx_methods() -> tuple[object, object]:
    return (
        httpx.HTTPTransport.handle_request,
        httpx.AsyncHTTPTransport.handle_async_request,
    )


def _restore_httpx_methods(methods: tuple[object, object]) -> None:
    """Restore exact class callables after a partial instrumentation mutation."""
    # OpenTelemetry patches these two public transport methods via wrapt. Its public
    # uninstrument API is attempted first; exact restoration is the last-resort
    # rollback needed when that API raises after only partially undoing its wrappers.
    try:
        type.__setattr__(httpx.HTTPTransport, "handle_request", methods[0])
    finally:
        type.__setattr__(httpx.AsyncHTTPTransport, "handle_async_request", methods[1])


class HTTPXInstrumentationManager:
    """Own the process-global HTTPX wrapper for one active provider at a time."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._provider: TracerProvider | None = None
        self._instrumentor: HTTPXClientInstrumentor | None = None
        self._references = 0
        self._original_methods: tuple[object, object] | None = None

    def _clear(self) -> None:
        self._instrumentor = None
        self._provider = None
        self._references = 0
        self._original_methods = None

    def acquire(self, provider: TracerProvider) -> None:
        """Instrument HTTPX or share an existing binding to the same provider."""
        with self._lock:
            if self._provider is not None:
                if self._provider is not provider:
                    raise RuntimeError("HTTPX tracing already has a different active provider")
                self._references += 1
                return

            original_methods = _snapshot_httpx_methods()
            instrumentor = HTTPXClientInstrumentor()
            try:
                instrumentor.instrument(tracer_provider=provider)
            except Exception as instrumentation_error:  # noqa: BLE001
                del instrumentation_error
                try:
                    instrumentor.uninstrument()
                except Exception:  # noqa: BLE001, S110 - exact rollback follows
                    pass
                finally:
                    try:
                        _restore_httpx_methods(original_methods)
                    finally:
                        self._clear()
                raise RuntimeError("HTTPX tracing instrumentation failed") from None
            self._instrumentor = instrumentor
            self._provider = provider
            self._references = 1
            self._original_methods = original_methods

    def release(self, provider: TracerProvider) -> bool:
        """Release one lease and report whether the provider lost its final owner."""
        with self._lock:
            if self._provider is not provider or self._instrumentor is None:
                raise RuntimeError("HTTPX tracing provider is not active")

            self._references -= 1
            if self._references > 0:
                return False

            original_methods = self._original_methods
            if original_methods is None:
                self._clear()
                raise RuntimeError("HTTPX tracing manager has no rollback snapshot")

            uninstrumentation_failed = False
            try:
                self._instrumentor.uninstrument()
            except Exception as uninstrumentation_error:  # noqa: BLE001
                del uninstrumentation_error
                uninstrumentation_failed = True
            finally:
                try:
                    _restore_httpx_methods(original_methods)
                finally:
                    self._clear()

            if uninstrumentation_failed:
                raise RuntimeError("HTTPX tracing uninstrumentation failed") from None
            return True


HTTPX_INSTRUMENTATION_MANAGER = HTTPXInstrumentationManager()

SAFE_SPAN_ATTRIBUTES = frozenset(
    {
        "http.flavor",
        "http.method",
        "http.request.method",
        "http.response.status_code",
        "http.status_code",
        "network.protocol.version",
    }
)
SAFE_RESOURCE_ATTRIBUTES = frozenset(
    {
        "deployment.environment.name",
        "service.name",
        "telemetry.sdk.language",
        "telemetry.sdk.name",
        "telemetry.sdk.version",
    }
)


def _safe_span_name(kind: SpanKind) -> str:
    if kind is SpanKind.SERVER:
        return "assistant-core.server"
    if kind is SpanKind.CLIENT:
        return "assistant-core.client"
    return "assistant-core.internal"


def _safe_http_method(candidate: object) -> str:
    if isinstance(candidate, str) and candidate in SAFE_HTTP_METHODS:
        return candidate
    return "_OTHER"


def _sanitized_context(context: SpanContext | None) -> SpanContext | None:
    if context is None:
        return None
    return SpanContext(
        trace_id=context.trace_id,
        span_id=context.span_id,
        is_remote=context.is_remote,
        trace_flags=context.trace_flags,
        trace_state=TraceState(),
    )


def _sanitized_span(span: ReadableSpan) -> ReadableSpan:
    """Clone a finished span using only bounded, content-free telemetry fields."""
    attributes: dict[str, AttributeValue] = {}
    for key, value in (span.attributes or {}).items():
        if key not in SAFE_SPAN_ATTRIBUTES:
            continue
        if key in {"http.method", "http.request.method"}:
            attributes[key] = _safe_http_method(value)
        else:
            attributes[key] = value
    resource = Resource(
        {
            key: value
            for key, value in span.resource.attributes.items()
            if key in SAFE_RESOURCE_ATTRIBUTES
        }
    )
    return ReadableSpan(
        name=_safe_span_name(span.kind),
        context=_sanitized_context(span.context),
        parent=_sanitized_context(span.parent),
        resource=resource,
        attributes=attributes,
        events=(),
        links=(),
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=InstrumentationScope(name="assistant-core.observability"),
    )


class SanitizingSpanExporter(SpanExporter):
    """Ensure URL, payload, and exception content never reaches an exporter."""

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._delegate.export(tuple(_sanitized_span(span) for span in spans))

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._delegate.force_flush(timeout_millis)


def configure_logging(log_level: str) -> None:
    """Configure stdlib logging as the sink for Structlog JSON events."""
    resolved_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=resolved_level, format="%(message)s")
    logging.getLogger().setLevel(resolved_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    for existing_filter in tuple(uvicorn_error_logger.filters):
        if isinstance(existing_filter, UvicornErrorRedactionFilter):
            uvicorn_error_logger.removeFilter(existing_filter)
    uvicorn_error_logger.addFilter(UvicornErrorRedactionFilter())
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
        response_started = False
        response_complete = False
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_correlation_id(message: Message) -> None:
            nonlocal response_complete, response_started, status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_correlation_id)
            except Exception:  # noqa: BLE001 - boundary converts ordinary failures safely
                generated_response = False
                if not response_started:
                    generated_response = True
                    await send_with_correlation_id(
                        {
                            "type": "http.response.start",
                            "status": 500,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(INTERNAL_ERROR_BODY)).encode()),
                            ],
                        }
                    )
                if not response_complete:
                    try:
                        await send_with_correlation_id(
                            {
                                "type": "http.response.body",
                                "body": INTERNAL_ERROR_BODY if generated_response else b"",
                            }
                        )
                    except Exception:  # noqa: BLE001, S110 - response completion is best effort
                        pass
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            elapsed_ms = min(max((perf_counter() - started_at) * 1000, 0.0), MAX_DURATION_MS)
            try:
                HTTP_LOGGER.info(
                    "http_request_completed",
                    http_method=_safe_http_method(scope.get("method")),
                    http_path=route_path,
                    status_code=status_code,
                    duration_ms=round(elapsed_ms, 3),
                )
            finally:
                structlog.contextvars.clear_contextvars()


class TracingRuntime:
    """Own app and process tracing resources across one application lifespan."""

    def __init__(self, app: FastAPI, provider: TracerProvider) -> None:
        self.app = app
        self.provider = provider
        self._httpx_active = False
        self._shutdown = False

    def start(self) -> None:
        """Bind global client instrumentation to this active provider."""
        if self._shutdown:
            raise RuntimeError("tracing runtime is already shut down")
        if self._httpx_active:
            return
        HTTPX_INSTRUMENTATION_MANAGER.acquire(self.provider)
        self._httpx_active = True

    def shutdown(self) -> None:
        """Remove instrumentation before shutting down the provider."""
        if self._shutdown:
            return
        final_provider_owner = not self._httpx_active
        release_error: RuntimeError | None = None
        try:
            if self._httpx_active:
                try:
                    final_provider_owner = HTTPX_INSTRUMENTATION_MANAGER.release(self.provider)
                except RuntimeError as error:
                    release_error = error
                    final_provider_owner = True
                finally:
                    self._httpx_active = False
        finally:
            try:
                FastAPIInstrumentor.uninstrument_app(self.app)
            finally:
                if final_provider_owner:
                    self.provider.shutdown()
                self._shutdown = True
        if release_error is not None:
            raise release_error


def configure_tracing(app: FastAPI, settings: Settings) -> TracingRuntime | None:
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
    exporter = SanitizingSpanExporter(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    return TracingRuntime(app, provider)


def create_metrics_registry() -> CollectorRegistry:
    """Create an isolated registry containing only bounded runtime collectors."""
    registry = CollectorRegistry()
    GCCollector(registry=registry)
    PlatformCollector(registry=registry)
    ProcessCollector(registry=registry)
    return registry


def setup_observability(app: FastAPI, settings: Settings) -> None:
    """Attach request observability before application routes are registered."""
    configure_logging(settings.log_level)
    app.add_middleware(CorrelationIdMiddleware)
    app.state.metrics_registry = create_metrics_registry()
    app.mount("/metrics", make_asgi_app(registry=app.state.metrics_registry), name="metrics")
    app.state.tracing_runtime = configure_tracing(app, settings)
    app.state.tracer_provider = (
        app.state.tracing_runtime.provider if app.state.tracing_runtime is not None else None
    )
