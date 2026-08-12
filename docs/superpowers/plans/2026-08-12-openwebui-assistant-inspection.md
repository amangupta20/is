# Open WebUI Assistant Inspection — Read-Only Diagnostics

**Goal:** Give the trusted user read-only visibility into what conversation recall indexed, what was tombstoned, and what jobs died, without Supabase SQL or payload content.

**Architecture:** Reuse existing signed HMAC transport, `CompletedTurn` + `ConversationSegment/Reference` + `Job` tables. No new migrations, no content logging. Lexical fallback unchanged. Two new signed `POST /v1/inspection/*` routes and two self-contained Open WebUI Tool methods that render metadata only.

**Scope (this slice only):**
* `show_recent_conversations(limit 1..10)` — last N owner-scoped `ConversationReference` rows: native chat/message IDs, role, chunk ordinal, created/ tombstoned, segment reuse counts. No `content`, no `search_vector`, no embedding vectors.
* `show_index_stats()` — counts: total segments, total references, active vs tombstoned references, embedded vs lexical segments, queued vs dead jobs, last indexed turn time. No content.
* Both fail open to the same `_UNAVAILABLE` string as the existing Tool and never break chat.

**Deferred:**
* Per-source inspection (list segments for a chat), file indexing, memory manager archive/merge, history import. Dashboard is deferred — same inspection APIs would back it later.

**Files:**
* Create `assistant-core/src/assistant_core/api/routes/inspection.py`
* Modify `assistant-core/src/assistant_core/main.py` (register router)
* Modify `assistant-core/src/assistant_core/conversation/repository.py` (add metadata queries)
* Modify `adapters/openwebui/assistant_core_tool.py` (add 2 methods)
* Tests: `assistant-core/tests/unit/test_inspection_api.py`, `adapters/openwebui/tests/test_assistant_core_tool.py` (extend)

**Interfaces:**
* Signed request shapes reuse `PersonalContextSearchRequest` identity envelope (`native_user_id`, `native_chat_id`, `native_message_id`) plus bounded `limit` for recent.
* `POST /v1/inspection/recent` → `{ results: [{ reference_id, native_chat_id, native_message_id, role, chunk_ordinal, created_at, tombstoned_at }] }`
* `POST /v1/inspection/stats` → `{ total_segments, embedded_segments, total_references, active_references, tombstoned_references, queued_jobs, dead_jobs, last_indexed_at }`
* Tool `show_recent_conversations(limit?)` and `show_index_stats()` use existing HMAC `_signed_json_post`.

**Test plan (minimal, per project rule):**
* One happy-path API test: `assistant_core` owned recent refs returned ordered, other user’s refs excluded, stats counts reflect known fixtures.
* One fail-open API test: inspection routes return 200 with empty results when DB has no rows (or valid fallback) — never expose content.
* One Tool test: successful recent + stats rendering and the preserved `_UNAVAILABLE` on http failure.
