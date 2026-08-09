FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations ./migrations
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime

RUN groupadd --gid 10001 assistant \
    && useradd --uid 10001 --gid 10001 --no-log-init --create-home assistant

WORKDIR /app

COPY --from=builder --chown=10001:10001 /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

EXPOSE 8080

CMD ["uvicorn", "assistant_core.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
