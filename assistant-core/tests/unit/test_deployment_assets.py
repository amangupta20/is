"""Static contracts for the local deployment package."""

import re
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

ASSISTANT_CORE = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = ASSISTANT_CORE.parent
COMPOSE_SERVICES = ("assistant-migrate", "assistant-core", "assistant-worker")
LOCAL_ASSISTANT_IMAGE = "${ASSISTANT_IMAGE:-assistant-core:local}"
ASSISTANT_BUILD_BLOCK = "build:\n      context: ../assistant-core\n      dockerfile: Dockerfile"


def read_required(path: Path) -> str:
    """Read a required asset after producing a useful assertion on absence."""
    assert path.is_file(), f"required deployment asset is missing: {path}"
    return path.read_text(encoding="utf-8")


def compose_service_sections(compose: str) -> dict[str, str]:
    """Return each assistant service's Compose section without adjacent sections."""
    heading_pattern = re.compile(
        rf"^  (?P<name>{'|'.join(COMPOSE_SERVICES)}):$", re.MULTILINE
    )
    headings = list(heading_pattern.finditer(compose))
    assert [heading.group("name") for heading in headings] == list(COMPOSE_SERVICES)

    service_block_end = compose.index("\nnetworks:")
    section_ends = [heading.start() for heading in headings[1:]] + [service_block_end]
    return {
        heading.group("name"): compose[heading.start() : section_end]
        for heading, section_end in zip(headings, section_ends, strict=True)
    }


def assert_compose_has_one_git_build_owner(compose: str) -> dict[str, str]:
    """Assert that only migration owns the Git build while all services share its tag."""
    sections = compose_service_sections(compose)

    for section in sections.values():
        assert re.findall(r"^    image: (.+)$", section, re.MULTILINE) == [
            LOCAL_ASSISTANT_IMAGE
        ]

    migration = sections["assistant-migrate"]
    assert migration.count(ASSISTANT_BUILD_BLOCK) == 1
    assert migration.count("pull_policy: build") == 1
    for service in ("assistant-core", "assistant-worker"):
        assert "build:" not in sections[service]
        assert "pull_policy:" not in sections[service]

    return sections


def test_dockerfile_installs_locked_package_after_copying_source() -> None:
    """The runtime image is reproducible and contains every executable input."""
    dockerfile = read_required(ASSISTANT_CORE / "Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.8.14" in dockerfile
    assert dockerfile.index("COPY src") < dockerfile.index(
        "RUN uv sync --frozen --no-dev --no-editable"
    )
    assert re.search(r"^COPY .*alembic\.ini", dockerfile, re.MULTILINE)
    assert "COPY migrations" in dockerfile
    assert 'PATH="/app/.venv/bin:$PATH"' in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert "assistant_core.main:create_app" in dockerfile
    assert '"--factory"' in dockerfile
    assert '"--no-access-log"' in dockerfile


def test_app_factory_import_does_not_construct_an_unused_global_app() -> None:
    """Factory startup must not configure a second unowned tracer provider on import."""
    main_module = read_required(ASSISTANT_CORE / "src" / "assistant_core" / "main.py")

    assert "\napp = create_app()" not in main_module


def test_dockerignore_excludes_credentials_and_local_artifacts() -> None:
    """Local secrets, tests, caches, and build outputs never enter the context."""
    dockerignore = read_required(ASSISTANT_CORE / ".dockerignore").lower()

    for required in (
        ".env",
        ".venv",
        "tests",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        "dist",
        "build",
        ".git",
        "credential",
    ):
        assert required in dockerignore


def test_env_example_has_only_safe_documented_assistant_settings() -> None:
    """The committed environment file contains placeholders rather than credentials."""
    env_example = read_required(REPOSITORY_ROOT / "deploy" / ".env.assistant.example")
    assignments = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert set(assignments) == {
        "ASSISTANT_CONTEXT_TIMEOUT_SECONDS",
        "ASSISTANT_DATABASE_URL",
        "ASSISTANT_EMBEDDING_API_KEY",
        "ASSISTANT_EMBEDDING_BASE_URL",
        "ASSISTANT_EMBEDDING_DIMENSION",
        "ASSISTANT_EMBEDDING_MODEL",
        "ASSISTANT_EMBEDDING_TIMEOUT_SECONDS",
        "ASSISTANT_ENVIRONMENT",
        "ASSISTANT_FILE_INDEXING_TIMEOUT_SECONDS",
        "ASSISTANT_HMAC_SECRET",
        "ASSISTANT_IMAGE",
        "ASSISTANT_LOG_LEVEL",
        "ASSISTANT_NETWORK",
        "ASSISTANT_OPEN_WEBUI_API_KEY",
        "ASSISTANT_OPEN_WEBUI_URL",
        "ASSISTANT_OTLP_ENDPOINT",
        "ASSISTANT_REQUEST_CLOCK_SKEW_SECONDS",
        "ASSISTANT_TASK_MODEL_BASE_URL",
        "ASSISTANT_TASK_MODEL_API_KEY",
        "ASSISTANT_TASK_MODEL_MODEL",
        "ASSISTANT_TASK_MODEL_TIMEOUT_SECONDS",
    }
    assert assignments["ASSISTANT_ENVIRONMENT"] == "production"
    assert "REPLACE" in assignments["ASSISTANT_DATABASE_URL"]
    assert "REPLACE" in assignments["ASSISTANT_HMAC_SECRET"]
    assert "32" in assignments["ASSISTANT_HMAC_SECRET"]
    assert "REPLACE" in assignments["ASSISTANT_TASK_MODEL_BASE_URL"]
    assert "REPLACE" in assignments["ASSISTANT_TASK_MODEL_MODEL"]
    assert not assignments["ASSISTANT_TASK_MODEL_API_KEY"]
    assert int(assignments["ASSISTANT_TASK_MODEL_TIMEOUT_SECONDS"]) > 0
    assert "REPLACE" in assignments["ASSISTANT_EMBEDDING_BASE_URL"]
    assert "REPLACE" in assignments["ASSISTANT_EMBEDDING_MODEL"]
    assert not assignments["ASSISTANT_EMBEDDING_API_KEY"]
    assert assignments["ASSISTANT_EMBEDDING_DIMENSION"] == "1536"
    assert int(assignments["ASSISTANT_EMBEDDING_TIMEOUT_SECONDS"]) > 0
    assert assignments["ASSISTANT_IMAGE"] == "assistant-core:local"
    assert "ASSISTANT_ADAPTER_HMAC_SECRET" not in env_example
    assert "Supavisor" in env_example
    assert "Dokploy" in env_example


def test_compose_builds_one_private_local_image_without_host_ports() -> None:
    """Git Compose builds one hardened local image for migration, API, and worker."""
    compose = read_required(REPOSITORY_ROOT / "deploy" / "compose.assistant.yml")

    sections = assert_compose_has_one_git_build_owner(compose)
    assert "env_file:" not in compose
    for variable in (
        "ASSISTANT_ENVIRONMENT",
        "ASSISTANT_DATABASE_URL",
        "ASSISTANT_HMAC_SECRET",
        "ASSISTANT_REQUEST_CLOCK_SKEW_SECONDS",
        "ASSISTANT_CONTEXT_TIMEOUT_SECONDS",
        "ASSISTANT_LOG_LEVEL",
        "ASSISTANT_OTLP_ENDPOINT",
    ):
        assert all(f"{variable}: ${{{variable}" in section for section in sections.values())
    for service in ("assistant-core", "assistant-worker"):
        for variable in (
            "ASSISTANT_EMBEDDING_BASE_URL",
            "ASSISTANT_EMBEDDING_API_KEY",
            "ASSISTANT_EMBEDDING_MODEL",
            "ASSISTANT_EMBEDDING_DIMENSION",
            "ASSISTANT_EMBEDDING_TIMEOUT_SECONDS",
        ):
            assert f"{variable}: ${{{variable}" in sections[service]
    assert "ports:" not in compose
    assert 'expose:\n      - "8080"' in compose
    assert compose.count("condition: service_completed_successfully") == 2
    assert "alembic upgrade head" in compose
    assert "python -m assistant_core.jobs.worker" in compose
    assert "health/ready" in compose
    assert "--no-access-log" in compose
    assert compose.count("assistant-private") >= 4
    assert "external: true" in compose
    assert "name: ${ASSISTANT_NETWORK:?set ASSISTANT_NETWORK}" in compose
    assert compose.count("read_only: true") == 3
    assert compose.count("no-new-privileges:true") == 3
    assert compose.count("cap_drop:") == 3
    assert compose.count("tmpfs:") == 3


def test_compose_build_contract_rejects_a_second_build_owner() -> None:
    """The one-build-owner contract fails if an API consumer starts building source."""
    compose = read_required(REPOSITORY_ROOT / "deploy" / "compose.assistant.yml")
    mutated_compose = compose.replace(
        "  assistant-core:\n",
        f"  assistant-core:\n    {ASSISTANT_BUILD_BLOCK}\n",
        1,
    )

    with pytest.raises(AssertionError):
        assert_compose_has_one_git_build_owner(mutated_compose)


def test_postgres_bootstrap_is_guarded_idempotent_and_least_privilege() -> None:
    """The optional admin script cannot mutate before password validation."""
    sql = read_required(
        REPOSITORY_ROOT / "deploy" / "postgres" / "bootstrap_assistant_role.sql"
    )
    lowered = sql.lower()

    assert lowered.index(r"\if :{?assistant_password}") < lowered.index("create role")
    assert r"\quit 3" in lowered
    assert "if not exists" in lowered
    assert "alter role assistant_core login" in lowered
    assert "alter role assistant_core password :'assistant_password'" in lowered
    assert "create schema if not exists assistant_core" in lowered
    assert "authorization assistant_core" not in lowered
    assert "set role" not in lowered
    assert "grant connect on database postgres to assistant_core" in lowered
    assert "grant usage, create on schema assistant_core to assistant_core" in lowered
    assert "grant usage on schema extensions to assistant_core" in lowered
    assert "set search_path to assistant_core, extensions, public" in lowered
    assert "drop " not in lowered


def test_operations_runbook_marks_live_work_pending_and_documents_git_recovery() -> None:
    """Operators receive exact commands and no unverified deployment claims."""
    runbook = read_required(REPOSITORY_ROOT / "docs" / "runbooks" / "assistant-core-operations.md")

    required_phrases = (
        "rotate",
        "amd64",
        "arm64",
        "Dokploy",
        "docker compose --env-file deploy/.env.assistant -f deploy/compose.assistant.yml",
        "assistant-migrate",
        "assistant-core",
        "assistant-worker",
        "/health/live",
        "/health/ready",
        "/status",
        "/metrics/",
        "disable",
        "https://github.com/amangupta20/is",
        "assistant-foundation",
        "./deploy/compose.assistant.yml",
        "Docker Compose from Git",
        "generated .env",
        "prior known-good Git commit",
        "never automatically downgraded",
        "no automatic database downgrade",
        "dual-secret verification is not implemented",
        "ordinary Open WebUI chat remains usable",
        "Slice 1F2",
        "pending",
    )
    for phrase in required_phrases:
        assert phrase.lower() in runbook.lower()


def test_committed_deployment_assets_contain_no_credential_bearing_url() -> None:
    """Static deployment files cannot contain a concrete database password and host."""
    assets = "\n".join(
        read_required(path)
        for path in (
            REPOSITORY_ROOT / "deploy" / ".env.assistant.example",
            REPOSITORY_ROOT / "deploy" / "compose.assistant.yml",
            REPOSITORY_ROOT / "deploy" / "postgres" / "bootstrap_assistant_role.sql",
            REPOSITORY_ROOT / "docs" / "runbooks" / "assistant-core-operations.md",
        )
    )

    database_urls = re.findall(r"postgres(?:ql)?(?:\+asyncpg)?://[^\s`]+", assets)
    assert all("REPLACE" in url for url in database_urls)
    assert not re.search(r"(?i)(password|secret)=[^\s\n]*(?!REPLACE)[A-Za-z0-9]{16,}", assets)


def test_completed_turn_migration_has_stable_schema_qualified_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The migration creates and reverses the completed-turn objects explicitly."""
    migration = ASSISTANT_CORE / "migrations" / "versions" / "0003_completed_turn.py"
    assert migration.is_file()

    operations: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def record(name: str):  # type: ignore[no-untyped-def]
        def operation(*args: object, **kwargs: object) -> None:
            operations.append((name, args, kwargs))

        return operation

    fake_op = SimpleNamespace(
        create_table=record("create_table"),
        create_index=record("create_index"),
        drop_index=record("drop_index"),
        drop_table=record("drop_table"),
        f=lambda name: name,
    )
    monkeypatch.setattr("alembic.op", fake_op)
    namespace = runpy.run_path(str(migration))

    assert namespace["revision"] == "0003_completed_turn"
    assert namespace["down_revision"] == "0002_event_inbox_jobs"

    namespace["upgrade"]()
    assert [name for name, _args, _kwargs in operations] == [
        "create_table",
        "create_index",
    ]
    create_table = operations[0]
    assert create_table[1][0] == "completed_turn"
    assert create_table[2]["schema"] == "assistant_core"
    table_objects = create_table[1][1:]
    columns = {
        item.name: item
        for item in table_objects
        if isinstance(item, sa.Column)
    }
    assert list(columns) == [
        "id",
        "event_id",
        "user_id",
        "native_chat_id",
        "native_user_message_id",
        "native_assistant_message_id",
        "user_content",
        "assistant_content",
        "user_content_sha256",
        "assistant_content_sha256",
        "occurred_at",
        "captured_at",
        "tombstoned_at",
    ]
    assert isinstance(columns["id"].type, sa.Uuid) and not columns["id"].nullable
    assert isinstance(columns["user_id"].type, sa.Uuid) and not columns["user_id"].nullable
    for name in (
        "event_id",
        "native_chat_id",
        "native_user_message_id",
        "native_assistant_message_id",
    ):
        assert isinstance(columns[name].type, sa.String)
        assert columns[name].type.length == 200
        assert not columns[name].nullable
    for name in ("user_content", "assistant_content"):
        assert isinstance(columns[name].type, sa.Text)
        assert not columns[name].nullable
    for name in ("user_content_sha256", "assistant_content_sha256"):
        assert isinstance(columns[name].type, sa.String)
        assert columns[name].type.length == 64
        assert not columns[name].nullable
    for name in ("occurred_at", "captured_at", "tombstoned_at"):
        assert isinstance(columns[name].type, sa.DateTime)
        assert columns[name].type.timezone is True
    assert not columns["occurred_at"].nullable
    assert not columns["captured_at"].nullable
    assert columns["captured_at"].server_default is not None
    assert columns["tombstoned_at"].nullable
    unique_constraints = [
        item
        for item in table_objects
        if isinstance(item, sa.UniqueConstraint)
    ]
    assert len(unique_constraints) == 1
    assert unique_constraints[0].name == "uq_completed_turn_event_id"
    assert unique_constraints[0]._pending_colargs == ["event_id"]
    foreign_keys = [
        item for item in table_objects if item.__class__.__name__ == "ForeignKeyConstraint"
    ]
    assert len(foreign_keys) == 1
    assert foreign_keys[0].elements[0].target_fullname == "assistant_core.user_identity.id"
    assert operations[1] == (
        "create_index",
        ("ix_assistant_core_completed_turn_native_chat_id", "completed_turn", ["native_chat_id"]),
        {"unique": False, "schema": "assistant_core"},
    )

    operations.clear()
    namespace["downgrade"]()
    assert operations == [
        (
            "drop_index",
            ("ix_assistant_core_completed_turn_native_chat_id",),
            {"table_name": "completed_turn", "schema": "assistant_core"},
        ),
        ("drop_table", ("completed_turn",), {"schema": "assistant_core"}),
    ]


def test_live_process_event_fixture_creates_and_cleans_exact_inbox_provenance() -> None:
    """The live worker success path must provide the event ID its job routes."""
    worker_integration = read_required(
        ASSISTANT_CORE / "tests" / "integration" / "test_worker.py"
    )

    assert "insert_process_event_fixture(" in worker_integration
    assert 'payload={"event_id": event_id}' in worker_integration
    assert "delete(EventInbox).where(EventInbox.event_id == event_id)" in worker_integration
    assert "delete(UserIdentity).where(" in worker_integration
