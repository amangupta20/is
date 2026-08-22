# Gemini-Native Multimodal Media Understanding Plan

**Goal:** Implement asynchronous multimodal media ingestion (YouTube URLs, audio/video files) using Gemini API via LiteLLM, timestamped segment extraction, pgvector embeddings, unified hybrid search, in-chat tools, and dashboard UI.

---

## Technical Tasks

### 1. Database Schema & Migration (`0018_media_understanding.py`)
- Models: `MediaDocument`, `MediaSegment` in `src/assistant_core/media/models.py`
- Alembic revision `0018_media_understanding.py`
- Unit tests: `tests/unit/test_media_models.py`

### 2. Gemini Multimodal Analysis Engine (`src/assistant_core/media/`)
- Schemas: `MediaAnalysisResult`, `MediaChapter`, `MediaSegmentHit` in `schemas.py`
- Engine: `GeminiMediaAnalyzer` in `analyzer.py` supporting YouTube URLs and media files
- Unit tests: `tests/unit/test_media_analyzer.py`

### 3. Media Repository with Hybrid Search & CRUD
- Repository: `src/assistant_core/media/repository.py`
- FTS + pgvector hybrid search across `MediaSegment`
- Unit tests: `tests/unit/test_media_repository.py`

### 4. Background Worker Integration (`Job(kind="index_media")`)
- Handler: `_handle_index_media` in `src/assistant_core/jobs/worker.py`
- Auto-detection: URL regex in user prompts or audio/video uploads
- Unit tests: `tests/unit/test_media_worker.py`

### 5. Unified Retrieval & API Integration
- Update `/v1/personal-context/search` and `/v1/personal-context/read` in `personal_context.py`
- Add Admin API routes `/v1/admin/media/*` in `admin.py`
- Unit tests in `test_personal_context_api.py` and `test_admin_api.py`

### 6. Tools & Dashboard UI
- Update `adapters/openwebui/assistant_core_tool.py` with media search rendering & `process_media` tool
- Update `index.html` and `app.js` with new **Media** tab
