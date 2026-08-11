# Open WebUI Assistant Phase 2B Cache-First Explicit Memory Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` task by task. Each task is a separately reviewable commit. Tests are intentionally limited to the representative behavior named below.

**Goal:** Deliver the first visible personal-memory loop: extract an explicitly stated preference from a completed turn, retain exact source provenance, freeze a small profile for each new chat, and expose source-linked search/read without changing Open WebUI source or rewriting dynamic context every turn.

**Architecture:** Phase 2A's `CompletedTurn` remains the canonical extraction input. A generic OpenAI-compatible cheap task model returns a small strict JSON list of explicit candidates; deterministic repository code applies them idempotently and preserves superseded versions plus evidence. `/v1/context` creates one immutable profile snapshot per native chat, and the Filter inserts that exact snapshot at a fixed early prompt position. Dynamic reads use stable Assistant Core tools whose results append to history.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx, async SQLAlchemy/Alembic, PostgreSQL, self-contained Open WebUI Functions, pytest, Ruff, mypy.

## Global constraints

- Base implementation commit is `686afea0288fc9b430dd6b1d2f18c040c68df3e7` on `assistant-foundation`; the documentation-plan commit follows it.
- Preserve the user's unstaged `.gitignore` change. Never stage, commit, rewrite, or depend on it.
- This is an experimental personal assistant for one primary trusted user. Preserve user IDs for a possible separate trusted family account, but add no enterprise multi-tenancy, quotas, policy engine, broker, or release machinery.
- Open WebUI remains source-unmodified. Use the existing Filter, Tool, signed endpoints, worker, PostgreSQL, and Dokploy Compose assets.
- Cache rule: the global system prompt and tool schemas stay stable; the per-chat profile is created once and returned byte-identically thereafter; changing memory is never automatically injected before each latest user message.
- Reset rule: starting a new chat creates a new profile snapshot. Do not build drop-all controls, context epochs, refresh controls, or prompt-history surgery.
- Extraction v1 handles only explicit user-authored facts, preferences, standing instructions, project context, and corrections. It does not infer personality from one turn, summarize chats, ingest attached files, or treat assistant text as evidence.
- Every accepted candidate must include an exact quote found in the captured user message. Provider output is untrusted and must pass strict local validation before persistence.
- Provider/model failures may delay memory extraction but must not roll back or lose the already persisted completed turn. Queue extraction as its own idempotent job.
- Optional context and search fail open. Requested memory writes/corrections report failure truthfully.
- Normally add one representative happy-path test per task and at most one ordinary failure/fail-open test. Add no combinatorial malformed-input matrix or repeated semantic trials. Run the complete existing suite only at the phase boundary.
- Never log prompts, completed-turn content, evidence quotes, memory text, model output, API keys, credential-bearing URLs, or arbitrary provider errors.
- This plan stops before inferred-memory curation, semantic embeddings/reranking, neighboring chat-message indexing, global-file ingestion, and canonical file deduplication. Those use the stable search/read interfaces in later plans.

---

### Task 1: Source-linked explicit-memory ledger

**Files:**

- Create `assistant-core/src/assistant_core/memory/__init__.py`
- Create `assistant-core/src/assistant_core/memory/models.py`
- Create `assistant-core/src/assistant_core/memory/schemas.py`
- Create `assistant-core/src/assistant_core/memory/repository.py`
- Create `assistant-core/migrations/versions/0004_explicit_memory.py`
- Modify `assistant-core/src/assistant_core/db/models.py`
- Create `assistant-core/tests/unit/test_memory_repository.py`

**Interfaces:**

- `ExplicitMemoryCandidate(key, category, statement, evidence_quote)` is strict/frozen. `key` is a lowercase dotted identifier; category is one of `fact`, `preference`, `instruction`, `project`, or `decision`; statement/evidence are nonblank and bounded.
- `apply_explicit_candidates(session, turn, candidates) -> list[MemoryRecord]` verifies every quote is an exact substring of `turn.user_content`.
- `MemoryRecord` keeps user, normalized key, category, statement, `kind="explicit"`, `confidence=1`, active/superseded/archived state, timestamps, and nullable `superseded_by_id`.
- `MemoryEvidence` links a record to the exact `CompletedTurn` and native user-message ID with its evidence quote.
- One active record exists per `(user_id, key)`. Reapplying the same turn/candidate is a no-op; the same key and statement adds no duplicate; a different statement creates a new record and supersedes the old record without deleting it.
- `ChatProfileSnapshot` stores `(user_id, native_chat_id)` uniquely, exact rendered text, ordered source memory UUIDs, and creation time.

- [ ] Write one repository test covering first insert, idempotent replay, and correction/supersession from two source turns; run it and confirm RED.
- [ ] Implement the three models, strict candidate schema, repository behavior, and reversible `0004_explicit_memory` migration.
- [ ] Run `uv run pytest tests/unit/test_memory_repository.py -q`, `uv run ruff check src tests/unit/test_memory_repository.py`, and `uv run mypy src`.
- [ ] Commit only Task 1 files as `feat: add source-linked explicit memory ledger`.

---

### Task 2: Cheap-model extraction job

**Files:**

- Create `assistant-core/src/assistant_core/memory/extractor.py`
- Modify `assistant-core/src/assistant_core/config.py`
- Modify `assistant-core/src/assistant_core/turns/repository.py`
- Modify `assistant-core/src/assistant_core/jobs/worker.py`
- Modify `deploy/.env.assistant.example`
- Modify `deploy/compose.assistant.yml`
- Create `assistant-core/tests/unit/test_memory_extractor.py`
- Modify `assistant-core/tests/unit/test_worker_unit.py`

**Interfaces:**

- New settings: optional `task_model_base_url`, secret `task_model_api_key`, `task_model_model`, and bounded `task_model_timeout_seconds`. Dokploy worker configuration supplies all four before enabling extraction.
- `TaskModelMemoryExtractor.extract(turn: CompletedTurnData) -> list[ExplicitMemoryCandidate]` sends a fixed extraction rubric followed by the captured user text to an OpenAI-compatible `/chat/completions` endpoint. It requests deterministic JSON, accepts `{"candidates": []}`, and validates the response locally.
- The rubric says: extract only directly stated durable facts/preferences/instructions/projects/decisions; use an exact source quote; produce no candidate for transient requests, pasted logs, quoted third-party text, assistant claims, secrets, or uncertainty.
- Keep `materialize_completed_turn(session, event) -> bool` compatible. Add `get_completed_turn_for_event(session, event_id) -> CompletedTurn`; `process_event` materializes/loads the completed turn and enqueues one `extract_memory` job with identity `memory:<turn_uuid>` in the same commit. It never calls a provider.
- `extract_memory` loads/copies the completed-turn input, releases the read transaction before the network call, invokes the extractor, then applies candidates and completes its own lease. Retry never duplicates records or evidence.

- [ ] Write one extractor test using a network-free `httpx` transport: a valid explicit preference is parsed and an empty candidate list is accepted; run it and confirm RED.
- [ ] Write one worker test proving completed-turn persistence/enqueue is independent of a later extractor failure; run it and confirm RED.
- [ ] Implement the provider client, configuration, idempotent extraction enqueue, and worker routing. Keep provider errors generic and content-free.
- [ ] Run only `uv run pytest tests/unit/test_memory_extractor.py tests/unit/test_worker_unit.py -q`, then Ruff and mypy for touched source.
- [ ] Commit only Task 2 files as `feat: extract explicit memory from completed turns`.

---

### Task 3: Frozen per-chat profile at a cache-stable position

**Files:**

- Create `assistant-core/src/assistant_core/memory/profile.py`
- Modify `assistant-core/src/assistant_core/api/routes/context.py`
- Modify `adapters/openwebui/context_filter.py`
- Modify `assistant-core/tests/unit/test_context_api.py`
- Modify `adapters/openwebui/tests/test_context_filter.py`

**Interfaces:**

- `get_or_create_profile(session, native_user_id, native_chat_id, max_chars=8000) -> ChatProfileSnapshot` upserts/resolves the native identity, selects only active `preference` and `instruction` records in deterministic category/key/UUID order, renders a compact `<user_profile>` block, and stores it once.
- Store an empty snapshot on the first request when no eligible memory exists. This prevents a memory learned later from unexpectedly changing the prefix of an already-started chat.
- A repeated call for the same user/chat returns the stored text and source IDs without consulting newly added memories. A new chat receives a new snapshot.
- `/v1/context` retains the signed request/response envelope. For saved chats it returns the snapshot as `context_text`; unsaved/unknown users and dependency failures return the existing empty/degraded-safe result.
- The Filter inserts nonempty context after all leading native system messages and before the first conversational message—not before the latest user message. It inserts exactly one ephemeral `<assistant_context>` system message and otherwise preserves the body.

- [ ] Add one API test proving a chat snapshot remains byte-identical after a later memory change while a second chat sees the change; run it and confirm RED.
- [ ] Add one Filter test proving fixed-prefix placement after leading system messages and unchanged fail-open behavior; run it and confirm RED.
- [ ] Implement deterministic rendering, transactional create-or-read behavior, endpoint use, and the adapter placement change.
- [ ] Run `uv run pytest tests/unit/test_context_api.py ../adapters/openwebui/tests/test_context_filter.py -q`, then Ruff and mypy for touched source.
- [ ] Commit only Task 3 files as `feat: freeze cache-stable chat profiles`.

---

### Task 4: Inspectable progressive memory search/read

**Files:**

- Create `assistant-core/src/assistant_core/api/routes/personal_context.py`
- Modify `assistant-core/src/assistant_core/main.py`
- Modify `assistant-core/src/assistant_core/memory/repository.py`
- Modify `adapters/openwebui/assistant_core_tool.py`
- Create `assistant-core/tests/unit/test_personal_context_api.py`
- Modify `adapters/openwebui/tests/test_assistant_core_tool.py`

**Interfaces:**

- `POST /v1/personal-context/search` accepts native user/chat/message IDs, a bounded query, and limit `1..10`. In this first slice it ranks active explicit memory by normalized exact/token overlap and returns compact previews with opaque memory source IDs. The response names its mode `lexical`; later semantic chat/file indexes extend the same contract.
- `POST /v1/personal-context/read` accepts one returned memory source ID and returns its statement, category, evidence quote, source native chat/message IDs, and whether neighboring/full-source expansion is currently available.
- `search_personal_context` Tool documentation tells the model to call proactively before making claims about the user's stored preferences, personal facts, projects, or previous decisions. It must state that this first slice searches memory records only; native current-chat file/chat tools remain separate until later source indexes join the same contract. `read_personal_context` expands one memory result. `show_loaded_profile` displays the current frozen profile and source IDs through the existing signed context endpoint.
- Tool reads return one fixed unavailable message on timeout/HTTP/schema failure and never break ordinary chat.

- [ ] Add one API test covering search preview then evidence read for the correct user, with another user's record excluded; run it and confirm RED.
- [ ] Add one adapter test covering successful profile/search/read calls and the single fail-open response; run it and confirm RED.
- [ ] Implement the two signed routes, repository queries, stable response schemas, and three tool methods using the existing HMAC transport pattern.
- [ ] Run `uv run pytest tests/unit/test_personal_context_api.py ../adapters/openwebui/tests/test_assistant_core_tool.py -q`, then Ruff and mypy for touched source.
- [ ] Commit only Task 4 files as `feat: expose source-linked personal context tools`.

---

## Phase boundary verification and live pilot

Run once after all four reviewed commits:

```powershell
cd assistant-core
uv run pytest tests ../adapters/openwebui/tests -q
uv run ruff check src tests ../adapters/openwebui
uv run mypy src
uv lock --check
```

Then push/deploy, apply migration `0004_explicit_memory`, configure the cheap task-model endpoint/model/key, and re-import only the changed Filter and Assistant Core Tool. Perform one manual flow:

1. Chat A: state one explicit response-style preference and complete the turn.
2. Confirm the extraction job completed and the source-linked memory exists without printing its content in service logs.
3. Chat B: inspect the frozen profile and ask a question where the preference changes the answer.
4. Add/correct the preference in Chat B; confirm Chat B's frozen profile does not change.
5. Chat C: confirm the corrected profile appears, use search then read to inspect its source, and send ordinary follow-ups while checking provider cache-hit tokens.
6. Stop and evaluate usefulness before planning inferred-memory curation, semantic transcript retrieval, or global-file ingestion.
