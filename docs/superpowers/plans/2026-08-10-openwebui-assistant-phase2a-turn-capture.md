# Open WebUI Assistant Phase 2A Turn Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture exactly the completed user/assistant pair with stable Open WebUI provenance and persist it as the bounded canonical input for later memory extraction.

**Architecture:** A globally active, fail-open Open WebUI Filter `outlet()` selects messages by current native IDs and emits an idempotent `turn.completed.v1` event through the existing HMAC `/v1/events` contract. The worker strictly validates that event and materializes one `CompletedTurn`; turns above 512 KiB produce a content-free `turn.oversized.v1` marker and no completed-turn row. Lifecycle Event Functions remain metadata-only.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, async SQLAlchemy 2, PostgreSQL/Alembic, httpx, pytest, Ruff, mypy.

## Global Constraints

- Base commit is `5ecb8f30c498ef260645d15abc3d7e2ac9b2dba8` on `assistant-foundation`.
- Preserve the user's unstaged `.gitignore` change; never stage or commit it.
- Open WebUI remains upstream/source-unmodified; adapters are self-contained Functions.
- Ordinary chat and outlet completion fail open on every validation, serialization, timeout, network, or companion error.
- Never log completed-turn content, arbitrary event payloads, HMAC secrets, URLs with credentials, or provider data.
- Forward only the current user-visible user/assistant pair selected by stable IDs; never forward the full message branch, system prompts, raw `output`, tools, sources, usage, files, titles, URLs, or arbitrary metadata.
- The exact serialized `turn.completed.v1` request must be at most `524288` bytes. Oversized turns send only IDs, SHA-256 hashes, UTF-8 byte counts, and `source=openwebui_outlet_filter` in `turn.oversized.v1`.
- Preserve exact HMAC method/path/timestamp/body-digest signing and the existing 60-second verification policy.
- Event and turn writes must be idempotent. No provider, embedding, memory-candidate, or retrieval behavior belongs in Phase 2A.
- Use strict TDD, one independently reviewed commit per task, full regression verification, and ignored reports under `.superpowers/sdd/`.

---

### Task 1: Durable completed-turn model and worker materialization

**Files:**
- Create: `assistant-core/src/assistant_core/turns/__init__.py`
- Create: `assistant-core/src/assistant_core/turns/models.py`
- Create: `assistant-core/src/assistant_core/turns/schemas.py`
- Create: `assistant-core/src/assistant_core/turns/repository.py`
- Create: `assistant-core/migrations/versions/0003_completed_turn.py`
- Create: `assistant-core/tests/unit/test_turn_models.py`
- Create: `assistant-core/tests/unit/test_turn_schemas.py`
- Create: `assistant-core/tests/unit/test_turn_repository.py`
- Create: `assistant-core/tests/integration/test_completed_turn.py`
- Modify: `assistant-core/src/assistant_core/db/models.py`
- Modify: `assistant-core/src/assistant_core/jobs/worker.py`
- Modify: `assistant-core/tests/unit/test_worker_unit.py`
- Modify: `assistant-core/tests/unit/test_deployment_assets.py`

**Interfaces:**
- Consumes: existing `EventInbox`, `Job(kind="process_event", payload={"event_id": ...})`, async sessions, and `complete_job`/`fail_job`.
- Produces: `CompletedTurn`, `CompletedTurnPayload`, `materialize_completed_turn(session, event) -> bool`, and worker routing that materializes only `turn.completed.v1` events.

- [ ] **Step 1: Write failing model and migration tests**

Assert the table is `assistant_core.completed_turn` with exactly: UUID `id`; unique `event_id`; FK `user_id`; native chat/user-message/assistant-message IDs; user and assistant content; 64-character SHA-256 fields; timezone-aware `occurred_at`/`captured_at`; nullable `tombstoned_at`. Assert migration revision `0003_completed_turn`, down revision `0002_event_inbox_jobs`, schema-qualified FK, stable indexes, upgrade, and reverse-order downgrade.

```python
def test_completed_turn_has_stable_provenance_and_content_columns() -> None:
    columns = CompletedTurn.__table__.c
    assert CompletedTurn.__table__.schema == "assistant_core"
    assert columns.event_id.unique and not columns.event_id.nullable
    assert columns.native_chat_id.type.length == 200
    assert columns.native_user_message_id.type.length == 200
    assert columns.native_assistant_message_id.type.length == 200
    assert not columns.user_content.nullable
    assert not columns.assistant_content.nullable
    assert columns.tombstoned_at.nullable
```

- [ ] **Step 2: Run model tests and verify RED**

Run: `uv run pytest tests/unit/test_turn_models.py tests/unit/test_deployment_assets.py -q`

Expected: failure because `CompletedTurn` and migration `0003_completed_turn` do not exist.

- [ ] **Step 3: Implement the model and schema-safe migration**

Use a focused model with no provider or memory fields:

```python
class CompletedTurn(Base):
    __tablename__ = "completed_turn"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assistant_core.user_identity.id"), nullable=False
    )
    native_chat_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    native_user_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    native_assistant_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    user_content: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_content: Mapped[str] = mapped_column(Text, nullable=False)
    user_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    assistant_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Write failing strict-payload tests**

`CompletedTurnPayload` must forbid extras and require exactly `source="openwebui_outlet_filter"`, two message objects with exact roles, IDs `1..200`, Unicode content, lowercase SHA-256 hex, and optional non-negative integer timestamps. It must recompute both UTF-8 hashes, require payload assistant ID equals `EventInbox.native_message_id`, and require all required native IDs.

```python
payload = CompletedTurnPayload.model_validate(event.payload)
assert payload.user_message.role == "user"
assert payload.assistant_message.role == "assistant"
assert sha256(payload.user_message.content.encode()).hexdigest() == payload.user_message.sha256
```

- [ ] **Step 5: Run schema tests and verify RED**

Run: `uv run pytest tests/unit/test_turn_schemas.py -q`

Expected: failure because the strict payload schema is absent.

- [ ] **Step 6: Implement strict payload validation**

Define immutable Pydantic models with `ConfigDict(extra="forbid")`. Reject mismatched hashes, swapped roles, missing/empty IDs, booleans as timestamps, non-lowercase hashes, and any unexpected message/event field. Content may be empty only for the assistant when Open WebUI produced a valid empty visible response; the user content must contain at least one non-whitespace character.

- [ ] **Step 7: Write failing idempotent repository and worker tests**

Tests must prove:

- only `turn.completed.v1` is materialized;
- identical replay returns duplicate without another row;
- mismatched payload/native IDs fail with bounded `invalid_turn_payload` and no content in the error;
- `turn.oversized.v1` and ordinary lifecycle events remain metadata-only no-ops;
- cancellation and `SystemExit` propagate;
- `process_event` loads the exact inbox event named by the job payload;
- no future `extract_memory` job is created in Phase 2A.

```python
inserted = await materialize_completed_turn(session, event)
assert inserted is True
duplicate = await materialize_completed_turn(session, event)
assert duplicate is False
```

- [ ] **Step 8: Run repository/worker tests and verify RED**

Run: `uv run pytest tests/unit/test_turn_repository.py tests/unit/test_worker_unit.py -q`

Expected: failure because materialization and event-aware worker routing do not exist.

- [ ] **Step 9: Implement repository and worker routing**

`materialize_completed_turn` parses the strict payload, cross-checks the inbox native IDs, and uses PostgreSQL `ON CONFLICT DO NOTHING(event_id)`. Refactor worker handling to receive the open session, load `EventInbox` only for `process_event`, and materialize `turn.completed.v1`; keep every existing event as a successful no-op. Convert ordinary validation/data errors to `InvalidTurnPayloadError("invalid_turn_payload")` so the existing worker retry/dead path stores only `handler_failed` or a new fixed safe code—never exception text or content.

- [ ] **Step 10: Add live integration coverage with exact cleanup**

Insert a unique test user/event/job, materialize the turn through the worker path, prove one row and replay idempotency, then delete only rows bearing that test UUID in reverse dependency order. Skip unless `ASSISTANT_TEST_DATABASE_URL` is present.

- [ ] **Step 11: Verify and commit Task 1**

Run:

```powershell
uv run pytest tests/unit/test_turn_models.py tests/unit/test_turn_schemas.py tests/unit/test_turn_repository.py tests/unit/test_worker_unit.py -q
uv run pytest tests/integration/test_completed_turn.py -q
uv run pytest tests ../adapters/openwebui/tests -q
uv run ruff check src tests ../adapters/openwebui
uv run mypy src
uv lock --check
```

Commit: `feat: persist completed turns for memory extraction`

Report: `.superpowers/sdd/phase2a-task1-report.md`

---

### Task 2: Fail-open Open WebUI outlet capture

**Files:**
- Modify: `adapters/openwebui/context_filter.py`
- Modify: `adapters/openwebui/tests/test_context_filter.py`

**Interfaces:**
- Consumes: current Open WebUI outlet body (`chat_id`, current assistant `id`, branch `messages`), `__user__`, `__metadata__.user_id`, `user_message_id`, `assistant_message_id`, and existing HMAC settings.
- Produces: unchanged outlet body plus one signed `/v1/events` `turn.completed.v1` or `turn.oversized.v1` envelope.

- [ ] **Step 1: Write failing exact-selection tests**

Use fixtures containing older branch messages, tool messages, system messages, misleading trailing entries, and private arbitrary fields. Require selection by metadata/current IDs—not `messages[-2:]`—and assert the exact compact JSON contains only the allowed envelope/message fields.

```python
result = anyio.run(filter_.outlet, body, user, metadata)
assert result is body
assert sent["event_type"] == "turn.completed.v1"
assert sent["payload"]["user_message"]["id"] == metadata["user_message_id"]
assert sent["payload"]["assistant_message"]["id"] == body["id"]
assert "messages" not in sent["payload"]
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `uv run pytest ../adapters/openwebui/tests/test_context_filter.py -q`

Expected: failure because `Filter.outlet` is absent.

- [ ] **Step 3: Implement shared signed-post helper and exact capture**

Refactor `_post_context` through a private `_post_signed(path, payload)` without changing the exact existing context request bytes. `outlet()` must:

1. retain the original body object;
2. validate plain-string native IDs and equality across user, metadata, and body;
3. locate exactly the metadata user message and current assistant message by ID/role;
4. accept only user-visible string `content` and optional non-negative plain-integer timestamps;
5. compute lowercase UTF-8 SHA-256 hashes;
6. create deterministic `event_id="turn:v1:" + sha256(source/user/chat/assistant identity).hexdigest()`;
7. sign and await one short `/v1/events` request;
8. catch every ordinary exception and return the original body unchanged, while allowing cancellation and `SystemExit` to propagate.

- [ ] **Step 4: Write failing size/privacy tests**

Prove the exact serialized completed envelope at `524288` bytes is allowed; one byte beyond instead emits `turn.oversized.v1`. The oversized payload contains only source, user/assistant IDs, hashes, and byte counts. Assert raw content and unique sentinels from metadata, system/tool messages, output, sources, usage, URLs, filenames, and HMAC secret never appear in the oversized body or bounded diagnostics.

- [ ] **Step 5: Run size/privacy tests and verify RED**

Run: `uv run pytest ../adapters/openwebui/tests/test_context_filter.py -q`

Expected: failures until exact byte-size switching and metadata-only markers exist.

- [ ] **Step 6: Implement the exact 512 KiB switch**

Serialize the completed envelope using the same compact, sorted, UTF-8 JSON function used for HMAC. If its byte length exceeds `524288`, replace it with an oversized envelope using the same deterministic identity basis and event type `turn.oversized.v1`. Never truncate either message and never fall back to a whole-chat API call.

- [ ] **Step 7: Preserve inlet and failure behavior**

Retain all existing inlet tests byte-for-byte, then add outlet tests for missing IDs, temporary/unsaved chats, mismatched users/chats/assistant IDs, duplicate IDs, non-string content, malformed mappings, timeout/HTTP/JSON/diagnostic failures, cancellation, and `SystemExit`.

- [ ] **Step 8: Verify and commit Task 2**

Run:

```powershell
uv run pytest ../adapters/openwebui/tests/test_context_filter.py -q
uv run pytest tests ../adapters/openwebui/tests -q
uv run ruff check ../adapters/openwebui/context_filter.py ../adapters/openwebui/tests/test_context_filter.py
uv run mypy src
```

Commit: `feat: capture completed Open WebUI turns`

Report: `.superpowers/sdd/phase2a-task2-report.md`

---

## Phase 2A deployment gate

After both reviewed commits are pushed:

1. Deploy assistant-core and run Alembic revision `0003_completed_turn`.
2. Re-import only the updated Context Filter and restore URL/HMAC/`300000` Valves.
3. Exercise new chat, ordinary reply, regeneration, continuation, tool-rich response, attachment turn, and an intentionally oversized synthetic fixture if practical.
4. Read-only verify exact completed-turn counts, non-null IDs, content hashes, no duplicate rows, metadata-only oversized markers, zero test rows, and no content in logs.
5. Stop before provider configuration. Phase 2B will add the provenance-aware memory ledger and deterministic candidate application on top of these captured turns.
