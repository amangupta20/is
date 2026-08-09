"""Tests for redacted runtime observability."""

import json
import logging
from uuid import UUID

import anyio
import httpx
import pytest
import structlog
from fastapi import FastAPI

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
    assert len(records) == 1
    assert records[0]["status_code"] == 500
    assert records[0]["http_path"] == "/explode/{item_id}"
    assert "private-item" not in json.dumps(records[0])
    assert "private exception detail" not in json.dumps(records[0])


def test_metrics_mount_exposes_only_runtime_collectors() -> None:
    """Prometheus exposition is reachable at the mount's canonical slash path."""
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


def test_otlp_is_disabled_by_default() -> None:
    """An unset endpoint creates no tracing provider or instrumentation."""
    app = create_app(Settings(hmac_secret="a" * 32))

    assert app.state.tracer_provider is None


def test_otlp_endpoint_configures_provider_and_instrumentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled tracing is fully configurable without sending network traffic."""
    calls: dict[str, object] = {}

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

    class FakeHTTPXInstrumentor:
        def instrument(self, *, tracer_provider: object) -> None:
            calls["httpx_instrumentation"] = tracer_provider

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
    assert isinstance(calls["processor_exporter"], FakeExporter)
    assert calls["fastapi_instrumentation"] == (app, app.state.tracer_provider)
    assert calls["httpx_instrumentation"] is app.state.tracer_provider

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            assert "provider_shutdown" not in calls

    anyio.run(run_lifespan)

    assert calls["provider_shutdown"] is True


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
