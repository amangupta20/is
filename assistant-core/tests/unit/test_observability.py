"""Tests for redacted runtime observability."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Self
from uuid import UUID

import anyio
import httpcore
import httpx
import pytest
import structlog
from fastapi import FastAPI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import Counter
from starlette.responses import StreamingResponse

from assistant_core import main as main_module
from assistant_core import observability
from assistant_core.config import Settings
from assistant_core.main import create_app


async def request(
    app: FastAPI,
    path: str,
    *,
    headers: dict[str, str | bytes] | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    """Call the ASGI app without opening a network socket."""
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, headers=headers)


def test_valid_correlation_id_is_returned_to_the_caller() -> None:
    """A safe caller-provided correlation ID crosses the response boundary."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(
        lambda: request(app, "/health/live", headers={"x-correlation-id": "chat_7:req-3"})
    )

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "chat_7:req-3"


@pytest.mark.parametrize("correlation_id", ["a", "a" * 128, "AZaz09._:-"])
def test_correlation_id_accepts_only_the_bounded_safe_alphabet(correlation_id: str) -> None:
    """The documented safe alphabet is accepted through its 128-character bound."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(
        lambda: request(app, "/health/live", headers={"x-correlation-id": correlation_id})
    )

    assert response.headers["x-correlation-id"] == correlation_id


@pytest.mark.parametrize(
    "correlation_id",
    ["", "contains space", "contains/slash", b"\xe9", "a" * 129],
)
def test_invalid_correlation_id_is_replaced_with_a_uuid(correlation_id: str | bytes) -> None:
    """Empty, unsafe, non-ASCII, and overlong caller IDs are never propagated."""
    app = create_app(Settings(hmac_secret="a" * 32))

    response = anyio.run(
        lambda: request(app, "/health/live", headers={"x-correlation-id": correlation_id})
    )

    generated = response.headers["x-correlation-id"]
    assert generated != correlation_id
    assert str(UUID(generated)) == generated


@pytest.mark.parametrize("raises", [False, True])
def test_correlation_context_is_cleared_after_every_request(raises: bool) -> None:
    """Request-local context cannot leak after success or an application exception."""
    app = create_app(Settings(hmac_secret="a" * 32))

    if raises:

        @app.get("/explode")
        async def explode() -> None:
            raise RuntimeError("sensitive exception text")

    path = "/explode" if raises else "/health/live"

    async def request_then_read_context() -> dict[str, object]:
        await request(app, path, raise_app_exceptions=False)
        return structlog.contextvars.get_contextvars()

    assert anyio.run(request_then_read_context) == {}


def test_completion_log_contains_only_redacted_fixed_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Completion logs use route templates and omit request or exception details."""
    app = create_app(Settings(hmac_secret="a" * 32))

    @app.get("/objects/{object_id}")
    async def read_object(object_id: str) -> dict[str, str]:
        return {"object_id": object_id}

    caplog.set_level(logging.INFO, logger="assistant_core.http")
    response = anyio.run(
        lambda: request(
            app,
            "/objects/private-object?secret=query-value",
            headers={
                "x-correlation-id": "safe-request-id",
                "authorization": "Bearer private-header",
            },
        )
    )

    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "assistant_core.http"
    ]
    assert response.status_code == 200
    assert len(records) == 1
    assert set(records[0]) == {
        "correlation_id",
        "duration_ms",
        "event",
        "http_method",
        "http_path",
        "level",
        "status_code",
        "timestamp",
    }
    assert records[0]["event"] == "http_request_completed"
    assert records[0]["correlation_id"] == "safe-request-id"
    assert records[0]["http_method"] == "GET"
    assert records[0]["http_path"] == "/objects/{object_id}"
    assert records[0]["status_code"] == 200
    assert 0 <= records[0]["duration_ms"] <= 86_400_000
    serialized = json.dumps(records[0])
    assert "private-object" not in serialized
    assert "query-value" not in serialized
    assert "private-header" not in serialized
    assert "query-value" not in caplog.text


def test_exception_path_emits_one_redacted_completion_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Application exceptions produce a generic 500 completion without exception text."""
    app = create_app(Settings(hmac_secret="a" * 32))

    @app.get("/explode/{item_id}")
    async def explode(item_id: str) -> None:
        del item_id
        raise RuntimeError("private exception detail")

    caplog.set_level(logging.INFO, logger="assistant_core.http")
    response = anyio.run(
        lambda: request(app, "/explode/private-item", raise_app_exceptions=False)
    )
    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "assistant_core.http"
    ]

    assert response.status_code == 500
    assert str(UUID(response.headers["x-correlation-id"])) == response.headers[
        "x-correlation-id"
    ]
    assert response.json() == {"detail": "internal server error"}
    assert len(records) == 1
    assert records[0]["status_code"] == 500
    assert records[0]["http_path"] == "/explode/{item_id}"
    assert "private-item" not in json.dumps(records[0])
    assert "private exception detail" not in json.dumps(records[0])


def test_uvicorn_error_records_are_generic_and_traceback_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Server error logging outside the middleware cannot expose exception content."""
    app = create_app(Settings(hmac_secret="a" * 32))
    uvicorn_logger = logging.getLogger("uvicorn.error")

    @app.get("/server-error")
    async def server_error() -> None:
        try:
            raise RuntimeError("private exception secret")
        except RuntimeError:
            uvicorn_logger.exception("private uvicorn message")
            raise

    caplog.set_level(logging.ERROR, logger="uvicorn.error")
    response = anyio.run(lambda: request(app, "/server-error"))
    uvicorn_records = [record for record in caplog.records if record.name == "uvicorn.error"]

    assert response.status_code == 500
    assert "x-correlation-id" in response.headers
    assert len(uvicorn_records) == 1
    assert uvicorn_records[0].getMessage() == "unhandled server error"
    assert uvicorn_records[0].args == ()
    assert uvicorn_records[0].exc_info is None
    assert uvicorn_records[0].exc_text is None
    assert "private exception secret" not in caplog.text
    assert "private uvicorn message" not in caplog.text


def test_started_streaming_error_is_finished_without_leaking_or_reraising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed started stream is closed best-effort without appending error content."""
    app = create_app(Settings(hmac_secret="a" * 32))

    async def failed_stream() -> AsyncIterator[bytes]:
        yield b"safe-partial-body"
        raise RuntimeError("private streaming exception")

    @app.get("/stream-error")
    async def stream_error() -> StreamingResponse:
        return StreamingResponse(failed_stream(), status_code=500)

    caplog.set_level(logging.INFO, logger="assistant_core.http")
    response = anyio.run(lambda: request(app, "/stream-error"))

    assert response.status_code == 500
    assert "x-correlation-id" in response.headers
    assert response.content == b"safe-partial-body"
    assert "private streaming exception" not in caplog.text


def test_cancellation_propagates_while_context_is_cleared() -> None:
    """The exception boundary does not convert task cancellation into an HTTP 500."""
    app = create_app(Settings(hmac_secret="a" * 32))

    @app.get("/cancel")
    async def cancel() -> None:
        raise asyncio.CancelledError

    async def exercise() -> None:
        with pytest.raises(asyncio.CancelledError):
            await request(app, "/cancel")
        assert structlog.contextvars.get_contextvars() == {}

    anyio.run(exercise)


def test_metrics_mount_exposes_only_runtime_collectors() -> None:
    """Prometheus exposition is reachable at the mount's canonical slash path."""
    private_metric = Counter(
        "private_user_metric_for_isolation",
        "Must never enter assistant-core metrics.",
        ["user_id"],
    )
    private_metric.labels(user_id="private-user").inc()
    app = create_app(Settings(hmac_secret="a" * 32))

    redirect = anyio.run(lambda: request(app, "/metrics"))
    response = anyio.run(lambda: request(app, "/metrics/"))

    assert redirect.status_code == 307
    assert redirect.headers["location"] == "http://testserver/metrics/"
    assert response.status_code == 200
    assert "python_info" in response.text
    assert "python_gc_objects_collected_total" in response.text
    assert "user_id" not in response.text
    assert "chat_id" not in response.text
    assert "message_id" not in response.text
    assert "job_id" not in response.text
    assert "private_user_metric_for_isolation" not in response.text
    assert "private-user" not in response.text


def test_otlp_is_disabled_by_default() -> None:
    """An unset endpoint creates no tracing provider or instrumentation."""
    app = create_app(Settings(hmac_secret="a" * 32))

    assert app.state.tracer_provider is None


def test_otlp_endpoint_configures_provider_and_instrumentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled tracing is fully configurable without sending network traffic."""
    calls: dict[str, object] = {}
    shutdown_order: list[str] = []

    class FakeResource:
        @staticmethod
        def create(attributes: dict[str, str]) -> object:
            calls["resource_attributes"] = attributes
            return "resource"

    class FakeProvider:
        def __init__(self, *, resource: object) -> None:
            calls["provider_resource"] = resource

        def add_span_processor(self, processor: object) -> None:
            calls["span_processor"] = processor

        def shutdown(self) -> None:
            calls["provider_shutdown"] = True
            shutdown_order.append("provider")

    class FakeExporter:
        def __init__(self, *, endpoint: str) -> None:
            calls["exporter_endpoint"] = endpoint

    class FakeSpanProcessor:
        def __init__(self, exporter: object) -> None:
            calls["processor"] = self
            calls["processor_exporter"] = exporter

    class FakeFastAPIInstrumentor:
        @staticmethod
        def instrument_app(app: FastAPI, *, tracer_provider: object) -> None:
            calls["fastapi_instrumentation"] = (app, tracer_provider)

        @staticmethod
        def uninstrument_app(app: FastAPI) -> None:
            calls["fastapi_uninstrumentation"] = app
            shutdown_order.append("fastapi")

    class FakeHTTPXInstrumentor:
        def instrument(self, *, tracer_provider: object) -> None:
            calls["httpx_instrumentation"] = tracer_provider

        def uninstrument(self) -> None:
            calls["httpx_uninstrumentation"] = True
            shutdown_order.append("httpx")

    monkeypatch.setattr(observability, "Resource", FakeResource, raising=False)
    monkeypatch.setattr(observability, "TracerProvider", FakeProvider, raising=False)
    monkeypatch.setattr(observability, "OTLPSpanExporter", FakeExporter, raising=False)
    monkeypatch.setattr(observability, "BatchSpanProcessor", FakeSpanProcessor, raising=False)
    monkeypatch.setattr(
        observability,
        "FastAPIInstrumentor",
        FakeFastAPIInstrumentor,
        raising=False,
    )
    monkeypatch.setattr(
        observability,
        "HTTPXClientInstrumentor",
        FakeHTTPXInstrumentor,
        raising=False,
    )

    endpoint = "http://collector.internal:4318/v1/traces"
    app = create_app(
        Settings(
            environment="staging",
            hmac_secret="a" * 32,
            otlp_endpoint=endpoint,
        )
    )

    assert isinstance(app.state.tracer_provider, FakeProvider)
    assert calls["resource_attributes"] == {
        "service.name": "assistant-core",
        "deployment.environment.name": "staging",
    }
    assert calls["provider_resource"] == "resource"
    assert calls["exporter_endpoint"] == endpoint
    assert calls["span_processor"] is calls["processor"]
    assert isinstance(calls["processor_exporter"], observability.SanitizingSpanExporter)
    assert isinstance(calls["processor_exporter"]._delegate, FakeExporter)
    assert calls["fastapi_instrumentation"] == (app, app.state.tracer_provider)
    assert "httpx_instrumentation" not in calls

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert "provider_shutdown" not in calls
            assert calls["httpx_instrumentation"] is app.state.tracer_provider

    anyio.run(run_lifespan)

    assert calls["provider_shutdown"] is True
    assert calls["httpx_uninstrumentation"] is True
    assert calls["fastapi_uninstrumentation"] is app
    assert shutdown_order == ["httpx", "fastapi", "provider"]


def test_observability_setup_runs_once_before_routers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application setup establishes observability before any API router is attached."""
    events: list[str] = []
    original_setup = main_module.setup_observability
    original_include_router = FastAPI.include_router

    def recording_setup(app: FastAPI, settings: Settings) -> None:
        events.append("observability")
        original_setup(app, settings)

    def recording_include_router(self: FastAPI, *args: object, **kwargs: object) -> None:
        events.append("router")
        original_include_router(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(main_module, "setup_observability", recording_setup)
    monkeypatch.setattr(FastAPI, "include_router", recording_include_router)

    main_module.create_app(Settings(hmac_secret="a" * 32))

    assert events.count("observability") == 1
    assert events.index("observability") < events.index("router")


def test_httpx_instrumentation_has_sequential_lifespan_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each app owns one active wrapper and removes it before provider shutdown."""
    original_handle = httpx.AsyncHTTPTransport.handle_async_request
    shutdown_saw_unwrapped: list[bool] = []

    class RecordingExporter(InMemorySpanExporter):
        def shutdown(self) -> None:
            shutdown_saw_unwrapped.append(
                httpx.AsyncHTTPTransport.handle_async_request is original_handle
            )
            super().shutdown()

    monkeypatch.setattr(
        observability,
        "OTLPSpanExporter",
        lambda *, endpoint: RecordingExporter(),
    )
    apps = [
        create_app(
            Settings(
                environment="test",
                hmac_secret="a" * 32,
                otlp_endpoint=f"http://collector.invalid/{index}",
            )
        )
        for index in range(2)
    ]

    assert httpx.AsyncHTTPTransport.handle_async_request is original_handle

    async def run_lifespans() -> None:
        for app in apps:
            async with app.router.lifespan_context(app):
                assert httpx.AsyncHTTPTransport.handle_async_request is not original_handle
            assert httpx.AsyncHTTPTransport.handle_async_request is original_handle

    anyio.run(run_lifespans)

    assert shutdown_saw_unwrapped == [True, True]


def test_exported_server_and_client_spans_contain_no_url_or_payload_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real auto-instrumented spans are sanitized before an exporter receives them."""
    exporter = InMemorySpanExporter()
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "url.full=https://private-resource.invalid/private-resource-path",
    )
    monkeypatch.setattr(
        observability,
        "OTLPSpanExporter",
        lambda *, endpoint: exporter,
    )
    app = create_app(
        Settings(
            environment="test",
            hmac_secret="a" * 32,
            otlp_endpoint="http://collector.invalid/v1/traces",
        )
    )

    class NetworkFreePool:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: object,
        ) -> None:
            return None

        async def handle_async_request(self, request: object) -> httpcore.Response:
            del request

            async def empty_body() -> AsyncIterator[bytes]:
                if False:
                    yield b""

            return httpcore.Response(204, content=empty_body())

        async def aclose(self) -> None:
            return None

    @app.get("/trace/{private_item}")
    async def traced_route(private_item: str) -> dict[str, str]:
        del private_item
        transport = httpx.AsyncHTTPTransport()
        transport._pool = NetworkFreePool()  # type: ignore[assignment]
        async with httpx.AsyncClient(transport=transport) as outbound_client:
            await outbound_client.get(
                "https://private-host.invalid/private-client-path"
                "?client_token=private-client-query"
            )
        return {"status": "ok"}

    async def exercise() -> None:
        async with app.router.lifespan_context(app):
            response = await request(
                app,
                "/trace/private-server-value?server_token=private-server-query",
                headers={
                    "traceparent": "00-11111111111111111111111111111111-2222222222222222-01",
                    "tracestate": "vendor=private-trace-header",
                },
            )
            assert response.status_code == 200
            assert app.state.tracer_provider.force_flush()

    anyio.run(exercise)

    spans = exporter.get_finished_spans()
    serialized = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes or {}),
                "resource": dict(span.resource.attributes),
                "events": [
                    {"name": event.name, "attributes": dict(event.attributes or {})}
                    for event in span.events
                ],
                "status_description": span.status.description,
                "trace_state": str(span.context.trace_state) if span.context else "",
                "parent_trace_state": str(span.parent.trace_state) if span.parent else "",
                "scope_schema_url": (
                    span.instrumentation_scope.schema_url if span.instrumentation_scope else None
                ),
                "scope_attributes": (
                    dict(span.instrumentation_scope.attributes or {})
                    if span.instrumentation_scope
                    else {}
                ),
            }
            for span in spans
        ],
        default=str,
    )
    forbidden_values = (
        "private-server-value",
        "private-server-query",
        "private-client-path",
        "private-client-query",
        "private-host.invalid",
        "private-resource.invalid",
        "private-resource-path",
        "private-trace-header",
        "/trace/",
    )

    assert len(spans) >= 2
    assert {span.name for span in spans} <= {
        "assistant-core.client",
        "assistant-core.internal",
        "assistant-core.server",
    }
    assert all(
        span.instrumentation_scope is None or not span.instrumentation_scope.schema_url
        for span in spans
    )
    assert all(value not in serialized for value in forbidden_values)
    for span in spans:
        assert set(span.attributes or {}) <= observability.SAFE_SPAN_ATTRIBUTES
        assert set(span.resource.attributes) <= observability.SAFE_RESOURCE_ATTRIBUTES
        assert span.events == ()
        assert span.links == ()
        assert span.status.description is None
        for attribute_name in span.attributes or {}:
            assert not any(
                fragment in attribute_name.lower()
                for fragment in ("body", "header", "path", "payload", "query", "route", "url")
            )


def test_concurrent_different_httpx_provider_fails_without_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second provider cannot silently steal the active global HTTPX wrapper."""
    original_handle = httpx.AsyncHTTPTransport.handle_async_request
    monkeypatch.setattr(
        observability,
        "OTLPSpanExporter",
        lambda *, endpoint: InMemorySpanExporter(),
    )
    apps = [
        create_app(
            Settings(
                environment="test",
                hmac_secret="a" * 32,
                otlp_endpoint=f"http://collector.invalid/concurrent-{index}",
            )
        )
        for index in range(2)
    ]

    async def exercise() -> None:
        async with apps[0].router.lifespan_context(apps[0]):
            assert httpx.AsyncHTTPTransport.handle_async_request is not original_handle
            with pytest.raises(RuntimeError, match="different active provider"):
                async with apps[1].router.lifespan_context(apps[1]):
                    pytest.fail("second provider unexpectedly started")
            assert httpx.AsyncHTTPTransport.handle_async_request is not original_handle

    anyio.run(exercise)

    assert httpx.AsyncHTTPTransport.handle_async_request is original_handle
