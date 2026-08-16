# Open WebUI File Awareness, Markdown Chunking, and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement asynchronous, non-blocking file awareness with Markdown-aware chunking (`markdown-v1`), per-user exact deduplication, hybrid retrieval (FTS + pgvector), fast tombstoning, and comprehensive observability across logs, inspection APIs, and in-chat diagnostics.

**Architecture:** Open WebUI extracts documents via Azure AI Document Intelligence and sends lightweight HMAC-signed file metadata on chat turns/uploads. `assistant-core` enqueues background jobs in PostgreSQL; the worker asynchronously fetches extracted Markdown from Open WebUI's API, chunks it along Markdown AST boundaries into `FileSegment` (deduplicated per user) and `FileReference`, computes 1536d embeddings, and registers execution telemetry. Unified hybrid search fuses conversation and document passages with Reciprocal Rank Fusion.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, async SQLAlchemy 2, PostgreSQL / pgvector / Alembic, httpx, structlog, pytest, Ruff, mypy.

**Spec:** [`docs/superpowers/specs/2026-08-16-openwebui-file-awareness-and-observability-design.md`](file:///home/aman/projects/openwebui_config/docs/superpowers/specs/2026-08-16-openwebui-file-awareness-and-observability-design.md)

## Global Constraints

- Base commit is `28ccb28` on `assistant-foundation`.
- Do not modify Open WebUI source; adapters are self-contained Functions/Tools.
- Chat `inlet()` and `outlet()` MUST remain strictly non-blocking and fail open. Never make synchronous in-process self-HTTP requests inside filter methods.
- Every state transition, worker attempt, and error must be logged with structured metadata (IDs, counts, durations, status codes) with zero document text, passwords, or secrets in logs.
- Document segments are capped at 4,000 characters with 400-character overlap and tagged `chunking_version="markdown-v1"`.
- Chunks preserve parent header breadcrumbs; tables and fenced code blocks remain atomic.
- Segments are deduplicated per user using exact `(user_id, content_sha256, chunking_version)`.
- Follow strict TDD with one independently reviewed commit per task.

---

### Task 1: Markdown-Aware Chunker Engine (`markdown-v1`)

**Files:**
- Create: `assistant-core/src/assistant_core/files/__init__.py`
- Create: `assistant-core/src/assistant_core/files/chunking.py`
- Create: `assistant-core/src/assistant_core/files/schemas.py`
- Test: `assistant-core/tests/unit/test_markdown_chunker.py`

**Interfaces:**
- Consumes: Raw Markdown string.
- Produces: `MarkdownChunk(text: str, header_path: str, chunk_ordinal: int, content_sha256: str)`
- Function: `chunk_markdown(text: str, max_chars: int = 4000, overlap_chars: int = 400) -> list[MarkdownChunk]`

- [ ] **Step 1: Write failing unit test for Markdown-aware chunking**
  - Test header preservation and breadcrumb tagging (e.g. `# Top > ## Sub`).
  - Test table atomicity (tables <= 4000 chars are never sliced midway).
  - Test code block atomicity (fenced code blocks are kept intact).
  - Test long paragraph subdivision with 400-char overlap.
  - Test deterministic SHA-256 calculation.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_markdown_chunker.py -q`
- [ ] **Step 3: Implement `chunking.py` and `schemas.py`**
  - Implement header parsing, table boundary detection, code fence boundary detection, and paragraph fallback.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_markdown_chunker.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(files): add markdown-aware chunking engine v1"`

---

### Task 2: Database Schema & Migration (`0006_file_awareness`)

**Files:**
- Create: `assistant-core/src/assistant_core/files/models.py`
- Create: `assistant-core/migrations/versions/0006_file_awareness.py`
- Modify: `assistant-core/src/assistant_core/db/models.py`
- Test: `assistant-core/tests/unit/test_file_models.py`

**Interfaces:**
- Consumes: SQLAlchemy Base, `assistant_core.user_identity`.
- Produces: `FileSegment`, `FileReference` SQLAlchemy models with pgvector `Vector(1536)` and `TSVECTOR` generated columns.

- [ ] **Step 1: Write failing test for models and migration structure**
  - Verify schema names (`assistant_core`), table constraints, unique keys `(user_id, content_sha256, chunking_version)`, foreign keys, and indexes.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_models.py -q`
- [ ] **Step 3: Implement models and `0006_file_awareness.py` migration**
  - Implement `FileSegment` and `FileReference` models with exact column constraints.
  - Define `0006_file_awareness` migration with HNSW cosine index and GIN search vector index.
  - Register in `db/models.py`.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_models.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(files): add file_segment and file_reference database models and migration"`

---

### Task 3: File Repository (Persistence, Deduplication, Tombstoning, Hybrid Querying)

**Files:**
- Create: `assistant-core/src/assistant_core/files/repository.py`
- Test: `assistant-core/tests/unit/test_file_repository.py`

**Interfaces:**
- Consumes: AsyncSession, `FileSegment`, `FileReference`, `chunk_markdown`.
- Produces:
  - `materialize_file_passages(session, user_id, native_file_id, filename, mime_type, markdown_text) -> FileMaterializationResult`
  - `tombstone_file_references(session, user_id, native_file_id) -> int`
  - `search_file_passages(session, user_id, query_text, query_embedding, limit) -> list[FileHit]`
  - `read_file_passage_context(session, user_id, reference_id) -> FilePassageContext | None`

- [ ] **Step 1: Write failing repository tests**
  - Test passage materialization, segment deduplication (inserting same content under a second file reference reuses segment), and tombstoning on delete.
  - Test lexical + semantic hybrid search candidate retrieval.
  - Test bounded passage reading with ordinal neighbors.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_repository.py -q`
- [ ] **Step 3: Implement `files/repository.py`**
  - Implement idempotent segment creation/lookup, reference creation, soft tombstoning, and FTS/cosine similarity queries.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_repository.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(files): add file repository with exact deduplication and hybrid retrieval"`

---

### Task 4: Asynchronous File Indexing Worker & Open WebUI Client

**Files:**
- Create: `assistant-core/src/assistant_core/files/client.py`
- Modify: `assistant-core/src/assistant_core/config.py`
- Modify: `assistant-core/src/assistant_core/jobs/worker.py`
- Modify: `deploy/.env.assistant.example`
- Modify: `deploy/compose.assistant.yml`
- Test: `assistant-core/tests/unit/test_file_worker.py`

**Interfaces:**
- Consumes: `Job(kind="index_file", payload={"file_id": ..., "user_id": ..., "filename": ...})`, `OpenAICompatibleEmbedder`.
- Produces: Worker execution handler that calls Open WebUI file API, chunks markdown, saves passages, calculates missing embeddings, and logs structured stage metrics.

- [ ] **Step 1: Write failing test for file indexing worker**
  - Mock Open WebUI file API response (returning Azure-extracted markdown).
  - Test successful job execution, missing embedding generation, retry handling on HTTP 500, and dead job logging on 404/max attempts.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_worker.py -q`
- [ ] **Step 3: Implement Open WebUI client and worker job handler**
  - Add settings: `open_webui_url`, `open_webui_api_key`, `file_indexing_timeout_seconds`.
  - Implement `fetch_openwebui_file_content(url, key, file_id)`.
  - Add `process_index_file_job` in `worker.py` with structured logging.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_file_worker.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(worker): add asynchronous file indexing job handler with telemetry"`

---

### Task 5: Observability & Diagnostics API (`/v1/inspection/files/*`)

**Files:**
- Modify: `assistant-core/src/assistant_core/api/routes/inspection.py`
- Modify: `assistant-core/src/assistant_core/main.py`
- Test: `assistant-core/tests/unit/test_inspection_api.py`

**Interfaces:**
- Consumes: Signed HMAC request with user identity.
- Produces:
  - `POST /v1/inspection/files/stats` -> `{ total_files, active_files, tombstoned_files, total_segments, embedded_segments, lexical_segments, reused_segments, queued_jobs, dead_jobs, last_indexed_at }`
  - `POST /v1/inspection/files/recent` -> list of recent files with status and chunk counts.
  - `POST /v1/inspection/jobs/dead` -> list of failed jobs with error reasons and attempts.

- [ ] **Step 1: Write failing tests for inspection endpoints**
  - Verify stats calculations, recent files listing (excluding content), and dead job error extraction.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_inspection_api.py -q`
- [ ] **Step 3: Implement file inspection routes**
  - Add repository methods and route handlers under `/v1/inspection/files/` and `/v1/inspection/jobs/`.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_inspection_api.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(inspection): add file stats, recent files, and dead jobs inspection endpoints"`

---

### Task 6: Unified Personal Context Search Integration & In-Chat Tools

**Files:**
- Modify: `assistant-core/src/assistant_core/api/routes/personal_context.py`
- Modify: `adapters/openwebui/assistant_core_tool.py`
- Test: `assistant-core/tests/unit/test_personal_context_api.py`
- Test: `adapters/openwebui/tests/test_assistant_core_tool.py`

**Interfaces:**
- Consumes: `POST /v1/personal-context/search`, `POST /v1/personal-context/read`.
- Produces:
  - Unified search output fusing conversation and document hits with Reciprocal Rank Fusion.
  - Open WebUI Tool methods: `show_file_index_status()`, `show_failed_indexing_jobs()`, `show_recent_files(limit)`.

- [ ] **Step 1: Write failing tests for unified search and tool methods**
  - Verify RRF combines conversation and document hits with proper provenance formatting (`[Document: ...]`, `[Conversation: ...]`).
  - Verify tool rendering of file stats and failed jobs.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_personal_context_api.py ../adapters/openwebui/tests/test_assistant_core_tool.py -q`
- [ ] **Step 3: Implement unified RRF ranking and in-chat tools**
  - Update `personal_context.py` to retrieve both candidate sets and merge via RRF.
  - Update `assistant_core_tool.py` with diagnostic methods and formatted hit display.
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_personal_context_api.py ../adapters/openwebui/tests/test_assistant_core_tool.py -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(search): integrate unified conversation and file hybrid retrieval with in-chat diagnostics"`

---

### Task 7: Open WebUI Event Hooks & Outlet File Ingestion

**Files:**
- Modify: `adapters/openwebui/context_filter.py`
- Modify: `adapters/openwebui/lifecycle_event.py`
- Test: `adapters/openwebui/tests/test_context_filter.py`
- Test: `adapters/openwebui/tests/test_lifecycle_event.py`

**Interfaces:**
- Consumes: Open WebUI chat payload `body` and lifecycle events.
- Produces:
  - `outlet()` extracts attached file IDs and includes them in `turn.completed.v1` payload.
  - `lifecycle_event.py` captures `file.deleted` and sends `file.deleted.v1` event to `/v1/events`.

- [ ] **Step 1: Write failing adapter tests**
  - Verify `outlet()` extracts file IDs from `body["files"]` and `messages[-1]["files"]` without blocking.
  - Verify `file.deleted` forwards file UUID to `assistant-core`.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest ../adapters/openwebui/tests -q`
- [ ] **Step 3: Implement adapter event forwarding**
  - Update `context_filter.py` and `lifecycle_event.py` with non-blocking metadata extraction.
- [ ] **Step 4: Run test to confirm GREEN & Full Regression Pass**
  - Run: `cd assistant-core && uv run pytest tests/unit ../adapters/openwebui/tests -q`
  - Run: `cd assistant-core && uv run ruff check src tests && uv run mypy src`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(adapter): forward attached file events and file deletion lifecycle hooks"`

---

## Plan Verification Checklist

- [ ] All 7 tasks implemented with strict TDD.
- [ ] All unit and integration tests passing.
- [ ] Zero lint or typecheck warnings (`ruff`, `mypy`).
- [ ] In-chat tool methods verified (`show_file_index_status`, `show_failed_indexing_jobs`).
- [ ] Deployment assets updated in `deploy/.env.assistant.example` and `compose.assistant.yml`.
