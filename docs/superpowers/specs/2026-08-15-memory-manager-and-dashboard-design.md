# Design Specification: Memory Manager, Web Dashboard, and In-Chat Tools

**Date:** 2026-08-15  
**Status:** Proposed  
**Branch:** `assistant-foundation`  

---

## 1. Overview & Goals

As the assistant interacts with the user, it extracts durable memories (`preference`, `instruction`, `fact`, `project`, `decision`). Over time, some memories become obsolete, duplicate, or noisy. Currently, modifying or archiving memories requires running direct SQL on Supabase.

This feature adds:
1. **A Standalone Web Dashboard (`/ui`):** A zero-build, responsive, single-page application served directly by Assistant Core, secured by password authentication, allowing visual search, inspection, 1-click archiving, editing, and merging of memories, as well as system index health diagnostics.
2. **In-Chat Open WebUI Tools:** Interactive tool methods (`list_active_memories`, `archive_memory`, `merge_memories`) inside Open WebUI so memory cleanup can also be performed conversationally.
3. **Dedicated Backend Management Endpoints:** Secure, signed/session-authenticated REST endpoints for listing, archiving, updating, and merging memory records.

---

## 2. Security & Authentication Architecture

### 2.1 Password / Admin Secret Authentication
- **Environment Variable:** `ASSISTANT_ADMIN_PASSWORD` (configured in Dokploy). If not set, falls back to `ASSISTANT_HMAC_SECRET`.
- **Login Flow:**
  1. User navigates to `GET /ui`. If no valid session is present, a clean login view is presented.
  2. User inputs the Admin Password.
  3. Client sends `POST /v1/auth/login` with `{ "password": "..." }`.
  4. Server performs constant-time comparison (`hmac.compare_digest`) against the configured secret.
  5. On success, server generates a cryptographic session token (HMAC-SHA256 of session ID + timestamp + secret) and returns it in a secure `HttpOnly`, `SameSite=Strict` cookie and JSON body.
  6. Client can also store the token in `sessionStorage` for bearer header authentication fallback (`x-assistant-session`).
  7. Endpoint `GET /v1/auth/check` verifies session validity.
  8. Endpoint `POST /v1/auth/logout` revokes/clears the session.

### 2.2 Dual Authentication on Management Endpoints
Endpoints under `/v1/personal-context/*` and `/v1/inspection/*` accept either:
1. **Signed HMAC Transport** (used by Open WebUI Filters & Tools: `x-assistant-timestamp` + `x-assistant-signature`).
2. **Session Authentication** (used by the Web Dashboard: `x-assistant-session` header or session cookie).

---

## 3. Backend Endpoints & Repository Extensions

### 3.1 Memory Management Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/ui` | None (serves login or app) | Serves the single-page dashboard HTML/CSS/JS |
| `POST` | `/v1/auth/login` | None | Authenticates password, returns session |
| `GET` | `/v1/auth/check` | Session | Validates active session |
| `POST` | `/v1/auth/logout` | Session | Clears session |
| `POST` | `/v1/personal-context/list` | Session or HMAC | Lists user memories with optional status/category filters |
| `POST` | `/v1/personal-context/archive` | Session or HMAC | Archives a specific memory (`status='archived'`) |
| `POST` | `/v1/personal-context/update` | Session or HMAC | Updates statement text of an existing memory |
| `POST` | `/v1/personal-context/merge` | Session or HMAC | Combines multiple source memories into one new memory and archives sources |

### 3.2 Request / Response Contracts

#### `POST /v1/personal-context/list`
- **Request:**
  ```json
  {
    "native_user_id": "user-uuid",
    "status": "all" | "active" | "archived" | "superseded",
    "category": null | "preference" | "instruction" | "fact" | "project" | "decision",
    "limit": 100
  }
  ```
- **Response:**
  ```json
  {
    "memories": [
      {
        "id": "memory-uuid",
        "category": "preference",
        "statement": "Keep responses concise and direct.",
        "status": "active",
        "confidence": 0.95,
        "created_at": "2026-08-12T10:00:00Z",
        "updated_at": "2026-08-12T10:00:00Z",
        "archived_at": null,
        "evidence_quote": "I prefer brief bullet points.",
        "native_chat_id": "chat-uuid",
        "native_message_id": "msg-uuid",
        "superseded_by_id": null
      }
    ]
  }
  ```

#### `POST /v1/personal-context/archive`
- **Request:**
  ```json
  {
    "native_user_id": "user-uuid",
    "memory_id": "memory-uuid"
  }
  ```
- **Response:**
  ```json
  {
    "status": "ok",
    "archived_id": "memory-uuid",
    "archived_at": "2026-08-15T03:30:00Z"
  }
  ```

#### `POST /v1/personal-context/merge`
- **Request:**
  ```json
  {
    "native_user_id": "user-uuid",
    "source_memory_ids": ["uuid-1", "uuid-2"],
    "target_category": "preference",
    "new_statement": "Prefer concise code examples and avoid lengthy prose unless asked."
  }
  ```
- **Response:**
  ```json
  {
    "status": "ok",
    "created_id": "new-memory-uuid",
    "archived_source_ids": ["uuid-1", "uuid-2"]
  }
  ```

---

## 4. Web Dashboard UI Design (`/ui`)

### 4.1 Technology Stack & Structure
- **Zero Build Step:** Plain HTML5, Modern Vanilla CSS tokens, and Vanilla JavaScript (ES modules) stored in `assistant-core/src/assistant_core/ui/`.
- **FastAPI Delivery:** Served via `HTMLResponse` or mounted static files directly in FastAPI without requiring Node.js, Vite, or npm build stages in Docker. Preserves Oracle ARM64 container build contract.
- **Aesthetic:** Dark/light mode auto-detect, modern clean typography (Inter / system fonts), card-based layout, clear state badges, smooth transitions, and responsive layout.

### 4.2 Views & Components
1. **Login View:**
   - Centered card with Assistant Core logo/title, password input, "Unlock Dashboard" button, error toast.
2. **Dashboard Header & Stats Bar:**
   - Header with active User ID display, live connection status indicator, theme toggle, and Logout button.
   - 4 Live Metric Cards:
     - **Active Memories** (counts per category).
     - **Indexed Conversations** (segments & active refs).
     - **Indexed Library Files** (total & embedded).
     - **Worker Queue Health** (queued & dead jobs).
3. **Memory Manager View (Primary Tab):**
   - **Search & Filter Controls:** Real-time text search input + Category pill filters (`All`, `Preferences`, `Instructions`, `Facts`, `Projects`, `Decisions`) + Status toggle (`Active`, `Archived`, `All`).
   - **Memory Cards Grid:**
     - Category badge (color-coded).
     - Statement text (editable inline or via modal).
     - Metadata footer: Creation timestamp, origin chat link/ID, confidence score.
     - Collapsible **Evidence Quote** accordion.
     - Action toolbar: `Archive` (red icon/button), `Edit` (pencil icon), `Select for Merge` checkbox.
   - **Merge Floating Bar & Modal:**
     - When 2+ memories are selected, a floating bottom action bar appears: `2 memories selected -> Merge`.
     - Clicking open a Merge Modal displaying the original statements side-by-side and a pre-populated combined draft editor.
     - Submitting sends `POST /v1/personal-context/merge` and refreshes the list.
4. **System Diagnostics Tab:**
   - Table of recent indexed conversations and files from `/v1/inspection/recent` and `/v1/inspection/stats`.
   - Detailed queue inspector.

---

## 5. In-Chat Open WebUI Tools

In [`adapters/openwebui/assistant_core_tool.py`](file:///home/aman/projects/openwebui_config/adapters/openwebui/assistant_core_tool.py), add 3 new tool methods:

1. **`list_memories(category: str = "", status: str = "active") -> str`:**
   - Lists memories with their IDs, categories, and preview statements so the user/model can review active memories in chat.
2. **`archive_memory(memory_id: str) -> str`:**
   - Archives the specified memory ID. Returns confirmation or fail-open status.
3. **`merge_memories(source_memory_ids: str, new_statement: str, category: str = "preference") -> str`:**
   - Takes comma-separated UUIDs and a consolidated statement, creates the new record, and archives the sources.

---

## 6. Testing & Verification Plan

1. **API Unit Tests:**
   - Password authentication: valid password issues session, invalid password returns 401, constant-time comparison verified.
   - Memory listing: owner-scoped filtering by status (`active`, `archived`) and category.
   - Archive: active memory transitions to `archived`, sets `archived_at`, excluded from subsequent context generation.
   - Merge: creates new active record, marks old records as archived/superseded with `superseded_by_id`.
2. **Tool Adapter Tests:**
   - Contract tests in `test_assistant_core_tool.py` for `list_memories`, `archive_memory`, `merge_memories`, and fail-open unavailable handling.
3. **Static & Linters:**
   - `ruff check . ../adapters/openwebui` clean.
   - `mypy` strict clean across all source files.
4. **Live Verification Gate:**
   - Deploy via Dokploy.
   - Open `https://assistant-core.domain/ui`, log in with password.
   - Verify active memories display.
   - Archive a noisy memory via UI -> verify it disappears from active list and context filter.
   - Merge two memories via UI -> verify single consolidated memory active.
   - Test in-chat `archive_memory` tool command in Open WebUI.
