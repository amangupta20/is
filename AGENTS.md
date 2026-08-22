# Agent Instructions

## Overview
This repository contains the architecture and services for a personal Open WebUI Assistant system:
- **`assistant-core/`**: FastAPI backend service managing memory, turn capture, topic episodes, and conversation recall with PostgreSQL (pgvector) and Alembic migrations.
- **`adapters/openwebui/`**: Open WebUI function filter adapter communicating with `assistant-core`.
- **`deploy/`**: Docker compose definitions and Postgres configuration.
- **`docs/`**: Superpowers specs, implementation plans, and operations runbooks.

---

## Development & Testing
From `assistant-core/` (using `uv`):
- **Tests**: `uv run pytest`
- **Linting & Formatting**: `uv run ruff check .` and `uv run ruff format .`
- **Type Checking**: `uv run mypy`
- **Migrations**: `uv run alembic upgrade head`

---

## Codebase Context & Repomix

- Use `repomix` to pack repository context into a single bundle when preparing context for LLM reviews, architecture discussions, or external prompts:
  - Run `repomix` from the root directory to generate `repomix-output.xml` using the configuration in `repomix.config.json`.
  - For markdown output on demand: `repomix --style markdown -o repomix-output.md`.
- Never commit `repomix-output.*` files to git (enforced in `.gitignore`).
