"""Static contracts for the local deployment package."""

import re
from pathlib import Path

ASSISTANT_CORE = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = ASSISTANT_CORE.parent


def read_required(path: Path) -> str:
    """Read a required asset after producing a useful assertion on absence."""
    assert path.is_file(), f"required deployment asset is missing: {path}"
    return path.read_text(encoding="utf-8")


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
        "ASSISTANT_ENVIRONMENT",
        "ASSISTANT_HMAC_SECRET",
        "ASSISTANT_IMAGE",
        "ASSISTANT_LOG_LEVEL",
        "ASSISTANT_NETWORK",
        "ASSISTANT_OTLP_ENDPOINT",
        "ASSISTANT_REQUEST_CLOCK_SKEW_SECONDS",
    }
    assert assignments["ASSISTANT_ENVIRONMENT"] == "production"
    assert "REPLACE" in assignments["ASSISTANT_DATABASE_URL"]
    assert "REPLACE" in assignments["ASSISTANT_HMAC_SECRET"]
    assert "32" in assignments["ASSISTANT_HMAC_SECRET"]
    assert "@sha256:REPLACE" in assignments["ASSISTANT_IMAGE"]
    assert "ASSISTANT_ADAPTER_HMAC_SECRET" not in env_example
    assert "Supavisor" in env_example
    assert "Dokploy" in env_example


def test_compose_uses_one_private_immutable_image_without_host_ports() -> None:
    """Migration, API, and worker share a hardened private-network image contract."""
    compose = read_required(REPOSITORY_ROOT / "deploy" / "compose.assistant.yml")

    assert re.findall(
        r"^  (assistant-migrate|assistant-core|assistant-worker):$", compose, re.MULTILINE
    ) == [
        "assistant-migrate",
        "assistant-core",
        "assistant-worker",
    ]
    assert compose.count("image: ${ASSISTANT_IMAGE:?set ASSISTANT_IMAGE}") == 3
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
    assert "revoke all on schema assistant_core from public" in lowered
    assert "grant connect on database postgres to assistant_core" in lowered
    assert "grant usage, create on schema assistant_core to assistant_core" in lowered
    assert "set search_path to assistant_core, public" in lowered
    assert "drop " not in lowered


def test_operations_runbook_marks_live_work_pending_and_documents_recovery() -> None:
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
        "prior image",
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
