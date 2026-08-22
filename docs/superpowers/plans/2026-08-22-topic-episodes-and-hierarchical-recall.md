# Open WebUI Topic Episodes & Hierarchical Conversation Recall Plan

**Goal:** Deliver asynchronous 3-hour inactivity session summarization into structured Topic Episodes (title, category, summary, decisions, open loops, entities), pgvector/FTS indexing, unified 3-tier hierarchical search, and dashboard inspection.

---

## Technical Tasks

### 1. Database Model & Migration (`0017_topic_episodes`)
- **Files**:
  - `assistant-core/src/assistant_core/episodes/__init__.py`
  - `assistant-core/src/assistant_core/episodes/models.py`
  - `assistant-core/migrations/versions/0017_topic_episodes.py`
  - `assistant-core/src/assistant_core/db/models.py`
  - `assistant-core/tests/unit/test_episode_models.py`

### 2. Extraction Engine & Schemas (`src/assistant_core/episodes/`)
- **Files**:
  - `assistant-core/src/assistant_core/episodes/schemas.py`
  - `assistant-core/src/assistant_core/episodes/extractor.py`
  - `assistant-core/tests/unit/test_episode_extractor.py`

### 3. Repository with FTS + pgvector Cosine Search
- **Files**:
  - `assistant-core/src/assistant_core/episodes/repository.py`
  - `assistant-core/tests/unit/test_episode_repository.py`

### 4. Worker Integration (3-Hour Inactivity Sweep & Compilation Job)
- **Files**:
  - `assistant-core/src/assistant_core/jobs/worker.py`
  - `assistant-core/tests/unit/test_episode_worker.py`

### 5. Hierarchical Retrieval in Personal Context API
- **Files**:
  - `assistant-core/src/assistant_core/api/routes/personal_context.py`
  - `assistant-core/tests/unit/test_personal_context_api.py`

### 6. Admin API & Web Dashboard Tab
- **Files**:
  - `assistant-core/src/assistant_core/api/routes/admin.py`
  - `assistant-core/src/assistant_core/ui/index.html`
  - `assistant-core/src/assistant_core/ui/app.js`
  - `assistant-core/tests/unit/test_admin_api.py`
