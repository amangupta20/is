# Open WebUI Assistant Phase 2C Conversation Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic, source-linked hybrid semantic recall for captured Open WebUI conversations without manual imports or dynamic prompt-prefix rewriting.

**Architecture:** Existing `CompletedTurn` rows remain the canonical Assistant Core capture input. The worker deterministically splits both user and assistant messages into bounded role-labelled passages, stores per-user exact-content segments and independent native message references, and enriches them asynchronously with configurable OpenAI-compatible embeddings. Existing personal-context tools become a unified memory/conversation search and bounded source-expansion surface; lexical search remains available whenever embeddings fail.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx, async SQLAlchemy/Alembic, PostgreSQL full-text search, pgvector/`pgvector-python`, structlog, self-contained Open WebUI Tools, pytest, Ruff, mypy.

## Global Constraints

- Preserve the user's unstaged `.gitignore`; never stage, rewrite, commit, or depend on it.
- Open WebUI remains source-unmodified. Use the existing signed Filter, Tool, Event Function, worker, PostgreSQL schema, and Dokploy Compose deployment.
- Optimize for one trusted primary user. Keep strict owner scoping for at most one separately bounded future trusted user; add no enterprise policy, quotas, broker, or release machinery.
- Index both user and assistant messages. Assistant text is conversation evidence, never authoritative personal truth.
- Automatically enqueue every future completed turn and one idempotent backfill over existing non-tombstoned `CompletedTurn` rows. Do not import pre-Phase-2A Open WebUI history in this plan.
- Keep the existing Open WebUI Tool method names and argument shapes. `search_personal_context` and `read_personal_context(memory_source_id)` continue to use opaque UUIDs even when the UUID resolves to a conversation reference.
- Preserve the frozen per-chat profile exactly. Dynamic recall enters the provider prompt only through model-invoked Tool results appended to chat history.
- Use paragraph-first chunks capped at 4,000 characters with 400-character overlap. Empty normalized chunks are skipped. Chunking behavior is versioned as `conversation-v1`.
- Use per-user SHA-256 exact-content reuse. Never deduplicate canonical text across different users.
- Keep embeddings configurable. Initial production settings use the existing Gemini Embedding 2 path at dimension `1536`; send exactly one input per HTTP request.
- Search memory lexically and conversation passages through PostgreSQL full-text plus pgvector cosine similarity. Fuse bounded candidate lists with reciprocal-rank fusion; do not add a reranker in this plan.
- If query embedding fails, return lexical results. If passage embedding fails, preserve lexical segments/references and retry only missing embeddings.
- `chat.deleted` immediately tombstones that user's matching conversation references and completed turns. Search/read exclude tombstoned rows. Physical garbage collection, full fork reconstruction, message-edit reconciliation, and periodic Open WebUI reconciliation are deferred.
- Worker and retrieval logs are metadata-only: stable IDs, role, sizes, counts, model/dimension, duration, mode, scores, tombstones, and safe error codes. Never log message text, search queries, prompts, evidence text, provider response bodies, URLs containing credentials, or secrets.
- Add one representative happy-path test and at most one ordinary fail-open/data-loss test per task. Run the complete suite only at the phase boundary.

---

## User-visible scenarios

1. Chat A contains a distinctive technical discussion. After completion, the worker logs role-labelled passage counts and embedding state without content.
2. In Chat B, a paraphrased `search_personal_context` call returns compact memory/conversation previews with opaque source IDs and provenance labels.
3. `read_personal_context` on a conversation source returns the exact bounded passage plus adjacent bounded references, roles, and native chat/message IDs.
4. If the embedding endpoint is unavailable, the same Tool returns lexical results and reports lexical mode rather than blocking chat.
5. After Chat A is deleted, its references immediately disappear from search/read while independently referenced exact segments remain available.

## File structure

- `assistant-core/src/assistant_core/conversation/models.py`: canonical conversation segments and native message references.
- `assistant-core/src/assistant_core/conversation/chunking.py`: deterministic paragraph-first chunking and content identity.
- `assistant-core/src/assistant_core/conversation/embedder.py`: strict one-input OpenAI-compatible embedding client.
- `assistant-core/src/assistant_core/conversation/repository.py`: lexical persistence, missing-embedding updates, hybrid candidate retrieval, bounded reads, and tombstones.
- `assistant-core/src/assistant_core/conversation/schemas.py`: internal immutable passage, hit, and neighbor values.
- `assistant-core/migrations/versions/0005_conversation_recall.py`: pgvector-backed segment/reference schema, FTS/HNSW indexes, and downgrade.
- `assistant-core/src/assistant_core/jobs/worker.py`: enqueue/route index jobs, bootstrap existing turns, process chat deletion, and emit safe structured logs.
- `assistant-core/src/assistant_core/api/routes/personal_context.py`: unified memory/conversation search and read contracts with lexical fallback.
- `adapters/openwebui/assistant_core_tool.py`: render generic personal-context search/read output while retaining public Tool signatures.

---

### Task 1: Durable bounded conversation passages

**Files:**

- Create: `assistant-core/src/assistant_core/conversation/__init__.py`
- Create: `assistant-core/src/assistant_core/conversation/chunking.py`
- Create: `assistant-core/src/assistant_core/conversation/models.py`
- Create: `assistant-core/src/assistant_core/conversation/schemas.py`
- Create: `assistant-core/src/assistant_core/conversation/repository.py`
- Create: `assistant-core/migrations/versions/0005_conversation_recall.py`
- Modify: `assistant-core/src/assistant_core/db/models.py`
- Modify: `assistant-core/pyproject.toml`
- Test: `assistant-core/tests/unit/test_conversation_repository.py`

**Interfaces:**

- `chunk_message(text: str) -> tuple[str, ...]` returns deterministic nonblank `<=4000`-character chunks with up to 400 characters of overlap.
- `ConversationSegment` is unique by `(user_id, content_sha256, chunking_version)` and stores exact text, generated `search_vector`, nullable `Vector(1536)`, embedding model/dimension/version, and timestamps.
- `ConversationReference` is unique by `(user_id, native_chat_id, native_message_id, role, chunk_ordinal)` and stores segment linkage, role `user|assistant`, ordering, occurrence time, and nullable tombstone time.
- `materialize_turn_passages(session, turn) -> PassageMaterialization` inserts/reuses lexical segments and creates independent references idempotently without calling a provider.
- `PassageMaterialization` contains exact internal segment IDs still missing embeddings plus inserted/reused/reference counts for safe logging.

- [x] Write one failing repository test that materializes both roles, splits a long message, replays idempotently, and reuses an exact per-user segment through a second native reference.
- [x] Run `cd assistant-core; uv run pytest tests/unit/test_conversation_repository.py -q` and confirm failure because the conversation package does not exist.
- [x] Add `pgvector>=0.4,<1` to dependencies, lock it, implement the chunker/models/repository, register models, and add reversible migration `0005_conversation_recall` with FTS and cosine indexes.
- [x] Run `uv run pytest tests/unit/test_conversation_repository.py -q`, `uv run ruff check src tests/unit/test_conversation_repository.py`, `uv run mypy src`, and `uv lock --check`.
- [x] Commit only Task 1 files as `feat: add bounded conversation passage store`.

---

### Task 2: One-input embedding and automatic worker indexing

**Files:**

- Create: `assistant-core/src/assistant_core/conversation/embedder.py`
- Modify: `assistant-core/src/assistant_core/conversation/repository.py`
- Modify: `assistant-core/src/assistant_core/config.py`
- Modify: `assistant-core/src/assistant_core/jobs/worker.py`
- Modify: `deploy/.env.assistant.example`
- Modify: `deploy/compose.assistant.yml`
- Create: `assistant-core/tests/unit/test_conversation_embedder.py`
- Modify: `assistant-core/tests/unit/test_worker_unit.py`
- Modify: `assistant-core/tests/unit/test_deployment_assets.py`

**Interfaces:**

- Settings: optional `embedding_base_url`, secret `embedding_api_key`, `embedding_model`, `embedding_dimension=1536`, and bounded `embedding_timeout_seconds`.
- `OpenAICompatibleEmbedder.embed_one(text: str) -> list[float]` posts one string to `<base>/embeddings`, requests the configured dimension, and strictly validates exactly one finite vector of that dimension.
- `enqueue_missing_conversation_jobs(session) -> int` idempotently inserts `conversation:<turn_uuid>:conversation-v1` jobs for every non-tombstoned existing completed turn.
- Processing `turn.completed.v1` enqueues `extract_memory` and `index_conversation` in the same transaction.
- `index_conversation` first commits lexical segments/references, then embeds only missing segment vectors one at a time. Provider failure leaves lexical rows intact and retries with safe `conversation_embedding_failed` state.
- Worker structured events: `conversation_index_started`, `conversation_index_completed`, `conversation_index_failed`, and `conversation_backfill_enqueued`; content is excluded.

- [x] Write a failing network-free embedder test proving one-input request shape, exact dimension validation, and one safe failure class.
- [x] Extend the worker test to prove completed-turn processing queues both independent jobs and that a later embedding failure does not remove lexical passage rows.
- [x] Implement configuration, client, bootstrap enqueue, worker routing, safe logs, Compose/environment examples, and deployment-contract assertions.
- [x] Run `uv run pytest tests/unit/test_conversation_embedder.py tests/unit/test_worker_unit.py tests/unit/test_deployment_assets.py -q`, then Ruff, mypy, and lock checks for touched source.
- [x] Commit only Task 2 files as `feat: index completed conversations asynchronously`.

---

### Task 3: Hybrid search and bounded neighboring read

**Files:**

- Modify: `assistant-core/src/assistant_core/conversation/repository.py`
- Modify: `assistant-core/src/assistant_core/conversation/schemas.py`
- Modify: `assistant-core/src/assistant_core/api/routes/personal_context.py`
- Modify: `adapters/openwebui/assistant_core_tool.py`
- Modify: `assistant-core/tests/unit/test_personal_context_api.py`
- Modify: `adapters/openwebui/tests/test_assistant_core_tool.py`

**Interfaces:**

- The signed request shapes and Open WebUI Tool method arguments remain unchanged.
- Search response becomes generic: `mode: lexical|hybrid`; each result has `source_id`, `source_type: memory|conversation`, `category`, `role`, `preview`, and native chat/message provenance when available.
- `search_conversation_context(session, user_id, query, query_embedding, limit) -> list[ConversationHit]` queries bounded lexical/vector candidate sets and uses reciprocal-rank fusion with deterministic tie-breaking.
- Memory lexical results and conversation hits share the final bounded response. Active explicit memories retain the higher authority label; ranking does not turn assistant text into personal truth.
- `read_personal_context(memory_source_id)` first resolves an active owned memory, then an active owned conversation reference. A conversation read returns the selected passage plus at most one previous and one next active reference, capped to 12,000 characters total.
- Query embedding errors emit metadata-only `personal_context_search_completed` with `mode=lexical` and never fail the route.

- [x] Extend one API test to cover a paraphrased hybrid conversation hit followed by a bounded neighboring read, while another user's identical text remains inaccessible.
- [x] Add one API assertion that embedder failure returns lexical mode and valid memory/conversation lexical results.
- [x] Update the self-contained Tool test for generic source output while proving public method names/arguments and the fixed unavailable result remain unchanged.
- [x] Implement repository fusion/read, route schemas, metadata-only search logging, and adapter rendering.
- [x] Run `uv run pytest tests/unit/test_personal_context_api.py ../adapters/openwebui/tests/test_assistant_core_tool.py -q`, then Ruff and mypy for touched source.
- [x] Commit only Task 3 files as `feat: add hybrid source-linked conversation recall`.

---

### Task 4: Immediate chat-deletion tombstones

**Files:**

- Modify: `assistant-core/src/assistant_core/conversation/repository.py`
- Modify: `assistant-core/src/assistant_core/jobs/worker.py`
- Modify: `assistant-core/tests/unit/test_conversation_repository.py`
- Modify: `assistant-core/tests/unit/test_worker_unit.py`

**Interfaces:**

- `tombstone_chat(session, user_id, native_chat_id, occurred_at) -> TombstoneResult` marks matching active references and completed turns, is idempotent, and reports reference/turn/orphan counts.
- `process_event` routes `chat.deleted` using only the already validated event owner/chat IDs; malformed or missing IDs use a fixed safe error code.
- Search and read always require `tombstoned_at IS NULL`.
- Worker emits `conversation_chat_tombstoned` with IDs/counts only. It does not physically delete segment text or embeddings in this plan.

- [x] Add one deletion/data-loss test proving a deleted chat's exclusive reference cannot be searched/read while another live reference to the same segment survives.
- [x] Add one worker test proving duplicate `chat.deleted` delivery converges to the same state and logs no content.
- [x] Implement tombstoning and event routing.
- [x] Run `uv run pytest tests/unit/test_conversation_repository.py tests/unit/test_worker_unit.py tests/unit/test_personal_context_api.py -q`, then Ruff and mypy for touched source.
- [x] Commit only Task 4 files as `feat: tombstone deleted conversation sources`.

---

## Phase boundary verification and live pilot

- [x] Run once from `assistant-core`:

```powershell
uv run pytest tests ../adapters/openwebui/tests -q
uv run ruff check src tests ../adapters/openwebui
uv run mypy src
uv lock --check
```

- [x] Scan the exact Phase 2C diff and committed files for credential-like values while excluding lockfile hashes and documented placeholders.
- [x] Use Luna/high for one read-only review of the exact Phase 2C commit range; fix Critical/Important findings only. Four runtime/privacy findings were fixed in `2573af0`; a new local PostgreSQL harness was declined as disproportionate, with the real Dokploy migration/search pilot retained as the database gate.
- [x] Update `.superpowers/sdd/progress.md`, this plan, and the current handoff with exact commits, verification, review, CI/CD state, and the first unfinished manual gate.
- [ ] Push the reviewed `assistant-foundation` commits. Dokploy deploys that upstream branch automatically.
- [ ] Manually verify migration `0005_conversation_recall`, API/worker health, and metadata-only indexing logs without exposing secrets or content.
- [ ] Re-import only `adapters/openwebui/assistant_core_tool.py`; the current Context Filter and Lifecycle Event Function remain installed unless implementation proves they changed.
- [ ] Live scenario: create a distinctive Chat A discussion, wait for indexed-role logs, paraphrase it from new Chat B through search/read, inspect neighbor provenance, delete Chat A, and confirm the source immediately disappears.
- [ ] Evaluate retrieval usefulness and noise before authorizing inspection Tools/dashboard, full-history backfill/fork reconstruction, files/deduplication, topic episodes, or physical garbage collection.

## Deferred but important

- Open WebUI Tool inspection commands for recent indexed sources, index state, tombstones, and recall diagnostics.
- A small authenticated read-only Assistant Core dashboard using the same inspection APIs.
- Pre-Phase-2A full-history import and fork-lineage reconstruction.
- Message edit/delete reconciliation, periodic native-state reconciliation, and 24-hour physical garbage collection.
- Global uploaded-file indexing, canonical file deduplication, topic episodes, and external reranking.
