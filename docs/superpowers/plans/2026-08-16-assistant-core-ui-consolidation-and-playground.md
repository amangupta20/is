# Assistant Core Admin UI, Data Management, Consolidation, and Search Playground

**Date:** 2026-08-16  
**Status:** Completed & Deployed  
**Branch:** `assistant-foundation`  

---

## 1. Overview & Architecture

This plan documents the delivery of the administrative dashboard, data management controls, memory consolidation engine, and interactive search playground for the **Assistant Core** subsystem.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                             Web Dashboard UI                             │
│                  (Vanilla JS + CSS Design System SPA)                    │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Overview   │  │   Memories   │  │  Documents   │  │Conversations │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘  │
│  ┌──────────────┐  ┌──────────────────────────────────────────────────┐  │
│  │ Worker Jobs  │  │         🎯 Interactive Search Playground          │  │
│  └──────────────┘  └──────────────────────────────────────────────────┘  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ Session Cookie / HMAC Bearer Token
                                     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Admin Router                           │
│                     (/v1/admin/* authenticated endpoints)                 │
├──────────────────────────────────────────────────────────────────────────┤
│ • /v1/admin/overview: Real-time telemetry, dead jobs, active entities   │
│ • /v1/admin/memories/*: CRUD, batch-delete, on-demand consolidation      │
│ • /v1/admin/files/*: Document inspection, segment tree, batch-delete     │
│ • /v1/admin/conversations/*: Turn inspection, segment tree, batch-delete │
│ • /v1/admin/jobs/*: Worker job queue inspection and retry                │
│ • /v1/admin/system/purge: Double-confirmation system clean slate         │
│ • /v1/admin/playground/search: Hybrid RRF search & LLM prompt simulator  │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
┌─────────────────────────────────────┐     ┌──────────────────────────────┐
│        PostgreSQL / pgvector        │     │      LiteLLM Task Model      │
│  (assistant_core tables & vectors)  │     │   (Memory Consolidation LLM) │
└─────────────────────────────────────┘     └──────────────────────────────┘
```

---

## 2. Key Features Delivered

### 2.1 Web Dashboard UI (`/static/` & `/`)
- Single-page application built with responsive vanilla CSS (HSL dark mode theme, CSS grid, accessible cards, micro-animations).
- Token-based admin session authentication with secure `httponly` cookie or `ASSISTANT_ADMIN_TOKEN` fallback.
- Live tabs:
  1. **Overview**: Key entity telemetry counters, character counts, dead job indicators, database connection health.
  2. **Memories**: Table with category badges, confidence gauges, active/tombstoned filter, search, full statement editor, and batch deletion.
  3. **Documents**: Hierarchical view of markdown documents, chunk segment breakdown, character counts, and delete actions.
  4. **Conversations**: Indexed chat turns, role badges, full content inspection, and chunk segment inspector.
  5. **Worker Jobs**: Queue status filter (`queued`, `running`, `completed`, `dead`), retry buttons for dead/failed tasks.
  6. **Search Playground**: Live hybrid search simulator with RRF score breakdowns and prompt context preview.

### 2.2 Batch Data Management & Double-Confirmation Purge
- Checkbox selection across Memories, Documents, and Conversations tables.
- Floating bulk action bar with multi-item deletion modal.
- System Purge modal (`#purge-modal`) requiring explicit `PURGE` confirmation text before dropping user data, protecting against accidental clean slates while supporting staging teardowns.

### 2.3 Memory Consolidation & Conflict Resolution
- **Task Model Consolidator (`consolidator.py`)**: Uses OpenAI-compatible LiteLLM endpoints to detect contradictory statements and version changes among active user memories.
- **Transactional Supersession (`repository.py`)**: Sets `state = 'superseded'`, `superseded_at = now()`, and links `superseded_by_id` to newer authoritative records.
- **24-Hour Scheduling**: Worker executes daily consolidation (`consolidate:{user}:{YYYY-MM-DD}`) rather than per message turn, avoiding pipeline lag.
- **On-Demand Admin UI**: **`⚡ Consolidate`** button triggers instant evaluation across all users or a specific user with live supersession diffs rendered in the modal.

### 2.4 Interactive Context Retrieval & Search Playground
- Hybrid RRF query simulator executing explicit memory lookup, conversation retrieval, and file passage matching.
- **Dual Pane Interface**:
  - **Left**: Ranked match cards showing Reciprocal Rank Fusion scores, vector cosine similarity metrics, and chunk breadcrumbs.
  - **Right**: Exact simulated `<user_profile>` and `<retrieved_context>` prompt blocks with a 1-click clipboard copy utility.

---

## 3. Configuration & Deployment

### Required Environment Variables in `compose.assistant.yml`
```env
# Admin Dashboard Authentication
ASSISTANT_ADMIN_TOKEN=your_secure_admin_password

# Task Model (Extraction & Memory Consolidation)
ASSISTANT_TASK_MODEL_BASE_URL=https://litellm.app.amhl.ovh/v1
ASSISTANT_TASK_MODEL_MODEL=vertex_ai/gemini-3.7-flash
ASSISTANT_TASK_MODEL_API_KEY=your_litellm_key
ASSISTANT_TASK_MODEL_TIMEOUT_SECONDS=15

# Embedding Model (1536d Cosine pgvector)
ASSISTANT_EMBEDDING_BASE_URL=https://litellm.app.amhl.ovh/v1
ASSISTANT_EMBEDDING_MODEL=text-embedding-3-small
ASSISTANT_EMBEDDING_API_KEY=your_litellm_key
ASSISTANT_EMBEDDING_DIMENSION=1536
```

---

## 4. Verification History

| Target | Tests | Status | Commit |
|---|---|---|---|
| Unit & Integration Tests | 374 passed | ✅ Green | `c0d9f45` |
| Static Code Analysis (Ruff) | 0 errors | ✅ Clean | `c0d9f45` |
| Type Verification (Mypy) | 0 errors in 55 files | ✅ Clean | `c0d9f45` |
| Wheel Package Build | 61 assets bundled | ✅ Validated | `c0d9f45` |

---

## 5. Next Feature Expansion Roadmap

### 5.1 🗂️ Project & Folder Context Scoping (Prioritized Retrieval)
- Detect active Open WebUI chat folder / project metadata.
- Boost relevance scores for memories, conversation turns, and files belonging to the active project while keeping general user preferences intact.

### 5.2 ⏳ Temporal Memory & Auto-Expiring Ephemeral Facts
- Add `valid_until` / `expires_at` metadata to `memory_record`.
- Extraction task model tags time-bound context (e.g. interviews, trips, short-term deadlines) and automatically demotes or expires them after the target timeframe passes.

### 5.3 🧠 Topic Episodes & Session Summarization
- Inactivity threshold (e.g. 1 hour) or on-demand tool trigger to compile multi-turn chats into structured Topic Episodes (decisions made, architecture choices, open loops).
- Hierarchical retrieval: search hits high-density Episode summaries first, then drills down to turn-by-turn passages.

### 5.4 🎛️ Persona & Dynamic Directives Manager in Dashboard
- Dashboard UI tab to configure system prompt directives, tone presets, and retrieval formatting rules live without redeploying code.
