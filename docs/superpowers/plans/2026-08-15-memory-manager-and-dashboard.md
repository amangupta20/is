# Memory Manager, Web Dashboard, and In-Chat Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide an interactive Memory Manager with password-authenticated web dashboard (`/ui`) and in-chat Open WebUI tools to search, inspect, 1-click archive, edit, and merge durable memories.

**Architecture:** Embedded zero-build SPA served by FastAPI on Assistant Core, secured by constant-time password auth and session tokens, backed by dual-authenticated (Session or HMAC) REST endpoints and extended Open WebUI Tool methods.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (asyncpg), Pydantic v2, Vanilla HTML5/CSS/JS (ES modules), pytest.

**Spec:** [`docs/superpowers/specs/2026-08-15-memory-manager-and-dashboard-design.md`](file:///home/aman/projects/openwebui_config/docs/superpowers/specs/2026-08-15-memory-manager-and-dashboard-design.md)

## Global Constraints

- Standalone zero-build frontend in `assistant-core/src/assistant_core/ui/` (no Node/Vite build stages in Dockerfile).
- Strict constant-time password comparison (`hmac.compare_digest`).
- Dual authentication for management endpoints (`x-assistant-session` or signed HMAC).
- Open WebUI stays completely unforked.
- Bounded fail-open behavior on tool errors without leaking internals or credentials.
- All code formatted with Ruff and strictly typed with Mypy (`strict = true`).

---

### Task 1: Authentication & Session Service

**Files:**
- Create: `assistant-core/src/assistant_core/api/routes/auth.py`
- Modify: `assistant-core/src/assistant_core/config.py`
- Modify: `assistant-core/src/assistant_core/api/dependencies.py`
- Modify: `assistant-core/src/assistant_core/main.py`
- Test: `assistant-core/tests/unit/test_auth_api.py`

**Interfaces:**
- Consumes: `AssistantSettings.admin_password` (or `hmac_secret` fallback) from `assistant_core/config.py`.
- Produces: `POST /v1/auth/login`, `GET /v1/auth/check`, `POST /v1/auth/logout`, and dependency `require_session_or_signature`.

- [x] **Step 1: Write the failing tests for Auth API**

```python
# assistant-core/tests/unit/test_auth_api.py
import pytest
from fastapi.testclient import TestClient
from assistant_core.main import create_app

def test_login_success_and_session_validation():
    app = create_app()
    client = TestClient(app)
    # Login with valid password (development default)
    res = client.post("/v1/auth/login", json={"password": "development-hmac-secret-change-me"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "token" in data
    
    # Check session using token
    check_res = client.get("/v1/auth/check", headers={"x-assistant-session": data["token"]})
    assert check_res.status_code == 200
    assert check_res.json()["authenticated"] is True

def test_login_invalid_password_returns_401():
    app = create_app()
    client = TestClient(app)
    res = client.post("/v1/auth/login", json={"password": "wrong-password"})
    assert res.status_code == 401
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --directory assistant-core pytest tests/unit/test_auth_api.py -v`  
Expected: FAIL (route not found / import error)

- [x] **Step 3: Implement Auth routes and dependency**

Add `admin_password` to `assistant_core/config.py`, create `assistant_core/api/routes/auth.py` with HMAC session token generation & verification, update `require_session_or_signature` in `assistant_core/api/dependencies.py`, and register auth router in `assistant_core/main.py`.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run --directory assistant-core pytest tests/unit/test_auth_api.py -v`  
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add assistant-core/src/assistant_core/ assistant-core/tests/unit/test_auth_api.py
git commit -m "feat(auth): add password login and session authentication"
```

---

### Task 2: Backend Memory Management Endpoints & Repository

**Files:**
- Modify: `assistant-core/src/assistant_core/memory/repository.py`
- Modify: `assistant-core/src/assistant_core/api/routes/personal_context.py`
- Test: `assistant-core/tests/unit/test_personal_context_api.py`

**Interfaces:**
- Consumes: `require_session_or_signature` from `assistant_core/api/dependencies.py`.
- Produces: `POST /v1/personal-context/list`, `POST /v1/personal-context/archive`, `POST /v1/personal-context/update`, `POST /v1/personal-context/merge`.

- [x] **Step 1: Write the failing tests for Memory Management**

```python
# In assistant-core/tests/unit/test_personal_context_api.py
def test_list_archive_and_merge_memories():
    # Test listing active memories with category filtering
    # Test archiving a memory and verifying status='archived' and excluded from active list
    # Test merging two memories into a single consolidated memory and archiving sources
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --directory assistant-core pytest tests/unit/test_personal_context_api.py -v`  
Expected: FAIL (endpoints not found)

- [x] **Step 3: Implement repository methods and routes**

Implement `list_memories`, `archive_memory`, `update_memory`, `merge_memories` in `assistant_core/memory/repository.py`. Expose endpoints in `assistant_core/api/routes/personal_context.py` protected by `require_session_or_signature`.

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run --directory assistant-core pytest tests/unit/test_personal_context_api.py -v`  
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add assistant-core/src/assistant_core/memory/ assistant-core/src/assistant_core/api/routes/ assistant-core/tests/unit/
git commit -m "feat(memory): add list, archive, update, and merge endpoints"
```

---

### Task 3: In-Chat Open WebUI Tools

**Files:**
- Modify: `adapters/openwebui/assistant_core_tool.py`
- Modify: `adapters/openwebui/tests/test_assistant_core_tool.py`

**Interfaces:**
- Consumes: `/v1/personal-context/list`, `/v1/personal-context/archive`, `/v1/personal-context/merge`.
- Produces: Tool methods `list_memories`, `archive_memory`, `merge_memories`.

- [x] **Step 1: Write the failing tests in `test_assistant_core_tool.py`**

```python
# In adapters/openwebui/tests/test_assistant_core_tool.py
def test_tool_list_archive_and_merge_memories():
    # Test list_memories formats markdown list of active memories
    # Test archive_memory sends signed POST and formats confirmation
    # Test merge_memories sends signed POST with comma-separated IDs and formats confirmation
    # Test all fail open to _UNAVAILABLE on transport error
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --directory assistant-core pytest ../adapters/openwebui/tests/test_assistant_core_tool.py -v`  
Expected: FAIL (methods not present)

- [x] **Step 3: Implement tool methods**

Add `list_memories`, `archive_memory`, `merge_memories` to `Tools` class in `adapters/openwebui/assistant_core_tool.py`.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run --directory assistant-core pytest ../adapters/openwebui/tests/test_assistant_core_tool.py -v`  
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add adapters/openwebui/assistant_core_tool.py adapters/openwebui/tests/test_assistant_core_tool.py
git commit -m "feat(adapter): add list_memories, archive_memory, and merge_memories tools"
```

---

### Task 4: Zero-Build Standalone Web Dashboard (`/ui`)

**Files:**
- Create: `assistant-core/src/assistant_core/ui/index.html`
- Create: `assistant-core/src/assistant_core/ui/styles.css`
- Create: `assistant-core/src/assistant_core/ui/app.js`
- Modify: `assistant-core/src/assistant_core/main.py`
- Test: `assistant-core/tests/unit/test_ui_routes.py`

**Interfaces:**
- Consumes: `/v1/auth/login`, `/v1/auth/check`, `/v1/personal-context/list`, `/v1/personal-context/archive`, `/v1/personal-context/update`, `/v1/personal-context/merge`, `/v1/inspection/stats`, `/v1/inspection/recent`.
- Produces: `GET /ui` and static assets route.

- [x] **Step 1: Write the failing tests for UI routing**

```python
# assistant-core/tests/unit/test_ui_routes.py
from fastapi.testclient import TestClient
from assistant_core.main import create_app

def test_ui_index_served():
    app = create_app()
    client = TestClient(app)
    res = client.get("/ui")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Memory Manager" in res.text
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run --directory assistant-core pytest tests/unit/test_ui_routes.py -v`  
Expected: FAIL (404 Not Found)

- [x] **Step 3: Implement Dashboard UI and static mount**

Build modern, responsive `index.html`, `styles.css`, and `app.js` in `assistant-core/src/assistant_core/ui/` with:
- Clean login screen.
- Metric cards for system & memory health.
- Real-time search and category filtering (`All`, `Preferences`, `Instructions`, `Facts`, `Projects`, `Decisions`).
- Memory cards with status badges, evidence quotes, inline archive/edit.
- Multi-select merge bar and modal.
- Recent Activity tab with conversation and file references.
- User identity context switcher.
Mount `/ui` and static assets in `assistant-core/src/assistant_core/main.py`.

- [x] **Step 4: Run test to verify it passes**

Run: `uv run --directory assistant-core pytest tests/unit/test_ui_routes.py -v`  
Expected: PASS

- [x] **Step 5: Run full test suite, linter, and type checks**

```bash
uv run --directory assistant-core pytest
uv run --directory assistant-core pytest ../adapters/openwebui/tests
uv run --directory assistant-core ruff check . ../adapters/openwebui
uv run --directory assistant-core mypy
```

- [x] **Step 6: Commit**

```bash
git add assistant-core/src/assistant_core/ui/ assistant-core/src/assistant_core/main.py assistant-core/tests/unit/test_ui_routes.py
git commit -m "feat(ui): add zero-build standalone memory manager dashboard at /ui"
```
