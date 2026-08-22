# Advanced Artifact Lifecycle, Typst Engine, and Hardened OnlyOffice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase 6 artifact suite: native Typst compilation for publication-grade resumes and technical documents, hardened OnlyOffice Document Server JWT security with atomic callbacks, version diffing and one-click rollback in API and Admin Web UI, and Open WebUI tool adapter extensions.

**Architecture:** `assistant-core` compiles raw Typst markup and structured resume schemas to PDF via the official `typst` engine. `OnlyOfficeManager` verifies HMAC/JWT tokens on Document Server save callbacks with strict lifecycle state transitions (`status=1, 2, 3, 4, 6`). Admin APIs and UI provide unified line-by-line diffing, historical version downloads, and non-destructive revert ($v_N \rightarrow v_{N+1}$).

**Tech Stack:** Python 3.12, FastAPI, `typst>=0.15.0`, `PyJWT`, `reportlab`, async SQLAlchemy 2, PostgreSQL / Alembic, vanilla JS / CSS UI, Open WebUI Tool adapters.

**Spec:** [`docs/superpowers/specs/2026-08-22-advanced-artifact-lifecycle-and-onlyoffice-design.md`](file:///home/aman/projects/openwebui_config/docs/superpowers/specs/2026-08-22-advanced-artifact-lifecycle-and-onlyoffice-design.md)

## Global Constraints

- Do not modify upstream Open WebUI source; adapters are standalone Tool classes.
- All version increments are strictly immutable and append-only ($v_1 \rightarrow v_2 \dots \rightarrow v_N$).
- A failed OnlyOffice save callback (`status=3`) must preserve previous valid versions (non-destructive failure).
- Reverting to a historical version $K$ creates version $N+1$ with content cloned from version $K$.
- Every new function must follow strict TDD with red-green verification.

---

### Task 1: Typst Generation Engine & Schema Support (`typst-v1`)

**Files:**
- Modify: `assistant-core/pyproject.toml`
- Create: `assistant-core/src/assistant_core/artifacts/generators/typst_gen.py`
- Modify: `assistant-core/src/assistant_core/artifacts/generators/__init__.py`
- Modify: `assistant-core/src/assistant_core/artifacts/schemas.py`
- Modify: `assistant-core/src/assistant_core/artifacts/repository.py`
- Test: `assistant-core/tests/unit/test_artifact_generators.py`
- Test: `assistant-core/tests/unit/test_artifact_repository.py`

**Interfaces:**
- Consumes: `typst.compile()`, `ResumeSpec`, `DocumentSpec`.
- Produces: `TypstGenerator.compile_markup(markup: str) -> bytes`, `TypstGenerator.generate_resume(spec: ResumeSpec) -> bytes`.

- [ ] **Step 1: Add `typst>=0.15.0` to `assistant-core/pyproject.toml` and run `uv sync`**
- [ ] **Step 2: Write failing unit test for `TypstGenerator`**
  - In `tests/unit/test_artifact_generators.py`:
    - `test_typst_generator_compiles_raw_markup`: compiles `#set page(paper: "a4")\n= Hello Typst` and verifies `%PDF-` output.
    - `test_typst_generator_creates_resume`: validates structured `ResumeSpec` generation.
- [ ] **Step 3: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_artifact_generators.py -k typst`
- [ ] **Step 4: Implement `ResumeSpec` in `schemas.py` and `TypstGenerator` in `typst_gen.py`**
  - Define `ExperienceItem`, `EducationItem`, `SkillCategory`, `ProjectItem`, `ResumeSpec` in `schemas.py`.
  - Implement Typst template compiler with contact header, experience timeline, skills, and projects in `typst_gen.py`.
  - Hook `TypstGenerator` into `ArtifactRepository._render_binary` when `artifact_type in ("typst", "resume")`.
- [ ] **Step 5: Run tests to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_artifact_generators.py tests/unit/test_artifact_repository.py`
  - Run: `cd assistant-core && uv run ruff check . && uv run mypy`
- [ ] **Step 6: Commit**
  - `git commit -m "feat(artifacts): add Typst generation engine and structured resume compiler"`

---

### Task 2: Hardened OnlyOffice JWT Verification & Webhook Lifecycle

**Files:**
- Modify: `assistant-core/src/assistant_core/artifacts/onlyoffice.py`
- Modify: `assistant-core/src/assistant_core/api/routes/artifacts.py`
- Test: `assistant-core/tests/unit/test_onlyoffice_lifecycle.py`

**Interfaces:**
- Consumes: `Settings.onlyoffice_jwt_secret`, `OnlyOfficeSession`, `ArtifactVersion`.
- Produces: `OnlyOfficeManager.verify_callback_jwt(token: str) -> dict[str, Any]`, `OnlyOfficeManager.handle_callback(...) -> dict[str, Any]`.

- [ ] **Step 1: Write failing unit tests for OnlyOffice JWT and status lifecycle**
  - In `tests/unit/test_onlyoffice_lifecycle.py`:
    - `test_onlyoffice_jwt_signature_validation`: valid token succeeds; forged token raises unauthorized.
    - `test_onlyoffice_status_editing_and_closed`: `status=1` sets `editing`, `status=4` sets `closed`.
    - `test_onlyoffice_status_save_creates_version`: `status=2` and `status=6` downloads file and increments version.
    - `test_onlyoffice_status_error_is_non_destructive`: `status=3` logs error without modifying version.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_onlyoffice_lifecycle.py`
- [ ] **Step 3: Implement JWT verification and lifecycle handling in `onlyoffice.py` and `artifacts.py`**
  - Extract JWT from `Authorization: Bearer` or `payload["token"]`.
  - Validate with `jwt.decode(token, secret, algorithms=["HS256"])`.
  - Update session statuses (`active`, `editing`, `saved`, `error`, `closed`).
- [ ] **Step 4: Run test to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_onlyoffice_lifecycle.py`
  - Run: `cd assistant-core && uv run ruff check . && uv run mypy`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(artifacts): harden OnlyOffice JWT authentication and callback status lifecycle"`

---

### Task 3: Artifact Version History, Diffing & Revert Endpoints

**Files:**
- Modify: `assistant-core/src/assistant_core/artifacts/repository.py`
- Modify: `assistant-core/src/assistant_core/api/routes/artifacts.py`
- Modify: `assistant-core/src/assistant_core/api/routes/admin.py`
- Test: `assistant-core/tests/unit/test_admin_api.py`
- Test: `assistant-core/tests/unit/test_artifacts_api.py`

**Interfaces:**
- Consumes: `Artifact`, `ArtifactVersion`, `difflib`.
- Produces:
  - `GET /v1/artifacts/{id}/versions/{version_num}/download`
  - `GET /v1/admin/artifacts/{id}/diff?v1={vA}&v2={vB}` -> `{ "v1": A, "v2": B, "diff_lines": list[str], "additions": int, "deletions": int }`
  - `POST /v1/admin/artifacts/{id}/revert` -> `{ "target_version_num": K }`

- [ ] **Step 1: Write failing unit tests for historical download, diff, and revert**
  - In `tests/unit/test_artifacts_api.py` and `tests/unit/test_admin_api.py`:
    - `test_download_specific_historical_version`: downloads raw binary for historical version.
    - `test_artifact_version_diff`: computes diff between v1 and v2.
    - `test_artifact_version_revert`: creates v3 cloned from v1.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_artifacts_api.py tests/unit/test_admin_api.py -k "diff or revert or download"`
- [ ] **Step 3: Implement diff calculation and revert in `repository.py`, `artifacts.py`, and `admin.py`**
  - Extract text representations for diff comparison (`difflib.unified_diff`).
  - Implement `revert_to_version(artifact_id, target_version_num, user_id)` in `ArtifactRepository`.
  - Expose routes in `admin.py` and `artifacts.py`.
- [ ] **Step 4: Run tests to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_artifacts_api.py tests/unit/test_admin_api.py`
  - Run: `cd assistant-core && uv run ruff check . && uv run mypy`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(artifacts): add historical version download, unified diffing, and revert API"`

---

### Task 4: Admin Web UI Version History Drawer & Diff Viewer

**Files:**
- Modify: `assistant-core/src/assistant_core/ui/index.html`
- Modify: `assistant-core/src/assistant_core/ui/app.js`
- Modify: `assistant-core/src/assistant_core/ui/styles.css`

**Interfaces:**
- Consumes: Admin API endpoints `/v1/admin/artifacts`, `/v1/admin/artifacts/{id}/diff`, `/v1/admin/artifacts/{id}/revert`.
- Produces: Interactive Version History drawer, Unified Diff modal with syntax highlighting, and 1-click Revert.

- [ ] **Step 1: Add Version History drawer and Diff modal containers in `index.html`**
  - Add `#artifact-versions-modal` and `#artifact-diff-modal` templates.
- [ ] **Step 2: Implement UI methods in `app.js`**
  - `openArtifactVersions(artifactId)`: fetches artifact details and displays version timeline.
  - `viewArtifactDiff(artifactId, v1, v2)`: fetches `/v1/admin/artifacts/{id}/diff` and renders highlighted green/red diff lines.
  - `revertArtifactVersion(artifactId, targetVersionNum)`: prompts confirmation, calls `/v1/admin/artifacts/{id}/revert`, and refreshes grid.
- [ ] **Step 3: Add styling in `styles.css`**
  - Diff viewer styling (`.diff-add`, `.diff-del`, `.diff-line`, `.version-timeline`).
- [ ] **Step 4: Verify UI assets with tests**
  - Run: `cd assistant-core && uv run pytest tests/unit/test_deployment_assets.py`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(ui): add Version History timeline drawer and unified diff viewer to artifacts console"`

---

### Task 5: Open WebUI Adapter Tool Extensions (`create_typst_document`, `create_resume`)

**Files:**
- Modify: `adapters/openwebui/assistant_core_artifacts_tool.py`
- Test: `adapters/openwebui/tests/test_assistant_core_artifacts_tool.py`

**Interfaces:**
- Produces: `create_typst_document(title: str, markup: str, ...) -> str`, `create_resume(title: str, resume_json: str, ...) -> str`.

- [ ] **Step 1: Write failing unit tests for new adapter tools**
  - In `adapters/openwebui/tests/test_assistant_core_artifacts_tool.py`:
    - `test_create_typst_document_tool`: tests `create_typst_document` dispatch.
    - `test_create_resume_tool`: tests `create_resume` dispatch.
- [ ] **Step 2: Run test to confirm RED**
  - Run: `cd assistant-core && uv run pytest ../adapters/openwebui/tests/test_assistant_core_artifacts_tool.py -k typst`
- [ ] **Step 3: Implement `create_typst_document` and `create_resume` in `assistant_core_artifacts_tool.py`**
- [ ] **Step 4: Run tests to confirm GREEN**
  - Run: `cd assistant-core && uv run pytest ../adapters/openwebui/tests/test_assistant_core_artifacts_tool.py`
- [ ] **Step 5: Commit**
  - `git commit -m "feat(adapters): add create_typst_document and create_resume tools to Open WebUI artifact pack"`

---

### Task 6: Full Suite Verification & Housekeeping

- [ ] **Step 1: Run full pytest suite across backend and adapters**
  - Run: `cd assistant-core && uv run pytest && uv run pytest ../adapters/openwebui/tests`
- [ ] **Step 2: Run linter, formatter, and strict type check**
  - Run: `cd assistant-core && uv run ruff check . && uv run ruff format --check . && uv run mypy`
- [ ] **Step 3: Final verification summary and commit**
