# Open WebUI Personal Assistant Architecture

Date: 2026-08-09  
Status: Approved 2026-08-09  
Implementation status: Not started

## 1. Purpose

Build a highly capable personal assistant around an upstream, self-hosted Open WebUI deployment. The assistant must provide reliable cross-chat continuity, personal memory, document and media recall, dynamic tool use, durable artifacts, and safe autonomous maintenance without depending on stock Open WebUI Memory or maintaining a permanent Open WebUI source fork.

Open WebUI remains the user-facing product. Custom behavior is added through supported Functions, Tools, Events, OpenAPI/MCP connections, and one external modular companion service named `assistant-core`.

## 2. Primary outcomes

The system must:

1. Remember explicit facts, corrections, preferences, projects, interaction style, and useful inferred personalization across chats.
2. Recover bounded, source-linked passages from uncompacted conversation history when rolling compaction omits a needed detail.
3. Treat canonical personal documents, Git repositories, and saved artifacts as stronger evidence than inferred memory or old chat summaries.
4. Process identical durable content once, store it once in steady state, and reuse compatible derivatives across references.
5. Create, edit, preview, version, search, and download real DOCX, XLSX, PPTX, and PDF artifacts.
6. Understand audio, video, and YouTube through native multimodal provider APIs rather than transcription alone.
7. Make a broad tool catalog usable without placing every heavy schema into every prompt.
8. Preserve ordinary Open WebUI chat when the custom retrieval layer is temporarily unavailable.
9. Remain practical for one primary trusted user and at most one additional trusted user.

## 3. Non-goals

The initial design will not:

- Fork or patch Open WebUI source unless a later proven blocker has no supported extension path and the user separately approves it.
- Replace Open WebUI chats, Knowledge, Files UI, Notes, Tasks, Calendar, Automations, Skills, Prompts, web tools, model selector, Terminal UI, image integration, or audio UX.
- Treat native Open WebUI Memory as authoritative.
- Build hostile enterprise multi-tenancy, a formal release pipeline, a large evaluation platform, or many independently deployed microservices.
- Add SSO, music generation, Open WebUI Computer, custom browser automation, Valkey, a message broker, or local-GPU workloads without observed need.
- Guarantee physical pre-processing deduplication for ordinary paperclip uploads without changing Open WebUI's upload path.

## 4. Existing deployment constraints

- Open WebUI currently tracks `ghcr.io/open-webui/open-webui:main` through Dokploy on an Oracle ARM server.
- Open WebUI application state remains in its native SQLite database/volume.
- Supabase PostgreSQL/pgvector is used only as the configured vector backend today.
- Open WebUI connects through Supavisor at `supabase-pooler:5432`.
- Gemini Embedding 2 is exposed through LiteLLM with 1536 dimensions.
- `RAG_EMBEDDING_BATCH_SIZE=1` is required because the provider returned one embedding for multi-input batches. Async embedding and modest concurrency remain enabled.
- Hybrid retrieval and external reranking through `voyageai/rerank-2.5-lite` remain the provisional document-retrieval configuration.
- The Obsidian repository is canonical; GitHub MCP writes it and oikb synchronizes it into the `engineering-vault` Knowledge Base.
- Open Terminal, Gemini Image/Nano Banana 2, backups, and basic operations already exist.
- Garage S3 and OnlyOffice already exist elsewhere in the user's infrastructure and will be reused.
- Persistent Open WebUI ConfigVars may override Compose environment values. Runtime settings must be audited, not inferred from Compose alone.

## 5. Architectural principles

### 5.1 Separate memory types

Do not force all continuity through one store. Treat these as separate layers:

- Working memory: current prompt, recent turns, active task state, and native rolling compaction.
- Episodic memory: topic-bounded records and source-linked prior conversations.
- Semantic personal memory: confirmed and inferred durable personal context.
- Document memory: canonical binaries, extracted text, chunks, and embeddings.
- Knowledge memory: Obsidian and native Open WebUI Knowledge Bases.
- Procedural memory: native Open WebUI Skills and Prompts.
- Capability registry: native, OpenAPI, MCP, Terminal, and assistant-specific tools.

### 5.2 Canonical sources decide; indexes discover

Authority precedence is:

1. Current explicit user instruction.
2. Current verified canonical document, repository, or artifact.
3. Confirmed memory with provenance.
4. Current KB extraction/index of a canonical source.
5. Inferred memory with confidence and evidence.
6. Episodic summary used for discovery.
7. Old raw chat or unverified web information.

Current primary web sources decide current external facts. Explicit user corrections override previous memory. Conflicting authoritative sources must not be silently merged.

### 5.3 Upstream-first extension

Before adding custom behavior, verify current native Open WebUI support. Prefer, in order:

1. Native configuration or built-in tools.
2. A small reviewed Function/Tool/Event adapter.
3. A maintained community extension after code and security review.
4. An external companion through supported OpenAPI/MCP/HTTP interfaces.
5. A source fork only after separate approval.

### 5.4 Simplify deployment, not behavior

Custom modules share one deployable `assistant-core` initially. They keep clear interfaces and table ownership so a workload can be split later without forcing microservice overhead now.

## 6. System boundaries

### 6.1 Open WebUI

Open WebUI owns:

- Authentication, users, chats, forks, folders, native file records, Notes, Tasks, Calendar, Automations, Skills, Prompts, Knowledge configuration, web tools, model selection, built-in tools, Open Terminal integration, image generation, audio UX, ratings, and admin settings.
- Native rolling context compaction.
- Current-chat and Knowledge query/search/grep/view tools.
- Its own SQLite application database and uploaded-file metadata.

### 6.2 Thin Open WebUI adapter pack

The adapter pack contains:

- Context Filter: requests a bounded memory/episode/recall block from `assistant-core` and injects it before inference.
- Lifecycle Event Function: sends completed-turn events and every lifecycle event exposed by the deployed Open WebUI version asynchronously. Reconciliation and supported API observation cover deletion, fork, or attachment transitions that are not exposed as native events.
- Assistant Core Tool: exposes explicit memory, recall, capability, library, artifact, media, and status operations.
- Status/Action adapters: display concise status and artifact cards and provide user controls.

Adapters are mostly stateless. A retrieval failure fails open for ordinary chat; a write/action failure never reports success.

### 6.3 `assistant-core`

One API/deployment contains these modules:

- Identity and policy.
- Memory ledger.
- Conversation index and topic episodes.
- Context planner.
- Capability registry/gateway.
- Content catalog.
- Artifact coordinator.
- Media coordinator.
- Jobs, reconciliation, health, audit, and metrics.

Modules own their tables and communicate through explicit service methods and durable internal events rather than reaching into one another's tables.

### 6.4 Worker

One worker initially processes the Postgres-backed job/outbox queue. It handles memory extraction, episode updates, embeddings, content extraction, previews, artifact/media jobs, deletion, garbage collection, and reconciliation. Jobs are idempotent and keyed by stable event/content/processing identities.

Valkey or a broker is added only if measured concurrency, delivery latency, or multi-replica coordination requires it.

## 7. Persistence

### 7.1 PostgreSQL

Use dedicated assistant-core roles/schemas alongside, not inside, Open WebUI's application SQLite state. Logical records include:

- User/native identity mappings.
- Memory records, evidence, confidence, paths/topics, and supersession.
- Canonical transcript segments and independent chat/message/fork references.
- Topic episodes and source references.
- Capabilities, normalized schemas, embeddings, chat bindings, and invocation audit.
- Content objects, aliases/references, processing identities, and derivatives.
- Artifacts, immutable versions, editor sessions, and media analyses.
- Event inbox/outbox, jobs, tombstones, reconciliation state, and compact audit records.

Assistant vectors use separate tables/collections from Open WebUI's existing `openwebui_vector.document_chunk` data.

### 7.2 Garage

Use separate buckets and credentials:

- `openwebui-uploads`: native Open WebUI uploads and normal paperclip lifecycle.
- `assistant-content`: canonical durable documents, media, artifacts, immutable versions, and binary derivatives.

Open WebUI can read/write only `openwebui-uploads` and has no access to `assistant-content`. `assistant-core` has read access to `openwebui-uploads` for ingestion and read/write/delete access to `assistant-content`, which it owns. Garage object tagging remains disabled, and application-level Postgres version chains replace unavailable S3 bucket versioning.

### 7.3 Other canonical sources

- GitHub/Obsidian remains canonical for the vault; oikb is the synchronizer.
- Open WebUI remains canonical for full chat transcripts and native workspace objects.
- Garage plus assistant-core metadata is canonical for durable binary artifacts/media/documents.
- Embeddings, chunks, previews, and indexes are rebuildable derivatives.

## 8. Context and compaction

- Use a universal 300,000-token compaction threshold and 300,000-token cap for all models.
- Raise both only temporarily for an exceptional task.
- Use a dedicated non-thinking compaction model verified to accept the compacted input.
- Allow the assembler to use effectively the full configured threshold; the provider's remaining real window is operational headroom.
- Protect, in order: system/deployment policy, current request/direct inputs, recent coherence, current task/open loops, and latest compaction state.
- Fill the remainder elastically with relevant memory, episodes, documents, transcript passages, web evidence, and tool results.
- Distill or evict stale, redundant, low-authority, and low-relevance material first.
- Native compaction never deletes the stored transcript.

## 9. Memory

The external ledger is authoritative.

After every completed turn, a cheap task model extracts zero or more explicit or inferred candidates. Deterministic validation rejects unsupported, duplicate, transient, empty, or secret-like candidates.

- Explicit facts/preferences/corrections become confirmed records with message-level evidence.
- Inferred interaction style and personalization remain labeled as inferred with confidence and evidence.
- Current instructions always override stored preferences.
- Corrections supersede previous values rather than creating competing active facts.
- Wider deduplication, contradiction checks, confidence recalculation, and decay run periodically.
- Retrieval ranks by semantic relevance, topic/path, authority, recency, confidence, and project scope.

Native Open WebUI Memory may be an asynchronous convenience mirror only. If it becomes stale, lossy, or confusing, disable it. The system must remain fully functional without it.

## 10. Conversation continuity

- Index completed user/assistant turns as bounded canonical segments with chat/message/role/time/topic and neighbor references.
- Reuse exact-text embeddings where possible while retaining independent fork/chat references.
- Build incremental topic episodes containing decisions, durable context, artifacts, source message IDs, and open loops.
- Use native `search_chats`/`view_chat` first for literal or small recovery.
- Use hybrid exact plus semantic transcript retrieval for vague or bounded recovery.
- Search progressively: current chat, current folder/project, then all chats for that user.
- Return approximately 3–6 passages plus necessary neighbors with direct provenance.
- Never reopen an entire huge transcript to recover one detail.

## 11. Dynamic capabilities

Keep a lightweight native core attached: memory/recall, Files/Knowledge, web, Notes/tasks/time/calculation, and content/artifact access. Heavy Terminal and browser capabilities remain explicit native/manual controls.

For specialized tools:

1. `find_capabilities` filters and ranks normalized native/OpenAPI/MCP/custom capabilities by intent, authorization, availability, model compatibility, risk, and environment.
2. It returns a small shortlist with IDs, argument requirements, and risk/cost hints.
3. `run_capability` validates arguments against the stored schema and invokes the selected backend.
4. Successful use creates a gateway-side chat binding so later matching turns prioritize it.

The gateway does not need to tick native Open WebUI checkboxes. Status UI shows the active binding and offers unbind. Dynamic discovery never bypasses confirmation policy.

## 12. Durable content and deduplication

### 12.1 Explicit Add to Library

- Hash before expensive processing.
- Reuse an existing canonical object and compatible derivatives immediately.
- Otherwise write one immutable canonical object, then create extraction, preview, chunk, embedding, and classification jobs.
- Place the durable item in a searchable inbox with visible, reversible classification suggestions.

### 12.2 Normal paperclip upload

- Leave the native upload path unchanged.
- Observe attached native file IDs after the turn, calculate canonical identity, and link/promote in the background.
- Clean redundant native state only when chat/file references remain valid; otherwise allow temporary duplication until reconciliation.

Processing identity is content hash plus extractor/model/version/options, chunking version, and embedding model/dimension. A changed processing version creates only missing derivatives, not another canonical binary.

Durable documents, audio, and video are promoted automatically according to visible/reversible classification. Screenshots, tiny snippets, and temporary exports remain chat-local by default.

## 13. Artifacts

- Create editable DOCX, XLSX, PPTX, and PDF through typed operations and reviewed format libraries/workers.
- Use NEURA generators as candidate components and design references, not as the canonical lifecycle.
- Use Typst or LaTeX where it produces superior PDFs, especially resumes.
- Render and validate outputs before marking them complete.
- Store immutable versions and move the current pointer only after successful validation.
- Refresh extraction, preview, and search derivatives after each successful version.
- Present persistent native artifact cards with Edit, Ask AI to revise, Download, and Versions.
- Open OnlyOffice in a separate tab with short-lived signed configuration/download URLs.
- Validate callback origin, owner, version, size, redirects, and content; a failed callback leaves the prior version intact.

## 14. Audio, video, and YouTube

- Keep native STT/TTS for voice UX and simple transcription.
- Use Gemini native Files/YouTube/media interfaces for original audio/visual understanding.
- Normalize media identity, deduplicate, and retain transcript, timestamped observations, thumbnails where useful, model/version, and embeddings as derivatives.
- Retrieve bounded timestamped evidence later and re-query original media only when stored derivatives are insufficient.
- Whisper is an optional fallback, not the primary architecture.

## 15. Web, extraction, and existing integrations

- Use native web search/fetch aggressively for general or current information; use personal stores first for personal facts/projects.
- Prefer primary current web sources and include citations.
- Keep Gemini Embedding 2 and the current Voyage reranker until observed evidence justifies a change.
- Benchmark Xberg through its Docling-compatible/external-loader interface against representative real PDFs, scans, tables, Office files, extraction quality, and ARM resource use. Adopt it only if it wins; no source patch is needed.
- Keep stock NEURA Browser optional and uncoupled. Do not customize it initially.
- Use native Rich UI/artifacts before adopting Inline Visualizer.
- Community Prune may supplement manual cleanup after review/backups but never owns referential correctness.

## 16. Action policy

- Autonomous: reads, retrieval, analysis, web research, memory/episode maintenance, Notes/tasks, indexes, library classification, artifact creation/versioning, and assistant-owned reversible writes.
- Scoped reversible project writes may run automatically after the capability is explicitly enabled for that chat/project and remain visible/auditable.
- Explicit confirmation is required immediately before communication to others, purchases/bookings/payments, account or permission changes, public publishing/deployment, destructive operations, irreversible overwrite, bulk deletion, or consequential writes outside the selected scope.
- The capability gateway enforces risk independently of model instructions.

Retrieved web/doc/chat/tool content is untrusted data. It cannot change system policy, authorize actions, expose secrets, or supersede current user instructions.

## 17. Deletion and retention

- A native message/chat deletion tombstones assistant-core references immediately, excluding them from all retrieval.
- Remove parent-only transcript/episode references and inferred memory evidence.
- A surviving fork retains its independent references.
- Explicitly confirmed memories survive source-chat deletion by default, retaining tombstoned provenance metadata rather than retrievable source text; they remain until explicitly corrected/deleted or covered by a separately approved retention rule. Independently saved durable documents/artifacts follow their own retention rules.
- Canonical segments/objects become physically eligible only when no live reference remains.
- Physical orphan purge occurs after a 24-hour grace period and a final reference/native-state verification.
- Lifecycle handlers are idempotent; reconciliation repairs missed/out-of-order events and stale orphans.
- Explicit durable artifacts/documents and versions remain until user deletion or a separately approved retention policy.
- Avoid logging full prompts, contents, secrets, or signed URLs by default.

## 18. Failure behavior and observability

- Context retrieval outage: continue native chat with concise degraded-memory status.
- Fail-open behavior applies only to optional context augmentation. Authentication, authorization, confirmation, and action-policy checks fail closed.
- Postgres outage: pause authoritative writes and report failure; do not invent success from cache.
- Garage outage: metadata may remain readable, but binary operations report unavailable.
- Provider outage: use only explicitly compatible fallbacks; otherwise surface failure.
- Worker outage: accepted jobs remain queued and visible.
- Failed extraction/media/artifact work preserves the canonical source and prior valid version.
- Correlation IDs connect Open WebUI adapters, `assistant-core`, workers, providers, and UI events.
- Use native OpenTelemetry/structured/audit logs plus assistant metrics for memory, recall, capability routing, jobs, deduplication, deletion lag, reconciliation, compaction, and artifact/media processing.
- Keep final responses clean; detailed diagnostics live in status cards and telemetry.

## 19. Backup and recovery

Back up:

- Open WebUI persistent volume/SQLite and effective configuration.
- Assistant-core PostgreSQL schemas.
- Both Garage buckets plus Garage configuration/metadata.
- Git/Obsidian through existing remote history.
- Dokploy deployment configuration and secure secret references.

Restore canonical state before derivatives. Restore order is infrastructure/secrets, Open WebUI native state, assistant-core Postgres, Garage objects, workers/reconciliation, then rebuilt indexes/previews/embeddings. Validate references before enabling garbage collection. Perform occasional small restore drills.

Prefer a pinned release tag or immutable image digest for the production instance. Preserve the previous digest/config and snapshot state before meaningful upgrades.

## 20. Incremental delivery

### Phase 0 — Native baseline

Audit the running image and persistent ConfigVars; verify native tools, 300k compaction, telemetry, Garage uploads, web, RAG, Terminal, and existing provider integrations.

### Phase 1 — Foundation

Deploy `assistant-core`, one worker, schemas/jobs/auth/health/metrics, and thin adapters with empty context behavior and fail-open reads.

### Phase 2 — External memory

Build confirmed/inferred provenance-aware memory, automatic per-turn extraction, correction, retrieval, and personalization. Native Memory remains disabled or optional.

### Phase 3 — Conversation continuity

Add transcript segments, topic episodes, hybrid bounded recall, source links, deletion/fork handling, 24-hour GC, and reconciliation.

### Phase 4 — Capability gateway

Normalize capabilities, provide semantic discovery/validated execution, risk filtering, audit, and chat-scoped bindings.

### Phase 5 — Content library

Add Garage canonical storage, explicit pre-hash library ingestion, paperclip reconciliation, derivative reuse, reference counting, and an evidence-based Xberg benchmark.

### Phase 6 — Artifacts

Add typed Office/PDF generation, templates, immutable versions, previews, AI revision, persistent cards, and hardened OnlyOffice editing.

### Phase 7 — Media

Add durable Gemini-native audio/video/YouTube processing, timestamp retrieval, and deduplication.

### Phase 8 — Optional polish

Consider Inline Visualizer, Prune, Open WebUI Computer, NEURA customization, music generation, GTX 1050 Ti workloads, SSO, Valkey, or service splitting only from observed need.

Each phase has a small maintenance check. Stop when the system is already sufficiently useful; later phases are not mandatory merely because they appear here.

## 21. Phase completion checks

- Phase 0: native chat, attachments/RAG, web, Terminal, images, compaction, history search, and Garage survive restart with effective settings documented.
- Phase 1: one turn propagates stable IDs and one idempotent event; native chat survives deliberate assistant-core outage.
- Phase 2: a preference crosses chats, a correction supersedes it, inferred style is evidenced, and provenance deletion behaves correctly.
- Phase 3: a compacted detail is recovered from bounded passages; deleting a parent hides parent-only recall while a fork survives.
- Phase 4: a specialized tool is discovered, executed, reused in-chat, access-filtered, and confirmation-gated when consequential.
- Phase 5: duplicate library/paperclip uploads converge to one canonical object/derivative set with independent references and correct final cleanup.
- Phase 6: a document and workbook survive AI and OnlyOffice revisions with prior versions downloadable and failed edits non-destructive.
- Phase 7: a later chat retrieves timestamped speech plus visual/non-speech evidence from deduplicated media.

These are lightweight post-change checks, not formal release gates or repeated model-scoring infrastructure.

## 22. Key references

- Open WebUI features: https://docs.openwebui.com/features/
- Extensibility: https://docs.openwebui.com/features/extensibility/
- Functions and events: https://docs.openwebui.com/features/extensibility/plugin/functions/
- Native MCP: https://docs.openwebui.com/features/extensibility/mcp/
- Knowledge: https://docs.openwebui.com/features/workspace/knowledge/
- Skills: https://docs.openwebui.com/features/workspace/skills/
- Memory: https://docs.openwebui.com/features/chat-conversations/memory/
- Context compaction: https://docs.openwebui.com/troubleshooting/context-window/
- Open Terminal: https://docs.openwebui.com/features/open-terminal/
- OpenTelemetry: https://docs.openwebui.com/reference/monitoring/otel/
- Storage configuration: https://docs.openwebui.com/reference/env-configuration/
- oikb: https://docs.openwebui.com/ecosystem/knowledge-base-sync/
- Xberg: https://github.com/xberg-io/xberg
- NEURA Office: https://github.com/ianustec/neura-office
- NEURA Browser: https://github.com/ianustec/neura-for-browser
- Classic298 plugins: https://github.com/Classic298/open-webui-plugins

The detailed discovery record and research rationale remain in `OPENWEBUI_ARCHITECTURE_DISCOVERY_CHECKPOINT.md`.
