# Open WebUI Assistant Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers-reasonable:subagent-driven-development` (recommended) or `superpowers-reasonable:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the audited Open WebUI baseline plus a deployable, authenticated `assistant-core`, worker, database foundation, and thin Open WebUI adapters that preserve ordinary chat when optional context retrieval is unavailable.

**Architecture:** Open WebUI remains unchanged and calls one modular FastAPI companion through small Python Functions/Tools. The companion stores identity, event, job, and audit state in a dedicated PostgreSQL schema; a separate worker claims jobs with `FOR UPDATE SKIP LOCKED`. Adapter requests are signed with a shared HMAC secret and bounded by strict timeouts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2/pydantic-settings, SQLAlchemy 2 async, Alembic, PostgreSQL/pgvector, httpx, structlog, Prometheus client, OpenTelemetry, pytest, Ruff, mypy, Docker Compose.

## Global Constraints

- Do not modify or fork Open WebUI source.
- Open WebUI application state remains in its native SQLite database/volume.
- Use one `assistant-core` API deployment and one worker deployment.
- Use PostgreSQL for the durable inbox, outbox, jobs, identity mappings, and module state; do not add Valkey or a message broker.
- Use a universal 300,000-token compaction threshold and 300,000-token cap for all models.
- A context/retrieval failure must fail open for ordinary chat without adding prompt text.
- Authentication, authorization, confirmation, and write/action policy checks fail closed.
- Do not trust user identity supplied through unsigned headers.
- Open WebUI Event Functions require a deployed Open WebUI version at or above 0.10.0.
- Keep `assistant-core` internal; expose it only on the trusted Docker/private network.
- Target both `linux/amd64` development and `linux/arm64` Oracle deployment.
- Do not log full prompts, message bodies, secrets, or signed URLs.
- The approved design is `docs/superpowers/specs/2026-08-09-openwebui-personal-assistant-architecture.md`.

## Scope and plan split

This plan implements Phase 0 and Phase 1 only. Each remaining subsystem gets a separate plan after this foundation passes its smoke check:

1. `openwebui-assistant-memory` — external confirmed/inferred memory.
2. `openwebui-assistant-conversation-continuity` — transcript segments, episodes, recall, deletion, forks, and reconciliation.
3. `openwebui-assistant-capability-gateway` — discovery, validated execution, policy, and chat bindings.
4. `openwebui-assistant-content-library` — Garage canonical storage, deduplication, derivatives, and Xberg benchmark.
5. `openwebui-assistant-artifacts` — Office/PDF generation, versions, cards, and OnlyOffice.
6. `openwebui-assistant-media` — Gemini-native audio, video, and YouTube processing.

## Repository map

```text
assistant-core/
  alembic.ini                     Alembic configuration
  Dockerfile                      Multi-architecture API/worker image
  pyproject.toml                  Runtime and development dependencies
  src/assistant_core/
    __init__.py
    config.py                     Validated process configuration
    main.py                       FastAPI application factory
    observability.py              Structured logs, metrics, trace setup
    api/
      dependencies.py             Settings/session/auth dependencies
      routes/
        context.py                Bounded context endpoint
        events.py                 Idempotent event ingestion endpoint
        health.py                 Liveness/readiness endpoints
        status.py                 Redacted operator status endpoint
    auth/
      hmac.py                     Canonical signing and verification
    db/
      base.py                     SQLAlchemy metadata/base
      session.py                  Async engine and session factory
    events/
      models.py                   Inbox/outbox/job SQLAlchemy models
      schemas.py                  Stable event wire contracts
      service.py                  Transactional event ingestion
    identity/
      models.py                   Native-to-assistant user mapping
    jobs/
      worker.py                   PostgreSQL job claim/complete loop
  migrations/
    env.py
    versions/0001_foundation.py
  tests/
    unit/
      test_config.py
      test_hmac.py
      test_context.py
      test_event_schema.py
    integration/
      test_event_ingestion.py
      test_health.py
      test_worker.py
adapters/openwebui/
  context_filter.py               Fail-open global inlet Filter
  lifecycle_event.py              Event Function forwarding selected events
  assistant_core_tool.py          Explicit status Tool
  tests/
    test_context_filter.py
    test_lifecycle_event.py
    test_assistant_core_tool.py
deploy/
  .env.assistant.example
  compose.assistant.yml
ops/
  audit_openwebui.ps1             Read-only Phase 0 evidence collection
  smoke_phase1.ps1                Phase 1 end-to-end smoke check
docs/runbooks/
  phase0-native-baseline.md
  assistant-core-operations.md
```

---

### Task 1: Bootstrap the Python workspace and quality gates

**Files:**
- Create: `assistant-core/pyproject.toml`
- Create: `assistant-core/uv.lock`
- Create: `assistant-core/Dockerfile`
- Create: `assistant-core/src/assistant_core/__init__.py`
- Create: `assistant-core/src/assistant_core/{api,api/routes,auth,db,events,identity,jobs}/__init__.py`
- Create: `assistant-core/tests/unit/test_config.py`

**Interfaces:**
- Consumes: none.
- Produces: an importable `assistant_core` package, a locked Python 3.12 environment, and standard `test`, `lint`, and `typecheck` commands used by every later task.

- [ ] **Step 1: Write the package smoke test**

```python
# assistant-core/tests/unit/test_config.py
def test_package_imports() -> None:
    import assistant_core

    assert assistant_core.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the test and verify the package is absent**

Run:

```powershell
docker run --rm -v "${PWD}/assistant-core:/app" -w /app python:3.12-slim-bookworm python -m pytest tests/unit/test_config.py -q
```

Expected: FAIL because `pytest` or `assistant_core` is not installed.

- [ ] **Step 3: Add the package definition and dependencies**

```toml
# assistant-core/pyproject.toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "openwebui-assistant-core"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "alembic>=1.16,<2",
  "asyncpg>=0.30,<1",
  "fastapi>=0.116,<1",
  "httpx>=0.28,<1",
  "opentelemetry-exporter-otlp-proto-grpc>=1.35,<2",
  "opentelemetry-instrumentation-fastapi>=0.56b0,<1",
  "opentelemetry-instrumentation-httpx>=0.56b0,<1",
  "opentelemetry-sdk>=1.35,<2",
  "prometheus-client>=0.22,<1",
  "pydantic>=2.11,<3",
  "pydantic-settings>=2.10,<3",
  "sqlalchemy[asyncio]>=2.0.41,<3",
  "structlog>=25.4,<26",
  "uvicorn[standard]>=0.35,<1",
]

[dependency-groups]
dev = [
  "mypy>=1.17,<2",
  "pytest>=8.4,<9",
  "pytest-asyncio>=1.1,<2",
  "respx>=0.22,<1",
  "ruff>=0.12,<1",
]

[tool.hatch.build.targets.wheel]
packages = ["src/assistant_core"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "../adapters/openwebui/tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["assistant_core"]
```

```python
# assistant-core/src/assistant_core/__init__.py
__version__ = "0.1.0"
```

Create the subpackage files with these exact single-line contents:

```text
assistant-core/src/assistant_core/api/__init__.py: """HTTP API package."""
assistant-core/src/assistant_core/api/routes/__init__.py: """HTTP route modules."""
assistant-core/src/assistant_core/auth/__init__.py: """Service authentication."""
assistant-core/src/assistant_core/db/__init__.py: """Database infrastructure."""
assistant-core/src/assistant_core/events/__init__.py: """Event ingestion and durable event contracts."""
assistant-core/src/assistant_core/identity/__init__.py: """Native identity mappings."""
assistant-core/src/assistant_core/jobs/__init__.py: """Durable background jobs."""
```

```dockerfile
# assistant-core/Dockerfile
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 assistant
USER assistant

CMD ["uvicorn", "assistant_core.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 4: Build and run the package test**

Run:

```powershell
cd assistant-core
uv lock
uv sync --group dev
uv run pytest tests/unit/test_config.py -q
cd ..
docker build -t assistant-core:test assistant-core
docker run --rm assistant-core:test python -c "import assistant_core; assert assistant_core.__version__ == '0.1.0'"
```

Expected: the test reports `1 passed`, the lockfile is generated, and the image imports version `0.1.0` on the local architecture.

- [ ] **Step 5: Commit the bootstrap**

```powershell
git add assistant-core/pyproject.toml assistant-core/uv.lock assistant-core/Dockerfile assistant-core/src/assistant_core assistant-core/tests/unit/test_config.py
git commit -m "build: bootstrap assistant core"
```

### Task 2: Capture the effective Phase 0 Open WebUI baseline

**Files:**
- Create: `ops/audit_openwebui.ps1`
- Create: `docs/runbooks/phase0-native-baseline.md`

**Interfaces:**
- Consumes: Docker access to the running Open WebUI container and its `/app/backend/data/webui.db`.
- Produces: `artifacts/openwebui-audit/<timestamp>/` containing image identity, redacted environment names, persisted config keys, health evidence, and a human-reviewed baseline record.

- [ ] **Step 1: Add the read-only audit script**

```powershell
# ops/audit_openwebui.ps1
param(
    [Parameter(Mandatory = $true)][string]$Container,
    [string]$OutputRoot = "artifacts/openwebui-audit"
)

$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path $OutputRoot $stamp
New-Item -ItemType Directory -Path $target -Force | Out-Null

$inspect = docker inspect $Container | ConvertFrom-Json
if ($inspect.Count -ne 1) { throw "Expected exactly one container inspection result." }

[pscustomobject]@{
    container = $inspect[0].Name.TrimStart('/')
    image = $inspect[0].Config.Image
    image_id = $inspect[0].Image
    started_at = $inspect[0].State.StartedAt
    health = $inspect[0].State.Health.Status
} | ConvertTo-Json | Set-Content (Join-Path $target "container.json")

$allowedNames = @(
    "ENABLE_PERSISTENT_CONFIG", "ENABLE_OTEL", "OTEL_EXPORTER_OTLP_ENDPOINT",
    "RAG_EMBEDDING_ENGINE", "RAG_EMBEDDING_MODEL", "RAG_EMBEDDING_BATCH_SIZE",
    "RAG_RERANKING_MODEL", "CONTENT_EXTRACTION_ENGINE", "STORAGE_PROVIDER",
    "TASK_MODEL", "ENABLE_FORWARD_USER_INFO_HEADERS"
)
$envRows = $inspect[0].Config.Env | ForEach-Object {
    $name = ($_ -split '=', 2)[0]
    if ($allowedNames -contains $name) { $name }
}
$envRows | Sort-Object | Set-Content (Join-Path $target "configured-env-names.txt")

$query = "select data from config order by id desc limit 1;"
docker exec $Container python -c "import sqlite3; c=sqlite3.connect('/app/backend/data/webui.db'); print(c.execute('$query').fetchone()[0])" |
    Set-Content (Join-Path $target "persisted-config.json")

docker exec $Container python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).read().decode())" |
    Set-Content (Join-Path $target "health.txt")

Write-Host "Audit written to $target"
```

The script intentionally captures only approved environment variable names, not their values. The persisted configuration must be treated as sensitive and remain outside Git.

- [ ] **Step 2: Run the audit against the deployed container**

Run on the Docker host:

```powershell
pwsh ./ops/audit_openwebui.ps1 -Container open-webui
```

Expected: a new timestamped directory with `container.json`, `configured-env-names.txt`, `persisted-config.json`, and `health.txt`; the container remains running.

- [ ] **Step 3: Record the verified baseline without copying secrets**

```markdown
# docs/runbooks/phase0-native-baseline.md

# Phase 0 Native Baseline

Audit date: 2026-08-09

## Runtime identity

- Image reference: record from `container.json`
- Immutable image ID/digest: record from `container.json`
- Open WebUI version: verify in Admin UI and record here
- Event Function support: verify version is at least 0.10.0
- Persistent ConfigVars enabled: record `true` or `false`

## Required functional checks

- [ ] Ordinary chat survives container restart.
- [ ] Native attachment upload and RAG survive restart.
- [ ] Native web search and fetch return cited results.
- [ ] Open Terminal remains available.
- [ ] Gemini Image/Nano Banana 2 remains available.
- [ ] Chat history search finds a known old phrase.
- [ ] Garage upload path remains healthy.
- [ ] RAG embedding batch size is effectively `1`.
- [ ] Gemini Embedding 2 uses 1536 dimensions.
- [ ] Voyage `rerank-2.5-lite` is the effective external reranker.
- [ ] All models use a 300,000-token compaction threshold and cap.
- [ ] A dedicated non-thinking task model performs compaction.
- [ ] OpenTelemetry exports a test trace or logs a clear disabled state.

## Differences from deployment configuration

Record each persisted ConfigVar that overrides Compose and which source will remain authoritative.

## Rollback evidence

Record the previous image digest, snapshot identifier, and Dokploy configuration revision before changing the deployment.
```

- [ ] **Step 4: Add sensitive audit output to Git exclusions and commit the script/runbook**

Add this exact entry to the repository `.gitignore`:

```gitignore
artifacts/openwebui-audit/
deploy/.env.assistant
```

Run:

```powershell
git add .gitignore ops/audit_openwebui.ps1 docs/runbooks/phase0-native-baseline.md
git commit -m "ops: add native Open WebUI baseline audit"
```

Expected: only the script, runbook, and exclusion are committed; collected configuration is untracked and excluded.

### Task 3: Add validated settings, HMAC authentication, and health endpoints

**Files:**
- Create: `assistant-core/src/assistant_core/config.py`
- Create: `assistant-core/src/assistant_core/auth/hmac.py`
- Create: `assistant-core/src/assistant_core/db/session.py`
- Create: `assistant-core/src/assistant_core/api/dependencies.py`
- Create: `assistant-core/src/assistant_core/api/routes/health.py`
- Create: `assistant-core/src/assistant_core/main.py`
- Modify: `assistant-core/tests/unit/test_config.py`
- Test: `assistant-core/tests/unit/test_hmac.py`
- Test: `assistant-core/tests/integration/test_health.py`

**Interfaces:**
- Consumes: `ASSISTANT_DATABASE_URL`, `ASSISTANT_ADAPTER_HMAC_SECRET`, and optional telemetry settings.
- Produces: `Settings`, `sign_request()`, `verify_request()`, `create_app()`, `GET /health/live`, and `GET /health/ready`.

- [ ] **Step 1: Write authentication and health tests**

```python
# assistant-core/tests/unit/test_hmac.py
from assistant_core.auth.hmac import sign_request, verify_request


def test_signature_round_trip() -> None:
    signature = sign_request(b"secret", "POST", "/v1/events", "1700000000", b'{"x":1}')
    assert verify_request(
        b"secret", "POST", "/v1/events", "1700000000", b'{"x":1}', signature
    )


def test_signature_rejects_modified_body() -> None:
    signature = sign_request(b"secret", "POST", "/v1/events", "1700000000", b'{"x":1}')
    assert not verify_request(
        b"secret", "POST", "/v1/events", "1700000000", b'{"x":2}', signature
    )
```

```python
import pytest
from pydantic import ValidationError

import assistant_core
from assistant_core.config import Settings


def test_package_imports() -> None:
    assert assistant_core.__version__ == "0.1.0"


def test_production_rejects_development_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", adapter_hmac_secret="development-only-secret")
```

```python
# assistant-core/tests/integration/test_health.py
from fastapi.testclient import TestClient

from assistant_core.main import create_app


def test_liveness_has_no_sensitive_configuration() -> None:
    response = TestClient(create_app()).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "secret" not in response.text.lower()
```

- [ ] **Step 2: Run the focused tests and verify imports fail**

Run:

```powershell
cd assistant-core
uv run pytest tests/unit/test_config.py tests/unit/test_hmac.py tests/integration/test_health.py -q
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement settings and canonical request signing**

```python
# assistant-core/src/assistant_core/config.py
from functools import lru_cache

from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASSISTANT_", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+asyncpg://assistant:assistant@postgres:5432/assistant"
    adapter_hmac_secret: SecretStr = Field(default=SecretStr("development-only-secret"))
    request_clock_skew_seconds: int = 60
    context_timeout_seconds: float = 1.5
    log_level: str = "INFO"
    otlp_endpoint: str | None = None

    @model_validator(mode="after")
    def reject_insecure_production_secret(self) -> Self:
        secret = self.adapter_hmac_secret.get_secret_value()
        if self.environment == "production" and (
            secret == "development-only-secret" or len(secret.encode()) < 32
        ):
            raise ValueError("production HMAC secret must contain at least 32 bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

```python
# assistant-core/src/assistant_core/auth/hmac.py
import hashlib
import hmac


def canonical_request(method: str, path: str, timestamp: str, body: bytes) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{body_digest}".encode()


def sign_request(secret: bytes, method: str, path: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, canonical_request(method, path, timestamp, body), hashlib.sha256).hexdigest()


def verify_request(
    secret: bytes,
    method: str,
    path: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    expected = sign_request(secret, method, path, timestamp, body)
    return hmac.compare_digest(expected, signature)
```

- [ ] **Step 4: Implement the application factory and health routes**

```python
# assistant-core/src/assistant_core/db/session.py
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assistant_core.config import Settings


def build_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
```

```python
# assistant-core/src/assistant_core/api/dependencies.py
import time

from fastapi import HTTPException, Request, status

from assistant_core.auth.hmac import verify_request


async def require_adapter_signature(request: Request) -> None:
    timestamp = request.headers.get("x-assistant-timestamp", "")
    signature = request.headers.get("x-assistant-signature", "")
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request timestamp") from exc
    settings = request.app.state.settings
    if age > settings.request_clock_skew_seconds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "expired request timestamp")
    body = await request.body()
    secret = settings.adapter_hmac_secret.get_secret_value().encode()
    if not verify_request(secret, request.method, request.url.path, timestamp, body, signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid request signature")
```

```python
# assistant-core/src/assistant_core/api/routes/health.py
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    async with request.app.state.session_factory() as session:
        await session.execute(text("select 1"))
    return {"status": "ready"}
```

```python
# assistant-core/src/assistant_core/main.py
from fastapi import FastAPI

from assistant_core.api.routes.health import router as health_router
from assistant_core.config import Settings, get_settings
from assistant_core.db.session import build_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    app = FastAPI(title="Open WebUI Assistant Core", version="0.1.0")
    app.state.settings = active_settings
    app.state.session_factory = build_session_factory(active_settings)
    app.include_router(health_router)
    return app
```

- [ ] **Step 5: Run focused verification**

Run:

```powershell
uv run pytest tests/unit/test_config.py tests/unit/test_hmac.py tests/integration/test_health.py -q
uv run ruff check src tests
uv run mypy src
```

Expected: all tests pass; Ruff and mypy report no errors.

- [ ] **Step 6: Commit the authenticated application shell**

```powershell
git add assistant-core/src/assistant_core assistant-core/tests/unit/test_hmac.py assistant-core/tests/integration/test_health.py
git commit -m "feat: add authenticated assistant core shell"
```

### Task 4: Add the foundation schema and transactional event/job ingestion

**Files:**
- Create: `assistant-core/src/assistant_core/db/base.py`
- Create: `assistant-core/src/assistant_core/identity/models.py`
- Create: `assistant-core/src/assistant_core/events/models.py`
- Create: `assistant-core/src/assistant_core/events/schemas.py`
- Create: `assistant-core/src/assistant_core/events/service.py`
- Create: `assistant-core/src/assistant_core/api/routes/events.py`
- Create: `assistant-core/alembic.ini`
- Create: `assistant-core/migrations/env.py`
- Create: `assistant-core/migrations/versions/0001_foundation.py`
- Create: `assistant-core/tests/conftest.py`
- Modify: `assistant-core/Dockerfile`
- Test: `assistant-core/tests/unit/test_event_schema.py`
- Test: `assistant-core/tests/integration/test_event_ingestion.py`

**Interfaces:**
- Consumes: signed `EventEnvelope` JSON.
- Produces: `POST /v1/events -> 202 EventAccepted`, unique `event_id`, a durable `event_inbox` row, one identity mapping, and one idempotent queued job in the same transaction.

- [ ] **Step 1: Define and test the stable wire contract**

```python
# assistant-core/tests/unit/test_event_schema.py
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from assistant_core.events.schemas import EventEnvelope


def test_completed_turn_requires_stable_native_ids() -> None:
    event = EventEnvelope(
        event_id="evt-1",
        event_type="chat.finished",
        occurred_at=datetime.now(UTC),
        native_user_id="user-1",
        native_chat_id="chat-1",
        native_message_id="message-1",
        payload={"message_ids": ["user-message-1", "message-1"]},
    )
    assert event.schema_version == 1


def test_event_rejects_empty_identity() -> None:
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_id="evt-1",
            event_type="chat.finished",
            occurred_at=datetime.now(UTC),
            native_user_id="",
            payload={},
        )
```

```python
# assistant-core/src/assistant_core/events/schemas.py
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    event_id: str = Field(min_length=1, max_length=200)
    event_type: str = Field(min_length=1, max_length=120)
    occurred_at: datetime
    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)
    payload: dict[str, Any]


class EventAccepted(BaseModel):
    event_id: str
    duplicate: bool
```

- [ ] **Step 2: Run the schema tests**

Run:

```powershell
cd assistant-core
uv run pytest tests/unit/test_event_schema.py -q
```

Expected: the first run fails before `schemas.py` exists, then reports `2 passed` after adding the contract.

- [ ] **Step 3: Add table models with explicit ownership**

```python
# assistant-core/src/assistant_core/db/base.py
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema="assistant_core", naming_convention=NAMING)
```

```python
# assistant-core/src/assistant_core/identity/models.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class UserIdentity(Base):
    __tablename__ = "user_identity"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    native_user_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

```python
# assistant-core/src/assistant_core/events/models.py
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from assistant_core.db.base import Base


class EventInbox(Base):
    __tablename__ = "event_inbox"

    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    native_chat_id: Mapped[str | None] = mapped_column(String(200))
    native_message_id: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Job(Base):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    identity_key: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="queued", server_default="queued", index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(120))


class OutboxEvent(Base):
    __tablename__ = "outbox_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Implement idempotent transactional ingestion**

```python
# assistant-core/src/assistant_core/events/service.py
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.events.models import EventInbox, Job
from assistant_core.events.schemas import EventAccepted, EventEnvelope
from assistant_core.identity.models import UserIdentity


async def ingest_event(session: AsyncSession, event: EventEnvelope) -> EventAccepted:
    identity_id = await session.scalar(
        insert(UserIdentity)
        .values(native_user_id=event.native_user_id)
        .on_conflict_do_update(
            index_elements=[UserIdentity.native_user_id],
            set_={"native_user_id": event.native_user_id},
        )
        .returning(UserIdentity.id)
    )
    if identity_id is None:
        raise RuntimeError("identity_upsert_returned_no_id")
    inserted_event_id = await session.scalar(
        insert(EventInbox)
        .values(
            event_id=event.event_id,
            event_type=event.event_type,
            user_id=identity_id,
            native_chat_id=event.native_chat_id,
            native_message_id=event.native_message_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
        )
        .on_conflict_do_nothing(index_elements=[EventInbox.event_id])
        .returning(EventInbox.event_id)
    )
    if inserted_event_id is None:
        await session.commit()
        return EventAccepted(event_id=event.event_id, duplicate=True)
    await session.execute(
        insert(Job)
        .values(
            identity_key=f"event:{event.event_id}",
            kind="process_event",
            payload={"event_id": event.event_id},
        )
        .on_conflict_do_nothing(index_elements=[Job.identity_key])
    )
    await session.commit()
    return EventAccepted(event_id=event.event_id, duplicate=False)
```

```python
# assistant-core/src/assistant_core/api/routes/events.py
from fastapi import APIRouter, Depends, Request, status

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.events.schemas import EventAccepted, EventEnvelope
from assistant_core.events.service import ingest_event

router = APIRouter(prefix="/v1", tags=["events"])


@router.post(
    "/events",
    response_model=EventAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_adapter_signature)],
)
async def accept_event(request: Request, event: EventEnvelope) -> EventAccepted:
    async with request.app.state.session_factory() as session:
        return await ingest_event(session, event)
```

Add `app.include_router(events_router)` to `create_app()` using:

```python
from assistant_core.api.routes.events import router as events_router

app.include_router(events_router)
```

- [ ] **Step 5: Add the exact Alembic environment and initial migration**

```ini
# assistant-core/alembic.ini
[alembic]
script_location = migrations
prepend_sys_path = .
```

```python
# assistant-core/migrations/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from assistant_core.config import get_settings
from assistant_core.db.base import Base
from assistant_core.events import models as event_models  # noqa: F401
from assistant_core.identity import models as identity_models  # noqa: F401

config = context.config
if config.config_file_name and config.get_section("loggers"):
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    if type_ == "schema":
        return name == "assistant_core"
    if type_ == "table":
        return parent_names.get("schema_name") == "assistant_core"
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        version_table_schema="assistant_core",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.execute(text("CREATE SCHEMA IF NOT EXISTS assistant_core"))

        def run_with_connection(sync_connection) -> None:
            context.configure(
                connection=sync_connection,
                target_metadata=target_metadata,
                include_schemas=True,
                include_name=include_name,
                version_table_schema="assistant_core",
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()

        await connection.run_sync(run_with_connection)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
```

```python
# assistant-core/migrations/versions/0001_foundation.py
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS assistant_core")
    op.create_table(
        "user_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("native_user_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_user_identity"),
        sa.UniqueConstraint("native_user_id", name="uq_user_identity_native_user_id"),
        schema="assistant_core",
    )
    op.create_table(
        "event_inbox",
        sa.Column("event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("native_chat_id", sa.String(200)),
        sa.Column("native_message_id", sa.String(200)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["assistant_core.user_identity.id"], name="fk_event_inbox_user_id_user_identity"
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_event_inbox"),
        schema="assistant_core",
    )
    op.create_index("ix_event_inbox_event_type", "event_inbox", ["event_type"], schema="assistant_core")
    op.create_table(
        "job",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_key", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(120)),
        sa.PrimaryKeyConstraint("id", name="pk_job"),
        sa.UniqueConstraint("identity_key", name="uq_job_identity_key"),
        schema="assistant_core",
    )
    op.create_index("ix_job_kind", "job", ["kind"], schema="assistant_core")
    op.create_index("ix_job_status", "job", ["status"], schema="assistant_core")
    op.create_table(
        "outbox_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("aggregate_id", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_event"),
        schema="assistant_core",
    )
    op.create_index("ix_outbox_event_topic", "outbox_event", ["topic"], schema="assistant_core")


def downgrade() -> None:
    op.drop_index("ix_outbox_event_topic", table_name="outbox_event", schema="assistant_core")
    op.drop_table("outbox_event", schema="assistant_core")
    op.drop_index("ix_job_status", table_name="job", schema="assistant_core")
    op.drop_index("ix_job_kind", table_name="job", schema="assistant_core")
    op.drop_table("job", schema="assistant_core")
    op.drop_index("ix_event_inbox_event_type", table_name="event_inbox", schema="assistant_core")
    op.drop_table("event_inbox", schema="assistant_core")
    op.drop_table("user_identity", schema="assistant_core")
```

Update the Dockerfile after the migration directory exists:

```dockerfile
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
```

Run:

```powershell
cd assistant-core
uv run alembic upgrade head
uv run alembic check
```

Expected: the migration applies to an empty assistant database and `alembic check` reports no new upgrade operations.

- [ ] **Step 6: Add isolated PostgreSQL fixtures and test duplicate ingestion**

```python
# assistant-core/tests/conftest.py
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from urllib.parse import urlparse

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.db.base import Base
from assistant_core.events import models as event_models  # noqa: F401
from assistant_core.identity import models as identity_models  # noqa: F401
from assistant_core.main import create_app

TEST_SECRET = "integration-test-secret"


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ["ASSISTANT_TEST_DATABASE_URL"]
    if not urlparse(url.replace("+asyncpg", "")).path.endswith("_test"):
        raise RuntimeError("ASSISTANT_TEST_DATABASE_URL must name a database ending in _test")
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS assistant_core CASCADE"))
        await connection.execute(text("CREATE SCHEMA assistant_core"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS assistant_core CASCADE"))
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(
        database_url=os.environ["ASSISTANT_TEST_DATABASE_URL"],
        adapter_hmac_secret=TEST_SECRET,
    )
    app = create_app(settings)
    app.state.session_factory = session_factory
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


@pytest_asyncio.fixture
async def signed_post(client: httpx.AsyncClient) -> Callable:
    async def post(path: str, payload: dict[str, object]) -> httpx.Response:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        signature = sign_request(TEST_SECRET.encode(), "POST", path, timestamp, body)
        return await client.post(
            path,
            content=body,
            headers={
                "content-type": "application/json",
                "x-assistant-timestamp": timestamp,
                "x-assistant-signature": signature,
            },
        )

    return post
```

```python
# assistant-core/tests/integration/test_event_ingestion.py
from sqlalchemy import func, select

from assistant_core.events.models import EventInbox, Job


async def test_duplicate_event_creates_one_inbox_row_and_one_job(
    signed_post, session_factory
) -> None:
    body = {
        "schema_version": 1,
        "event_id": "evt-duplicate",
        "event_type": "chat.finished",
        "occurred_at": "2026-08-09T12:00:00Z",
        "native_user_id": "user-1",
        "native_chat_id": "chat-1",
        "native_message_id": "message-1",
        "payload": {"message_ids": ["u-1", "message-1"]},
    }
    first = await signed_post("/v1/events", body)
    second = await signed_post("/v1/events", body)

    assert first.status_code == 202
    assert first.json() == {"event_id": "evt-duplicate", "duplicate": False}
    assert second.status_code == 202
    assert second.json() == {"event_id": "evt-duplicate", "duplicate": True}
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EventInbox)) == 1
        assert await session.scalar(select(func.count()).select_from(Job)) == 1


async def test_unsigned_event_creates_no_rows(client, session_factory) -> None:
    response = await client.post(
        "/v1/events",
        json={
            "schema_version": 1,
            "event_id": "unsigned",
            "event_type": "chat.finished",
            "occurred_at": "2026-08-09T12:00:00Z",
            "native_user_id": "user-1",
            "payload": {},
        },
    )
    assert response.status_code == 401
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(EventInbox)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
```

Run:

```powershell
uv run pytest tests/integration/test_event_ingestion.py -q
```

Expected: `2 passed`; unsigned requests return 401 and create no rows, while the HMAC unit tests reject modified bodies.

- [ ] **Step 7: Commit the persistence foundation**

```powershell
git add assistant-core/src/assistant_core/db assistant-core/src/assistant_core/identity assistant-core/src/assistant_core/events assistant-core/src/assistant_core/api/routes/events.py assistant-core/migrations assistant-core/alembic.ini assistant-core/tests
git commit -m "feat: add idempotent event ingestion"
```

### Task 5: Add the PostgreSQL worker claim lifecycle

**Files:**
- Create: `assistant-core/src/assistant_core/jobs/repository.py`
- Create: `assistant-core/src/assistant_core/jobs/worker.py`
- Test: `assistant-core/tests/integration/test_worker.py`

**Interfaces:**
- Consumes: queued `Job` rows.
- Produces: `claim_next_job(session) -> Job | None`, `complete_job(session, job)`, `fail_job(session, job, error_code)`, and a worker process that handles `process_event` as a no-op foundation handler.

- [ ] **Step 1: Write concurrent claim and completion tests**

```python
# assistant-core/tests/integration/test_worker.py
from datetime import UTC, datetime, timedelta

from assistant_core.events.models import Job
from assistant_core.jobs.repository import claim_next_job, complete_job, fail_job


async def seed_job(session_factory, identity_key: str) -> None:
    async with session_factory() as session:
        session.add(
            Job(identity_key=identity_key, kind="process_event", payload={"event_id": identity_key})
        )
        await session.commit()


async def test_two_workers_cannot_claim_the_same_job(session_factory) -> None:
    await seed_job(session_factory, "event:claim-once")
    async with session_factory() as first, session_factory() as second:
        claimed_first = await claim_next_job(first)
        claimed_second = await claim_next_job(second)
        assert claimed_first is not None
        assert claimed_second is None
        await complete_job(first, claimed_first)


async def test_failure_requeues_with_bounded_backoff(session_factory) -> None:
    await seed_job(session_factory, "event:retry")
    async with session_factory() as session:
        claimed = await claim_next_job(session)
        assert claimed is not None
        await fail_job(session, claimed, "handler_failed")
        assert claimed.status == "queued"
        assert claimed.attempts == 1
        assert claimed.last_error_code == "handler_failed"


async def test_stale_running_job_is_reclaimed(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            Job(
                identity_key="event:stale",
                kind="process_event",
                payload={"event_id": "stale"},
                status="running",
                claimed_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
        await session.commit()
        reclaimed = await claim_next_job(session)
        assert reclaimed is not None
        assert reclaimed.identity_key == "event:stale"
```

- [ ] **Step 2: Run the tests and verify repository functions are absent**

Run:

```powershell
cd assistant-core
uv run pytest tests/integration/test_worker.py -q
```

Expected: FAIL because `claim_next_job`, `complete_job`, and `fail_job` are undefined.

- [ ] **Step 3: Implement atomic claim, completion, and retry**

```python
# assistant-core/src/assistant_core/jobs/repository.py
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_core.events.models import Job


async def claim_next_job(session: AsyncSession) -> Job | None:
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=5)
    job = await session.scalar(
        select(Job)
        .where(
            or_(
                and_(Job.status == "queued", Job.available_at <= now),
                and_(Job.status == "running", Job.claimed_at <= stale_before),
            )
        )
        .order_by(Job.available_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    job.status = "running"
    job.claimed_at = datetime.now(UTC)
    await session.commit()
    return job


async def complete_job(session: AsyncSession, job: Job) -> None:
    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.last_error_code = None
    await session.commit()


async def fail_job(session: AsyncSession, job: Job, error_code: str) -> None:
    job.attempts += 1
    job.status = "dead" if job.attempts >= 8 else "queued"
    delay = min(300, 2 ** min(job.attempts, 8))
    job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
    job.last_error_code = error_code[:120]
    await session.commit()
```

- [ ] **Step 4: Implement the foundation worker loop**

```python
# assistant-core/src/assistant_core/jobs/worker.py
import asyncio
import signal

from assistant_core.config import get_settings
from assistant_core.db.session import build_session_factory
from assistant_core.jobs.repository import claim_next_job, complete_job, fail_job


async def handle(kind: str, payload: dict[str, object]) -> None:
    if kind != "process_event":
        raise ValueError("unsupported_job_kind")


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    factory = build_session_factory(get_settings())
    while not stop.is_set():
        async with factory() as session:
            job = await claim_next_job(session)
            if job is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    continue
                continue
            try:
                await handle(job.kind, job.payload)
            except Exception:
                await fail_job(session, job, "handler_failed")
            else:
                await complete_job(session, job)


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 5: Verify worker behavior and commit**

Run:

```powershell
uv run pytest tests/integration/test_worker.py -q
uv run ruff check src tests
uv run mypy src
git add assistant-core/src/assistant_core/jobs assistant-core/tests/integration/test_worker.py
git commit -m "feat: add postgres job worker"
```

Expected: all checks pass and a failed job stores only a bounded error code, never exception payload or message content.

### Task 6: Add the bounded no-op context endpoint and fail-open Open WebUI Filter

**Files:**
- Create: `assistant-core/src/assistant_core/api/routes/context.py`
- Create: `assistant-core/tests/unit/test_context.py`
- Create: `adapters/openwebui/context_filter.py`
- Test: `adapters/openwebui/tests/test_context_filter.py`

**Interfaces:**
- Consumes: `ContextRequest(native_user_id, native_chat_id, native_message_id, request_text, max_tokens)`.
- Produces: `ContextResponse(context_text, token_estimate, sources, degraded)`; the Phase 1 endpoint always returns an empty context block. The Filter leaves the request byte-for-byte equivalent at the Python object level when the response is empty, times out, or fails.

- [ ] **Step 1: Write endpoint and Filter behavior tests**

```python
# assistant-core/tests/unit/test_context.py
import json
import time

from fastapi.testclient import TestClient

from assistant_core.auth.hmac import sign_request
from assistant_core.config import Settings
from assistant_core.main import create_app


def test_foundation_context_is_empty() -> None:
    secret = "context-test-secret"
    client = TestClient(create_app(Settings(adapter_hmac_secret=secret)))
    payload = {
        "native_user_id": "user-1",
        "native_chat_id": "chat-1",
        "native_message_id": "message-1",
        "request_text": "What did we decide?",
        "max_tokens": 4000,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    response = client.post(
        "/v1/context",
        content=body,
        headers={
            "content-type": "application/json",
            "x-assistant-timestamp": timestamp,
            "x-assistant-signature": sign_request(
                secret.encode(), "POST", "/v1/context", timestamp, body
            ),
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "context_text": "",
        "token_estimate": 0,
        "sources": [],
        "degraded": False,
    }
```

```python
# adapters/openwebui/tests/test_context_filter.py
import copy

import httpx
import pytest

from context_filter import Filter


async def async_empty_context(_: dict) -> dict:
    return {"context_text": "", "token_estimate": 0, "sources": [], "degraded": False}


async def async_timeout(_: dict) -> dict:
    raise httpx.ReadTimeout("bounded timeout")


async def async_nonempty_context(_: dict) -> dict:
    return {
        "context_text": "Preferred response style: direct.",
        "token_estimate": 6,
        "sources": [{"source_type": "memory", "source_id": "m-1", "label": "preference"}],
        "degraded": False,
    }


@pytest.mark.asyncio
async def test_empty_context_does_not_modify_prompt(monkeypatch) -> None:
    filter_ = Filter()
    body = {"model": "gemini", "messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)
    monkeypatch.setattr(filter_, "_post_context", async_empty_context)
    result = await filter_.inlet(
        body,
        __user__={"id": "user-1"},
        __metadata__={"chat_id": "chat-1", "message_id": "message-1"},
    )
    assert result == original


@pytest.mark.asyncio
async def test_timeout_does_not_modify_prompt(monkeypatch) -> None:
    filter_ = Filter()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    original = copy.deepcopy(body)
    monkeypatch.setattr(filter_, "_post_context", async_timeout)
    assert await filter_.inlet(body, __user__={"id": "u"}, __metadata__={}) == original


@pytest.mark.asyncio
async def test_nonempty_context_is_inserted_before_latest_request(monkeypatch) -> None:
    filter_ = Filter()
    body = {"messages": [{"role": "user", "content": "hello"}]}
    monkeypatch.setattr(filter_, "_post_context", async_nonempty_context)
    result = await filter_.inlet(body, __user__={"id": "u"}, __metadata__={})
    assert result["messages"][-2]["role"] == "system"
    assert "Preferred response style" in result["messages"][-2]["content"]
    assert result["messages"][-1] == {"role": "user", "content": "hello"}
```

- [ ] **Step 2: Add strict context schemas and empty implementation**

```python
# assistant-core/src/assistant_core/api/routes/context.py
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends

from assistant_core.api.dependencies import require_adapter_signature

router = APIRouter(prefix="/v1", tags=["context"])


class ContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)
    request_text: str = Field(max_length=16_000)
    max_tokens: int = Field(default=4_000, ge=0, le=16_000)


class ContextSource(BaseModel):
    source_type: str
    source_id: str
    label: str


class ContextResponse(BaseModel):
    context_text: str
    token_estimate: int
    sources: list[ContextSource]
    degraded: bool


@router.post(
    "/context",
    response_model=ContextResponse,
    dependencies=[Depends(require_adapter_signature)],
)
async def assemble_context(_: ContextRequest) -> ContextResponse:
    return ContextResponse(context_text="", token_estimate=0, sources=[], degraded=False)
```

Add `app.include_router(context_router)` to `create_app()`.

- [ ] **Step 3: Implement the self-contained global fail-open Context Filter**

```python
# adapters/openwebui/context_filter.py
"""
title: Assistant Context
author: local
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import time

import httpx
from pydantic import BaseModel, Field, SecretStr


class Filter:
    class Valves(BaseModel):
        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: SecretStr = Field(default=SecretStr("development-only-secret"))
        timeout_seconds: float = Field(default=1.5, ge=0.1, le=5.0)
        max_context_tokens: int = Field(default=4_000, ge=0, le=16_000)
        priority: int = Field(default=-100)

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def _post_context(self, payload: dict) -> dict:
        path = "/v1/context"
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
        secret = self.valves.hmac_secret.get_secret_value().encode()
        signature = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        async with httpx.AsyncClient(timeout=self.valves.timeout_seconds) as client:
            response = await client.post(
                f"{self.valves.assistant_core_url.rstrip('/')}{path}",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-assistant-timestamp": timestamp,
                    "x-assistant-signature": signature,
                },
            )
        response.raise_for_status()
        return response.json()

    async def inlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> dict:
        if not __user__ or not body.get("messages"):
            return body
        metadata = __metadata__ or {}
        request_text = str(body["messages"][-1].get("content", ""))[:16_000]
        try:
            response = await self._post_context(
                {
                    "native_user_id": str(__user__["id"]),
                    "native_chat_id": metadata.get("chat_id"),
                    "native_message_id": metadata.get("message_id"),
                    "request_text": request_text,
                    "max_tokens": self.valves.max_context_tokens,
                }
            )
        except Exception:
            return body
        context_text = str(response.get("context_text", "")).strip()
        if not context_text:
            return body
        body["messages"].insert(
            -1,
            {"role": "system", "content": f"<assistant_context>\n{context_text}\n</assistant_context>"},
        )
        return body
```

- [ ] **Step 4: Run adapter and endpoint tests**

Run:

```powershell
cd assistant-core
$env:PYTHONPATH="src;../adapters/openwebui"
uv run pytest tests/unit/test_context.py ../adapters/openwebui/tests/test_context_filter.py -q
```

Expected: empty and timeout cases leave the original prompt unchanged; a non-empty response inserts exactly one bounded system message immediately before the latest user request.

- [ ] **Step 5: Commit context plumbing**

```powershell
git add assistant-core/src/assistant_core/api/routes/context.py assistant-core/src/assistant_core/main.py assistant-core/tests/unit/test_context.py adapters/openwebui/context_filter.py adapters/openwebui/tests
git commit -m "feat: add fail-open context adapter"
```

### Task 7: Add lifecycle forwarding and the explicit status Tool

**Files:**
- Create: `assistant-core/src/assistant_core/api/routes/status.py`
- Create: `adapters/openwebui/lifecycle_event.py`
- Create: `adapters/openwebui/assistant_core_tool.py`
- Test: `adapters/openwebui/tests/test_lifecycle_event.py`
- Test: `adapters/openwebui/tests/test_assistant_core_tool.py`

**Interfaces:**
- Consumes: Open WebUI Event Function arguments (`event`, `__event_id__`, `__event_name__`) and Tool dunder identity arguments.
- Produces: selected lifecycle events forwarded to `/v1/events`, plus `assistant_status()` returning redacted health/job state. Event delivery errors are observable but do not block Open WebUI; Tool errors return an explicit unavailable result.

- [ ] **Step 1: Add event forwarding tests**

```python
# adapters/openwebui/tests/test_lifecycle_event.py
import pytest

from lifecycle_event import Event


def capture_into(items):
    async def capture(path: str, payload: dict) -> dict:
        items.append((path, payload))
        return {"event_id": payload["event_id"], "duplicate": False}

    return capture


async def fail_delivery(_: str, __: dict) -> dict:
    raise ConnectionError("service unavailable")


@pytest.mark.asyncio
async def test_chat_finished_forwards_stable_event_id(monkeypatch) -> None:
    adapter = Event()
    sent = []
    monkeypatch.setattr(adapter, "_post", capture_into(sent))
    await adapter.event(
        {"actor": {"id": "user-1"}, "chat": {"id": "chat-1"}, "message": {"id": "m-1"}},
        __event_id__="native-event-1",
        __event_name__="chat.finished",
    )
    assert sent[0][1]["event_id"] == "native-event-1"
    assert sent[0][1]["native_user_id"] == "user-1"


@pytest.mark.asyncio
async def test_unselected_event_is_ignored(monkeypatch) -> None:
    adapter = Event()
    sent = []
    monkeypatch.setattr(adapter, "_post", capture_into(sent))
    await adapter.event({}, __event_id__="x", __event_name__="auth.login")
    assert sent == []


@pytest.mark.asyncio
async def test_delivery_failure_does_not_block_open_webui(monkeypatch) -> None:
    adapter = Event()
    monkeypatch.setattr(adapter, "_post", fail_delivery)
    await adapter.event(
        {"actor": {"id": "user-1"}},
        __event_id__="native-event-2",
        __event_name__="chat.finished",
    )
```

- [ ] **Step 2: Implement the Event Function with an explicit allowlist**

```python
# adapters/openwebui/lifecycle_event.py
"""
title: Assistant Lifecycle
author: local
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field, SecretStr


class Event:
    class Valves(BaseModel):
        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: SecretStr = Field(default=SecretStr("development-only-secret"))
        timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)

    event_names = {
        "chat.finished",
        "chat.deleted",
        "chat.compacted",
        "message.created",
        "file.uploaded",
        "file.deleted",
        "user.deleted",
    }

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
        signature = hmac.new(
            self.valves.hmac_secret.get_secret_value().encode(),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        async with httpx.AsyncClient(timeout=self.valves.timeout_seconds) as client:
            response = await client.post(
                f"{self.valves.assistant_core_url.rstrip('/')}{path}",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-assistant-timestamp": timestamp,
                    "x-assistant-signature": signature,
                },
            )
        response.raise_for_status()
        return response.json()

    async def event(
        self,
        event: dict,
        __event_id__: str | None = None,
        __event_name__: str | None = None,
        **_: object,
    ) -> None:
        if not __event_id__ or __event_name__ not in self.event_names:
            return
        actor = event.get("actor") or {}
        user_id = actor.get("id") or (event.get("user") or {}).get("id")
        if not user_id:
            return
        chat = event.get("chat") or {}
        message = event.get("message") or {}
        try:
            await self._post(
                "/v1/events",
                {
                    "schema_version": 1,
                    "event_id": __event_id__,
                    "event_type": __event_name__,
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "native_user_id": str(user_id),
                    "native_chat_id": chat.get("id"),
                    "native_message_id": message.get("id"),
                    "payload": event,
                },
            )
        except Exception:
            print(f"assistant_lifecycle_delivery_failed event_id={__event_id__}")
```

The implementation must verify the actual payload shapes captured during Phase 0 before enabling destructive lifecycle handling in later phases. For Phase 1, forwarding is evidence collection plus idempotent queueing only.

- [ ] **Step 3: Add the redacted status endpoint and Tool**

```python
# assistant-core/src/assistant_core/api/routes/status.py
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from assistant_core.api.dependencies import require_adapter_signature
from assistant_core.events.models import Job

router = APIRouter(prefix="/v1", tags=["status"])


class StatusRequest(BaseModel):
    native_user_id: str = Field(min_length=1, max_length=200)
    native_chat_id: str | None = Field(default=None, max_length=200)
    native_message_id: str | None = Field(default=None, max_length=200)


@router.post("/status", dependencies=[Depends(require_adapter_signature)])
async def status(request: Request, _: StatusRequest) -> dict[str, object]:
    async with request.app.state.session_factory() as session:
        queued = await session.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))
        dead = await session.scalar(select(func.count()).select_from(Job).where(Job.status == "dead"))
    return {"status": "ok", "queued_jobs": queued or 0, "dead_jobs": dead or 0}
```

```python
# adapters/openwebui/assistant_core_tool.py
"""
title: Assistant Core
author: local
version: 0.1.0
requirements: httpx
"""

import hashlib
import hmac
import json
import time

import httpx
from pydantic import BaseModel, Field, SecretStr


class Tools:
    class Valves(BaseModel):
        assistant_core_url: str = Field(default="http://assistant-core:8080")
        hmac_secret: SecretStr = Field(default=SecretStr("development-only-secret"))

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        digest = hashlib.sha256(body).hexdigest()
        canonical = f"POST\n{path}\n{timestamp}\n{digest}".encode()
        signature = hmac.new(
            self.valves.hmac_secret.get_secret_value().encode(),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.post(
                f"{self.valves.assistant_core_url.rstrip('/')}{path}",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-assistant-timestamp": timestamp,
                    "x-assistant-signature": signature,
                },
            )
        response.raise_for_status()
        return response.json()

    async def assistant_status(
        self,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> str:
        """Return redacted Assistant Core health and queue counts."""
        metadata = __metadata__ or {}
        try:
            result = await self._post(
                "/v1/status",
                {
                    "native_user_id": str((__user__ or {}).get("id", "unknown")),
                    "native_chat_id": metadata.get("chat_id"),
                    "native_message_id": metadata.get("message_id"),
                },
            )
        except Exception:
            return "Assistant Core is unavailable. Ordinary chat can continue without custom context."
        return (
            f"Assistant Core: {result['status']}; "
            f"queued jobs: {result['queued_jobs']}; dead jobs: {result['dead_jobs']}."
        )
```

```python
# adapters/openwebui/tests/test_assistant_core_tool.py
import pytest

from assistant_core_tool import Tools


async def healthy_status(_: str, __: dict) -> dict:
    return {"status": "ok", "queued_jobs": 2, "dead_jobs": 0}


async def unavailable_status(_: str, __: dict) -> dict:
    raise ConnectionError("service unavailable")


@pytest.mark.asyncio
async def test_status_is_redacted_and_concise(monkeypatch) -> None:
    tools = Tools()
    monkeypatch.setattr(tools, "_post", healthy_status)
    assert await tools.assistant_status() == "Assistant Core: ok; queued jobs: 2; dead jobs: 0."


@pytest.mark.asyncio
async def test_status_reports_unavailability(monkeypatch) -> None:
    tools = Tools()
    monkeypatch.setattr(tools, "_post", unavailable_status)
    assert "unavailable" in (await tools.assistant_status()).lower()
```

Add `app.include_router(status_router)` to `create_app()`.

- [ ] **Step 4: Verify adapters against captured native payloads**

Run:

```powershell
cd assistant-core
$env:PYTHONPATH="src;../adapters/openwebui"
uv run pytest ../adapters/openwebui/tests/test_lifecycle_event.py ../adapters/openwebui/tests/test_assistant_core_tool.py -q
```

Expected: selected events forward once with stable IDs, unrelated events do not call the service, missing actor identity is ignored, and unavailable status is reported explicitly.

- [ ] **Step 5: Commit the lifecycle and status adapters**

```powershell
git add assistant-core/src/assistant_core/api/routes/status.py assistant-core/src/assistant_core/main.py adapters/openwebui/lifecycle_event.py adapters/openwebui/assistant_core_tool.py adapters/openwebui/tests
git commit -m "feat: connect Open WebUI lifecycle and status"
```

### Task 8: Add observability and a deployable ARM-compatible stack

**Files:**
- Create: `assistant-core/src/assistant_core/observability.py`
- Create: `deploy/.env.assistant.example`
- Create: `deploy/postgres/bootstrap_assistant_role.sql`
- Create: `deploy/compose.assistant.yml`
- Create: `docs/runbooks/assistant-core-operations.md`

**Interfaces:**
- Consumes: deployment secrets and the existing private Docker network.
- Produces: structured redacted logs, `/metrics`, optional OTLP export, health checks, migrations, API, and worker services.

- [ ] **Step 1: Add structured logging and metrics setup**

```python
# assistant-core/src/assistant_core/observability.py
import logging
import uuid

import structlog
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import make_asgi_app
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from assistant_core.config import Settings


def configure_observability(app: FastAPI, settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
    app.mount("/metrics", make_asgi_app())

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
        value = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(correlation_id=value)
        try:
            response = await call_next(request)
            response.headers["x-correlation-id"] = value
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    if settings.otlp_endpoint:
        provider = TracerProvider(
            resource=Resource.create(
                {"service.name": "assistant-core", "deployment.environment": settings.environment}
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.otlp_endpoint,
                    insecure=settings.otlp_endpoint.startswith("http://"),
                )
            )
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
```

Call `configure_observability(app, active_settings)` before adding routers in `create_app()`.

- [ ] **Step 2: Add the deployment environment contract**

```dotenv
# deploy/.env.assistant.example
ASSISTANT_ENVIRONMENT=production
ASSISTANT_DATABASE_URL=
ASSISTANT_ADAPTER_HMAC_SECRET=
ASSISTANT_REQUEST_CLOCK_SKEW_SECONDS=60
ASSISTANT_CONTEXT_TIMEOUT_SECONDS=1.5
ASSISTANT_LOG_LEVEL=INFO
ASSISTANT_OTLP_ENDPOINT=
ASSISTANT_CORE_IMAGE=registry.example/assistant-core:0.1.0
ASSISTANT_PRIVATE_NETWORK=assistant-private
```

- [ ] **Step 3: Create the least-privilege PostgreSQL role and schema**

```sql
-- deploy/postgres/bootstrap_assistant_role.sql
\if :{?assistant_password}
\else
\echo 'assistant_password psql variable is required'
\quit 3
\endif

SELECT 'CREATE ROLE assistant_core LOGIN'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'assistant_core')
\gexec

ALTER ROLE assistant_core PASSWORD :'assistant_password';
CREATE SCHEMA IF NOT EXISTS assistant_core AUTHORIZATION assistant_core;
ALTER SCHEMA assistant_core OWNER TO assistant_core;
GRANT CONNECT ON DATABASE postgres TO assistant_core;
GRANT USAGE, CREATE ON SCHEMA assistant_core TO assistant_core;
ALTER ROLE assistant_core IN DATABASE postgres SET search_path = assistant_core, public;
```

Run once through an administrative PostgreSQL connection:

```powershell
psql $env:ASSISTANT_ADMIN_DATABASE_URL -v assistant_password="$env:ASSISTANT_DATABASE_PASSWORD" -f deploy/postgres/bootstrap_assistant_role.sql
```

Expected: `assistant_core` owns and can migrate its schema; this script grants nothing on unrelated PostgreSQL schemas and cannot affect Open WebUI's separate SQLite state.

- [ ] **Step 4: Add Compose services with migrations as a one-shot dependency**

```yaml
# deploy/compose.assistant.yml
services:
  assistant-migrate:
    image: ${ASSISTANT_CORE_IMAGE}
    command: ["alembic", "upgrade", "head"]
    env_file: .env.assistant
    networks: [assistant-private]
    restart: "no"

  assistant-core:
    image: ${ASSISTANT_CORE_IMAGE}
    env_file: .env.assistant
    command: ["uvicorn", "assistant_core.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
    depends_on:
      assistant-migrate:
        condition: service_completed_successfully
    expose: ["8080"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=3)"]
      interval: 15s
      timeout: 5s
      retries: 5
    networks: [assistant-private]
    restart: unless-stopped

  assistant-worker:
    image: ${ASSISTANT_CORE_IMAGE}
    env_file: .env.assistant
    command: ["python", "-m", "assistant_core.jobs.worker"]
    depends_on:
      assistant-migrate:
        condition: service_completed_successfully
    networks: [assistant-private]
    restart: unless-stopped

networks:
  assistant-private:
    name: ${ASSISTANT_PRIVATE_NETWORK}
    external: true
```

- [ ] **Step 5: Build both deployment architectures**

Run:

```powershell
docker buildx build --platform linux/amd64,linux/arm64 -t registry.example/assistant-core:0.1.0 assistant-core --push
```

Expected: one manifest lists both `linux/amd64` and `linux/arm64`; neither service publishes port 8080 to the public host.

- [ ] **Step 6: Write the operations runbook**

```markdown
# Assistant Core Operations

## Deploy

1. Create the external private network if it does not exist.
2. Create `deploy/.env.assistant` from the example and store secrets through Dokploy.
3. Grant the PostgreSQL role access only to the `assistant_core` schema.
4. Deploy `assistant-migrate`; require exit code 0.
5. Start API and worker; require `/health/ready` and `/metrics` internally.
6. Configure matching HMAC secrets in the three Open WebUI adapters.
7. Import and activate the Lifecycle Event Function and Assistant Core Tool.
8. Activate the Context Filter globally only after its fail-open test succeeds.

## Roll back

1. Disable the Context Filter and Lifecycle Event Function.
2. Restore the preceding API/worker image digest.
3. Do not downgrade the database automatically.
4. Verify native chat, files, web, Terminal, and images.

## Rotate the adapter secret

1. Deploy dual-secret verification for the rotation window.
2. Update all adapter Valves to sign with the new secret.
3. Verify one context request, lifecycle event, and status call.
4. Remove the old verification secret.

## Diagnose

- Liveness failure: inspect process/container state.
- Readiness failure: inspect PostgreSQL reachability and migrations.
- Queued growth: inspect worker state and job error codes.
- Context degradation: keep ordinary chat enabled and inspect correlation IDs.
- Dead jobs: inspect bounded error codes; never dump message contents into logs.
```

- [ ] **Step 7: Commit deployment and observability**

```powershell
git add assistant-core/src/assistant_core/observability.py assistant-core/src/assistant_core/main.py deploy docs/runbooks/assistant-core-operations.md
git commit -m "ops: deploy observable assistant core"
```

### Task 9: Prove Phase 1 end to end and capture the contract for Phase 2

**Files:**
- Create: `ops/smoke_phase1.ps1`
- Create: `docs/contracts/assistant-core-v1.md`
- Modify: `docs/runbooks/phase0-native-baseline.md`

**Interfaces:**
- Consumes: running Open WebUI, API, worker, PostgreSQL, and installed adapters.
- Produces: repeatable Phase 1 smoke evidence and the frozen v1 envelope/context/status contracts consumed by the memory plan.

- [ ] **Step 1: Add an automated service smoke script**

```powershell
# ops/smoke_phase1.ps1
param(
    [Parameter(Mandatory = $true)][string]$AssistantCoreUrl,
    [Parameter(Mandatory = $true)][string]$HmacSecret
)

$ErrorActionPreference = "Stop"
$live = Invoke-RestMethod "$AssistantCoreUrl/health/live"
$ready = Invoke-RestMethod "$AssistantCoreUrl/health/ready"
if ($live.status -ne "ok" -or $ready.status -ne "ready") { throw "Health check failed." }

$event = [ordered]@{
    schema_version = 1
    event_id = "smoke-$([guid]::NewGuid())"
    event_type = "chat.finished"
    occurred_at = (Get-Date).ToUniversalTime().ToString("o")
    native_user_id = "smoke-user"
    native_chat_id = "smoke-chat"
    native_message_id = "smoke-message"
    payload = @{ message_ids = @("smoke-user-message", "smoke-message") }
}
$body = $event | ConvertTo-Json -Depth 10 -Compress
$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
$sha = [System.Security.Cryptography.SHA256]::Create()
$digest = [Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($body))).ToLowerInvariant()
$canonical = "POST`n/v1/events`n$timestamp`n$digest"
$hmac = [System.Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($HmacSecret))
$signature = [Convert]::ToHexString($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($canonical))).ToLowerInvariant()
$headers = @{ "X-Assistant-Timestamp" = $timestamp; "X-Assistant-Signature" = $signature }
$first = Invoke-RestMethod "$AssistantCoreUrl/v1/events" -Method Post -Headers $headers -ContentType "application/json" -Body $body
$second = Invoke-RestMethod "$AssistantCoreUrl/v1/events" -Method Post -Headers $headers -ContentType "application/json" -Body $body
if ($first.duplicate -or -not $second.duplicate) { throw "Idempotency check failed." }
Write-Host "Phase 1 service smoke passed for event $($event.event_id)."
```

- [ ] **Step 2: Run the automated smoke check on the private network**

Run from a trusted host that can reach `assistant-core`:

```powershell
pwsh ./ops/smoke_phase1.ps1 -AssistantCoreUrl http://assistant-core:8080 -HmacSecret $env:ASSISTANT_ADAPTER_HMAC_SECRET
```

Expected: liveness/readiness pass, the first event is new, the replay is marked duplicate, and the worker completes the single job.

- [ ] **Step 3: Perform the Open WebUI behavioral smoke check**

Perform and record these exact checks:

1. With `assistant-core` healthy, send a unique sentence in a new chat. Confirm the Filter adds no visible/system prompt text because Phase 1 context is empty.
2. Confirm one `chat.finished` event appears in `event_inbox` with stable native IDs and one completed `process_event` job.
3. Invoke `assistant_status`; confirm it reports only health and queue counts.
4. Stop `assistant-core`, send another ordinary chat message, and confirm the model replies normally without injected error text.
5. While the service is stopped, invoke `assistant_status`; confirm it explicitly reports unavailability.
6. Restart the service and worker; confirm readiness and queue recovery.
7. Re-run Phase 0 checks for attachments/RAG, web, Terminal, images, history search, and restart persistence.

- [ ] **Step 4: Freeze the Phase 1 contracts**

```markdown
# Assistant Core v1 Contracts

## Authentication

Every adapter request includes Unix seconds in `X-Assistant-Timestamp` and a lowercase hex HMAC-SHA256 in `X-Assistant-Signature`. The canonical value is `METHOD`, path, timestamp, and SHA-256 body digest joined by newline characters. The service permits at most 60 seconds of clock skew.

## Event ingestion

`POST /v1/events` accepts schema version 1 with stable event, type, time, native user/chat/message IDs, and a bounded payload. Replaying an event ID is successful and returns `duplicate: true` without creating another job.

## Context

`POST /v1/context` accepts a request string of at most 16,000 characters and a maximum context budget from 0 to 16,000 tokens. Phase 1 returns no context. Later phases may return bounded source-linked context without changing this envelope.

## Status

`POST /v1/status` returns service state plus queued and dead job counts. It never returns configuration values, secrets, prompts, or stored content.

## Failure rules

Context failure leaves the native prompt unchanged. Event and status failures never claim success. Authentication and authorization failures return 401/403 and create no durable rows.
```

- [ ] **Step 5: Run the complete verification suite**

Run:

```powershell
cd assistant-core
$env:PYTHONPATH="src;../adapters/openwebui"
uv run pytest -q
uv run ruff check src tests ../adapters/openwebui
uv run mypy src
uv run alembic check
docker build -t assistant-core:phase1 .
```

Expected: all tests pass, lint/type checks are clean, Alembic reports no pending model changes, and the image builds.

- [ ] **Step 6: Commit Phase 1 evidence and contract**

```powershell
git add ops/smoke_phase1.ps1 docs/contracts/assistant-core-v1.md docs/runbooks/phase0-native-baseline.md
git commit -m "test: verify assistant core foundation"
```

## Phase 1 exit criteria

Do not begin the external-memory plan until all are true:

- The deployed Open WebUI image/version and effective ConfigVars are recorded without secrets.
- The running version supports Event Functions and the selected lifecycle payloads are captured.
- API and worker run on Oracle ARM64 and remain private.
- A completed turn carries stable user/chat/message/event IDs and creates one idempotent job.
- The context response is empty and produces no prompt pollution.
- Ordinary native chat succeeds while `assistant-core` is deliberately unavailable.
- Writes/status calls fail explicitly when unavailable or unauthenticated.
- Metrics and structured logs expose health and bounded error codes without full content.
- Phase 0 native features still pass after restart.

## Current-source notes

- Open WebUI 0.10.0 introduced Event Functions and documents stable event IDs plus names such as `chat.finished`, `chat.deleted`, `chat.compacted`, `message.created`, `file.uploaded`, and `file.deleted`.
- Filter `inlet()` is the reliable pre-inference hook; a global active Filter runs for every model.
- Persistent ConfigVars can override external Compose environment values after initial startup, so Phase 0 audits both.
- Function code executes inside the Open WebUI server and must be reviewed as trusted code.

References:

- https://docs.openwebui.com/features/extensibility/plugin/functions/event/
- https://docs.openwebui.com/features/extensibility/plugin/functions/filter/
- https://docs.openwebui.com/features/extensibility/plugin/functions/
- https://docs.openwebui.com/reference/env-configuration/
