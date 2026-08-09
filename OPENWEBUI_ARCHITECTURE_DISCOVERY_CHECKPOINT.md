# Open WebUI Personal Assistant Architecture — Discovery Checkpoint

Checkpoint date: 2026-08-09
Status: Requirements elicitation and architecture discovery are in progress. Do not implement yet.
Workspace: C:\Users\amanp\Documents\ChatGPT\openwebui config

This is the continuation source of truth for the current design conversation. Read it before continuing after context compaction.

## 1. Goal and working method

The user is building a heavily customized self-hosted Open WebUI personal assistant. The goal is not merely to add a memory plugin. The target is a practical self-hosted context, memory, tool, document, artifact, and action architecture that approaches or exceeds the continuity and usefulness of ChatGPT, Gemini, Claude-style skills, and Odysseus.

Open WebUI remains the primary frontend, but external components may be added where stock Open WebUI is insufficient.

Working rules:

- External-first.
- Avoid modifying Open WebUI source where Functions, Tools, Actions, Filters, Rich UI, OpenAPI/MCP, Open Terminal, or external services suffice.
- A small maintained patch/fork is acceptable only as a last resort.
- Before building, check current Open WebUI native support, community extensions, and credible alternatives.
- Build and evaluate one layer at a time.
- The Superpowers brainstorming skill was invoked only to encourage more targeted questions. Do not follow its entire gated spec/commit/visual-companion ceremony.

Current sequence:

1. Continue targeted requirements questions.
2. Build a master capability matrix.
3. Audit each capability against Open WebUI and alternatives.
4. Define loading modes, authority, permissions, and routing.
5. Propose architecture options and a phased recommendation.
6. Write detailed requirements and architecture specifications.
7. Implement and evaluate approved layers incrementally.

## 2. Product boundaries already decided

### Users

- Optimize v1 for one trusted primary user.
- At most one additional trusted user may use it.
- Preserve clean identity boundaries for possible future expansion.
- Hostile multi-tenant isolation is not a v1 requirement.

### Privacy

- Configured external AI providers may process all content.
- Persistent stores remain self-hosted where practical.
- No per-document local-only processing tier is required for v1.
- API-first is preferred over building around local GPU infrastructure.

### Action/autonomy policy

- Reads, retrieval, analysis, and web research are automatic.
- The assistant may write its own memory, notes, KBs, artifact metadata, and routine synchronization without confirmation.
- Routine reversible knowledge maintenance may run automatically and must be auditable/versioned.
- Confirmation is required for communication to other people, purchases, permission/account changes, destructive operations, and consequential writes outside assistant-owned stores.
- Prefer reversible changes and retain history.

## 3. Existing deployment

### Open WebUI

- Image: ghcr.io/open-webui/open-webui:main
- Deployed through Dokploy on an Oracle ARM server.
- Shared network: open-webui-integrations
- Open WebUI also has its private Dokploy/application network.
- Application state remains in Open WebUI's normal SQLite database/volume.
- The user deliberately did not migrate the complete application database to PostgreSQL.

### Supabase PostgreSQL / pgvector

- Self-hosted Supabase PostgreSQL.
- Dedicated role: openwebui_vector
- Dedicated schema: openwebui_vector
- Search path: openwebui_vector, extensions, public
- vector extension is in extensions.
- Open WebUI created openwebui_vector.document_chunk automatically.
- Vector dimension: 1536
- HNSW: m=16, ef_construction=128
- Hybrid retrieval enabled.

### Supavisor

- Open WebUI connects through Supabase native Supavisor.
- Supavisor joins supa-supabase-hzugof and open-webui-integrations.
- Network alias: supabase-pooler
- Internal connection: supabase-pooler:5432
- Tenant ID: de1ccf40-dd51-4fbf-aa87-365aadab9913
- Vector database URL uses the Supavisor tenant-user format.

### Embeddings

- Gemini Embedding 2 through LiteLLM.
- LiteLLM URL: https://litellm.app.amhl.ovh/v1
- OpenAI-compatible embedding interface.
- Dimension: 1536.
- Important incompatibility: sending a list of multiple inputs returned only one embedding.
- Error observed: embeddings generated 1 for 7 items; IndexError: list index out of range.
- Confirmed fix: RAG_EMBEDDING_BATCH_SIZE=1.
- Keep async embedding enabled and use concurrency instead of batching.
- Current concept: batch size 1, async enabled, concurrency 2.

### Retrieval and reranking

Pipeline:

    query
      -> Gemini 1536-dimensional embedding
      -> pgvector HNSW semantic retrieval plus PostgreSQL/BM25 lexical retrieval
      -> hybrid candidates
      -> external reranker
      -> selected context

Current intent:

- Hybrid search enabled.
- Enriched hybrid search enabled.
- BM25 weight 0.4.
- RAG_TOP_K initially 20-30.
- RAG_TOP_K_RERANKER initially 5-8.
- RAG_RELEVANCE_THRESHOLD initially 0.
- Tune through evaluation later.

Reranker:

- OpenRouter endpoint: https://openrouter.ai/api/v1/rerank
- Model: voyageai/rerank-2.5-lite
- Remains the provisional cost/quality choice for mostly textual technical knowledge.
- Do not replace merely because another checklist names Qwen/Cohere/NVIDIA.

### Persistent configuration gotcha

- Many RAG settings are persistent ConfigVars.
- Admin Settings -> Documents can override Compose environment variables.
- This previously caused Open WebUI to continue loading all-MiniLM-L6-v2.
- Documents UI was corrected.
- Always verify runtime ConfigVars and environment variables; Compose alone is not authoritative.

Important Documents settings:

- Bypass Embedding and Retrieval: OFF
- Full Context Mode: OFF
- Hybrid Search: ON
- Normal path: chunk -> embed -> hybrid retrieve -> rerank.
- Do not globally enable Full Context Mode.

### Other existing components

- Open Terminal is already integrated.
- Gemini Image / Nano Banana 2 is already used for image generation/editing.
- Current cheap task model is a Gemini 3.5 Lite configuration/alias.
- Backups and basic operations are already implemented, but restore tests, health, upgrade/rollback, and observability still belong in the audit.
- A GTX 1050 Ti is available but should not drive the architecture. It may later help with lightweight Whisper, embeddings/reranking, or experimental OCR. API-first remains preferred.

## 4. Existing files, knowledge, and vault behavior

### Desired current-chat document behavior

First substantive question after upload:

- Run normal RAG retrieval.
- Also read the complete extracted document with view_file, paginated as needed.

Later questions:

- query_chat_files first.
- Escalate to grep_chat_files and view_file for exact wording, broader context, or the full source.

Knowledge equivalents:

- query_knowledge_files
- search_knowledge_files
- grep_knowledge_files
- view_knowledge_file

Do not use global Full Context Mode.

### Relevant current built-ins

Files:

- list_chat_files
- query_chat_files
- grep_chat_files
- view_file

Knowledge:

- list_knowledge
- list_knowledge_bases
- search_knowledge_bases
- query_knowledge_bases
- search_knowledge_files
- query_knowledge_files
- grep_knowledge_files
- view_knowledge_file
- optional kb_exec

Chats:

- search_chats
- view_chat

Other built-ins discovered:

- Hierarchical Memory tools and background review
- Notes tools
- Time and calculation
- Web search and URL fetching
- Image generation/editing
- Code Interpreter
- Task management
- Automations
- Calendar
- Channels
- Foreground/background sub-agents

### Obsidian vault

- Repository: amangupta20/obs-vault
- Canonical durable engineering/personal technical knowledge store.
- Open WebUI KB: engineering-vault
- KB UUID: 7b11f1e8-eaba-4448-8dec-27b46baeae55

### oikb

- Image: ghcr.io/open-webui/oikb:latest
- Sync: GitHub obs-vault -> Open WebUI engineering-vault
- Interval: 5 minutes
- Include: *.md and **/*.md
- Exclude: .obsidian/**, AGENTS.md, skills/**, 00_Inbox/Processed/**
- Max file size: 10 MB
- Initial concurrency: 1

Known workarounds:

- validate --deep fails because current Open WebUI can return files: null and oikb calls len() on it.
- Use oikb validate, not validate --deep.
- History DB: /data/history.db
- Image runs UID/GID 1000:1000.
- Keep an idempotent oikb-init service that chowns /data to 1000:1000 and chmods 0750.

oikb OpenAPI Tool Server:

- Internal URL: http://oikb:8080
- Protected by OIKB_API_KEY.
- Exposes sync status, history, and trigger.
- Intended loop: write GitHub -> commit -> trigger oikb -> KB refresh.
- oikb does not edit GitHub itself.

### Authority split for the vault

- KB: semantic discovery and citations.
- GitHub MCP: authoritative current repository state and writes.
- oikb: synchronization.

Vault Assistant / KB Manager:

- Attached Knowledge: engineering/Obsidian vault.
- Tools: GitHub MCP and oikb.
- Skill: Obsidian_Vault_Operations.
- Intended write loop: search KB -> verify GitHub -> follow AGENTS and skill -> minimal coherent change -> commit main -> trigger oikb -> report files/commit/sync.
- Memory is disabled for this model because the vault is canonical.

## 5. Memory architecture decisions

Memory is not one system. Separate:

- Working memory: active context window.
- Episodic memory: meaningful segments of earlier conversations.
- Semantic personal memory: durable facts, preferences, inferred personalization, and context.
- Document memory: canonical files, versions, extraction, and embeddings.
- Knowledge memory: Obsidian and structured KBs.
- Procedural memory: skills, policies, and workflows.
- Capability registry: tools and integrations.

### Confirmed memory

Includes:

- Explicit facts/preferences.
- Confirmed decisions/commitments.
- Verified project state.
- Completed outcomes.

### Inferred memory and personalization

Includes:

- Communication style, directness, detail, formatting, and question preferences.
- Expertise by domain and what may be assumed.
- Workflow habits and autonomy/review preferences.
- Interests, priorities, recurring goals, and likely intent.
- Domain-specific preferences for coding, research, career work, writing, etc.
- Causal or relationship hypotheses at lower confidence.

Requirements:

- Confidence.
- Evidence/source links.
- Timestamps.
- Decay/expiry.
- Promotion with repeated evidence.
- Demotion/retirement on contradiction.
- Never override explicit instructions or canonical sources.
- Use silently for low-risk personalization.
- Disclose/confirm when consequential behavior depends on uncertain inference.

### Personalization overlays

Use:

    global user profile
      -> domain profile
      -> assistant/model override

Store only differences in narrower layers. It can later collapse into one universal profile without migrating underlying memories.

### Episodic memory

- Automatically index meaningful conversation segments, not entire chats.
- A single chat may generate several episode records.
- Each record includes topic, date, exact chat/message references, decisions, durable context, open loops, artifacts, and status.
- Exclude repetition, debugging noise, abandoned ideas, and incorrect intermediate hypotheses.
- Retrieval finds an episode first, then opens the referenced original messages.

Example:

    Episode: Gemini embedding batch failure
    Messages: 71-89
    Finding: one embedding returned for several inputs
    Resolution: batch size 1, async concurrency 2
    Status: resolved
    Source chat: <chat-id>

## 6. Context compaction

User chose transparent automatic compaction for long active chats.

Current Open WebUI v0.11 native behavior:

- Optional and off by default.
- Configurable compaction model, threshold, cap, and prompt.
- Summarizes roughly oldest 60%; keeps newest 40% verbatim.
- Stores a rolling checkpoint while retaining/displaying full chat.
- Preserves tool-call/result boundaries.
- Reassembles system prompts, tools, skills, RAG, web, and memory fresh.
- /compact triggers manually.
- /status shows context usage.
- API exposes usage and manual compaction.

Known issues/limits:

- Mostly invisible to user.
- Older tool outputs get condensed.
- May decline to compact one enormous exchange.
- Confirmed issue #27604: reasoning tokens can consume the approximately 1000-token summary allowance and yield an incomplete summary.

Recommended architecture:

    Native Context Compaction
      -> active-chat continuity

    Small safety Filter if needed
      -> trim obsolete large tool results/attachments
      -> enforce final per-model ceiling

    External episodic index
      -> meaningful source-linked segments
      -> exact recovery via chat/message IDs

Operational recommendations:

- Test configured Gemini Lite model with thinking disabled for compaction.
- Prefer a dedicated non-thinking compaction model.
- Use per-model thresholds based on usable context, not blindly the global 80k default.
- Use community hard-trimming filters only as safety nets.
- PrismAI is a reference, not an immediate wholesale replacement.

Compaction prompt must preserve:

- Current objective and active tasks.
- Confirmed decisions and constraints.
- User corrections/preferences.
- File, artifact, chat, and source IDs.
- Tool actions and verified outcomes.
- Unresolved questions and next steps.
- Important failed approaches.
- Facts versus hypotheses.

Current unanswered question:

Should major decisions and completed milestones immediately create an episodic checkpoint rather than waiting for compaction threshold? Recommendation: yes.

## 7. Durable documents/media and deduplication

### KB organization

Candidate durable KBs:

- engineering-vault
- career-documents
- academic-documents
- personal-reference

Auto-promoted items initially enter a durable searchable inbox such as personal-inbox/document-inbox.

- Store/index immediately.
- Save classification suggestions as metadata.
- Do not auto-move into domain KBs.
- Organize later through an explicit inbox-processing workflow.

### Selective paperclip promotion

User chose selective automatic promotion:

- Durable documents, audio, and video become durable.
- Screenshots, temporary exports, tiny snippets, and disposable files stay chat-local.
- Classification must be visible and reversible.

### Deduplication

Requirement:

Identical content is processed once, stored once, embedded once, and referenced many times.

Data separation:

Canonical content:

- SHA-256/content identity.
- Original binary.
- Extracted text/transcript.
- Chunks and embeddings.
- Media timestamps/thumbnails.
- Processing versions.

References:

- Filename/alias.
- Source URL.
- Chat attachment.
- User/owner.
- KB membership.
- First/last seen.

Processing cache key:

    content hash
    + extractor/model
    + extractor version
    + processing options
    + chunking version
    + embedding model/dimension

Open WebUI findings:

- Normal upload computes file_hash but still creates a new record and begins processing.
- Same-chat dedup only avoids reattaching something already in that chat.
- Real hash-diff reuse already exists in directory sync and oikb.

Agreed combination:

1. Add to Library: dedicated flow hashes before processing, immediately reuses canonical content, indexes durably, and places it in the inbox.
2. Normal paperclip: unchanged UI; an event-driven worker later detects canonical matches, reuses cached derivatives, links the chat, and safely cleans redundant records.
3. Periodic reconciliation catches duplicate binaries, orphaned vectors, failures, and incomplete indexing.

Limitation:

A Tool/Function cannot stop the normal paperclip upload before Open WebUI may begin processing. After-the-fact cleanup may not save the first duplicate processing cost. Fully transparent pre-processing dedup through the paperclip would need a small upstream/backend change or an undesirable reverse-proxy interception.

### Version rules

- Exact byte duplicate: reuse canonical item.
- Agent edits existing canonical artifact: new version in the same chain.
- User independently uploads changed bytes: separate document.
- Group/replace independently uploaded variants only when explicitly requested.
- Never automatically delete an older similar artifact.

## 8. Audio, video, and YouTube

User chose automatic durable storage/indexing for processed media.

Gemini natively supports:

- Audio beyond transcription, including non-speech sounds.
- Video through audio and visual streams.
- Timestamped questions.
- Files API uploads.
- Public YouTube URLs.

Open WebUI limitation:

Normal audio uploads are usually transcribed and ordinary attachments go through extraction/RAG. Original audio/video may not reach Gemini natively through the OpenAI-compatible path.

Recommended external media tool:

    audio/video/YouTube URL
      -> canonical ingestion and dedup
      -> Gemini native Files/YouTube media API
      -> transcript + audio/visual understanding + timestamps
      -> durable media record and embeddings
      -> reusable in later chats

- Whisper is an optional cheap transcription fallback.
- YouTube identity: normalized video ID plus periodic source metadata/revision check.
- Web pages: normalized URL plus content hash/fetch time.
- Exact-byte dedup first; near-duplicate detection later.

## 9. Web behavior

User wants aggressive web use because local KBs are mostly personal.

Policy:

- General/current informational question: browse proactively.
- Personal facts/projects/preferences/documents: local memory/KB/files/chats first.
- Mixed question: combine local personal context and current web information.
- Prefer primary sources and cite.
- Canonical local documents remain authoritative about the user's history.

Use two research tiers:

- Ordinary informational question: quick multi-source research.
- Consequential, disputed, technical, novel, or explicitly research-heavy: deeper multi-query research and source comparison.

Candidates to evaluate:

- Brave LLM Context.
- Classic Brave plus fetch/load.
- Tavily.
- Exa.
- Perplexity.
- Firecrawl.
- SearXNG.
- Open WebUI native web/fetch.

Compare citations, extraction quality, cost, rate limits, latency, and prompt-injection handling.

## 10. Model roles and provider compatibility

Model-role routing initially means explicit task configuration, not an elaborate router.

- Main response: selected capable model.
- Titles/tags/query rewrites/summaries/memory extraction: Gemini Lite task model.
- Audio/video/YouTube: Gemini media-capable model through native media API.
- Image generation/editing: existing Gemini Image.
- Embedding: Gemini Embedding 2.
- Rerank: Voyage Lite.
- Artifacts: format-specific tool/model workflows.

Provider compatibility is a repeatable smoke-test matrix for:

    Open WebUI -> LiteLLM/OpenRouter/direct API -> model

Test:

- Streaming.
- Tool calling/multiple calls.
- Images/files/audio/video.
- Reasoning preservation through tools.
- Long context/compaction.
- Citations.
- Structured outputs.
- Errors, retries, rate limits, cancellation.

It is a test checklist, not another service.

## 11. Document and artifact production

Target: full office-copilot experience, not one-shot file generation.

Requirements:

- Create, edit, render, inspect, revise, and export polished DOCX/XLSX/PPTX/PDF.
- Reusable templates and style systems.
- LaTeX or Typst where it produces better PDFs, especially resumes.
- Human office editing.
- Agent structural editing of real binaries, not only extracted Markdown.
- Versioning, previews, downloads, and search/index refresh.

### Agreed no-fork UX

- Persistent artifact card in chat.
- Shows preview/summary, filename, type, version, modified time.
- Actions: Edit, Ask AI to revise, Download, Versions.
- Full OnlyOffice editor opens in a separate tab.
- Save updates canonical artifact, retains prior version, refreshes extraction and RAG.

Feasible through current Open WebUI:

- Rich UI HTMLResponse embeds.
- Persistent iframe cards.
- Action Functions.
- Event emitters/external tool events.
- Prompt submission from embeds.

Use a thin Open WebUI adapter and external artifact service.

Artifact service responsibilities:

- Binary storage.
- Metadata and versioning.
- OnlyOffice launch/save callback.
- Structural DOCX/XLSX/PPTX editing.
- LaTeX/Typst PDF workflows.
- Preview rendering and visual verification.
- Text extraction and KB sync.
- Audit history.

### Community references

- ianustec/openwebui-generate-documents: native editable DOCX, Markdown/JSON, templates, letterhead, Open WebUI Files API.
- ianustec/openwebui-generate-spreadsheets: native XLSX, sheets, formulas, charts, validation, formatting.
- NEURA Office groups DOCX/PPTX/XLSX tools but is young.
- NEURA Browser adds page-context chat/browser actions but is early and needs license/security/auth review.
- Classic298 Inline Visualizer is useful Rich UI presentation, not a durable artifact engine.
- Also evaluate MCP file generators, ForLegalAI Office MCP, Open Terminal, Pandoc/Quarto, LaTeX/Typst, OnlyOffice, and Collabora.

Evaluate actual edit fidelity, round-trip preservation, templates, render verification, ARM support, security, maintenance, and license.

## 12. Odysseus OnlyOffice audit

Repository: amangupta20/odysseus-custom
Branch: dev
Inspected commit: 45fffad55e63813fa6abf69280dd7d0ce90a147a

Confirmed:

- Optional Dokploy deployment, not ordinary root Compose.
- Uses ONLYOFFICE_URL, ODYSSEUS_API_BASE, shared JWT secret.
- Pins onlyoffice/documentserver:9.4.0.1; loopback 8083 by default.
- Owner-scoped date-partitioned binary store with uploads.json and SHA-256 dedup.
- Text versions in Document/DocumentVersion tables.
- Imports DOC/DOCX/ODT/RTF/PPT/PPTX/ODP/XLS/XLSX/ODS.
- Saves binary, extracts Markdown through MarkItDown, links via hidden marker.
- UI loads OnlyOffice api.js and mounts DocsAPI.DocEditor.
- Chat attachments may be imported/reopened.
- Separate short-lived HS256 JWTs for editor config, download, callback.
- Callback fetches only configured OnlyOffice origin, refuses redirects, caps 50 MB, temp-writes, re-extracts, validates version/owner, atomically replaces, increments version, restores on DB failure.

Reusable protocol:

    artifact metadata + binary
      -> signed editor config
      -> protected download
      -> OnlyOffice
      -> verified callback
      -> atomic replacement
      -> extracted text refresh
      -> version

Avoid these gaps:

- Human editing only; agents edit textual current_content, not binary structure.
- Agent can remove hidden source marker.
- No blank Office creation or structural AI edit lifecycle.
- Callback token around 10 minutes.
- Likely stale token/version key after first save.
- Old Office binaries not retained.
- Inconsistent spreadsheet routing.
- EPUB UI/backend mismatch.
- Bulk export emits text instead of original binary.
- No OnlyOffice health check/persistent Document Server volume in supplied Compose.
- Query tokens may enter proxy logs.

Relevant repo paths:

- dockploy-compose.yml
- .env.example
- src/upload_handler.py
- core/database.py
- routes/document/document_routes.py
- src/office_onlyoffice.py
- static/js/document.js
- static/js/chat.js
- src/agent_tools/document_tools.py
- core/middleware.py

## 13. Production-readiness reference mapping

Use as candidate requirements, not mandatory technology.

Keep/evaluate:

- OCR: Xberg/Kreuzberg, Docling, vision-OCR.
- Media: Gemini native understanding; Whisper fallback.
- Image: existing Gemini Image.
- Task model: existing Gemini Lite.
- Thinking preservation: compatibility test.
- Web search/fetch: provider evaluation.
- Open Terminal: already installed; enable/test built-in multi-user if desired.
- MCP: registry, permissions, health, schema/context budget, routing.
- Prompts: small shared policy plus skills and role overlays.
- Sharing: default no public links; verify RBAC.
- Concurrency: load-test actual deployment.

Do not replace without evidence:

- Gemini Embedding 2/Voyage Lite.
- pgvector.
- SQLite Open WebUI application state.
- Add Valkey only if worker/replica/session needs justify it.
- Built-in Time/Calculation may already cover calculator.
- Treat vision filter as capability-aware routing.
- SSO is low priority for one/two trusted users.

## 14. Extraction-engine comparison

Xberg/Kreuzberg is a candidate engine, not a new architecture layer.

Open WebUI already supports Docling, Tika, Azure, Mistral OCR, Datalab Marker, MinerU, PaddleOCR, and custom loaders.

Kreuzberg/Xberg claims broad formats, Rust/CPU performance, OCR/VLM options, REST/CLI/library/MCP, ARM64, rich metadata, and heading/element-aware extraction. Elastic License 2.0 restrictions require review.

Compare:

- Native/scanned PDF quality.
- Tables/reading order.
- Images/captions.
- OCR languages/accuracy.
- ARM resource use and speed.
- Metadata/page/bounding-box preservation.
- Open WebUI integration.
- Security, maintenance, license.

## 15. Preliminary authority rules

Not fully reviewed yet.

Proposed precedence:

1. Current explicit user instruction.
2. Verified canonical source: Git repo, current artifact binary, authoritative personal document.
3. Confirmed memory/decision with provenance.
4. Current KB extraction/index of canonical source.
5. Inferred memory with confidence/evidence.
6. Episodic summary for discovery.
7. Raw old chat or unverified web information.

Rules:

- Indexes discover; canonical sources decide.
- Track current versus stale explicitly.
- Preserve citations/source IDs through summaries.
- Do not silently merge conflicts.
- Current primary web sources win for external facts.
- Explicit user correction/canonical personal documents win for personal facts.

This needs a dedicated question/review.

## 16. Remaining capability inventory

Master matrix must cover:

- Current-turn context assembly.
- Context budgeting/compaction.
- Episodic indexing/recovery.
- Confirmed semantic memory.
- Inferred personalization/confidence/decay.
- Files and document lifecycle.
- Media ingestion/understanding.
- KBs/Obsidian.
- Web/current information.
- Skills/procedures.
- Tool registry/dynamic selection.
- Model/task assignments and compatibility.
- Artifact generation/editing.
- Browser/computer use.
- Actions/approvals/audit.
- Synchronization/conflicts.
- Identity/sharing/permissions.
- Operations/health/upgrades/rollback.
- Evaluation/regression.
- Cost/latency/context management.
- Cross-model continuity.

Open questions:

1. Immediate episodic checkpoint after major decisions/milestones?
2. Final authority/conflict UX.
3. Memory backend: improved native Open WebUI Memory, external Mem0/Letta/Graphiti/mnemory, vault-backed, or hybrid.
4. Episode generation/update/delete and exact message links.
5. Need for unified context router versus thin independent components.
6. Tool selection given attached schemas rather than Odysseus-style semantic tool retrieval.
7. Always-loaded core tools and schema budget.
8. Artifact service data model/API.
9. Media KB transcript/timestamp format and YouTube refresh policy.
10. Selective promotion classifier and correction UX.
11. Dedup worker integration/safe cleanup.
12. Web provider and prompt-injection defenses.
13. Evaluation scenarios and acceptance criteria.
14. Audit actual running main version, persisted ConfigVars, and deployment settings.

## 17. Immediate continuation

Resume with:

Should major decisions and completed milestones immediately create an episodic checkpoint, rather than waiting for the context window to approach compaction threshold?

Recommended: yes, with two independent triggers:

- Context threshold -> native Open WebUI rolling compaction.
- Semantic milestone -> external source-linked episode after decisions, completed work, major corrections, and new open loops.

Then ask/review in roughly this order:

1. Source authority/conflict rules.
2. Personal memory backend and review/edit UX.
3. Episodic generation/retrieval.
4. Tool registry/loading/routing.
5. Artifact/media service boundaries.
6. Context assembly order and budgets.
7. Evaluation scenarios and phased roadmap.

## 18. Key sources

Open WebUI:

- Context compaction: https://docs.openwebui.com/troubleshooting/context-window/
- v0.11 commands: https://openwebui.com/blog/v0-11-0-the-interface-reorganized
- Compaction bug: https://github.com/open-webui/open-webui/issues/27604
- Built-in tools: https://docs.openwebui.com/features/extensibility/plugin/tools/
- Rich UI: https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/
- Actions: https://docs.openwebui.com/features/extensibility/plugin/functions/action/
- Events: https://docs.openwebui.com/features/extensibility/plugin/development/events/
- Knowledge: https://docs.openwebui.com/features/workspace/knowledge/
- Memory: https://docs.openwebui.com/features/chat-conversations/memory/
- Sub-agents: https://docs.openwebui.com/features/chat-conversations/chat-features/subagents/
- Open Terminal multi-user: https://docs.openwebui.com/features/open-terminal/advanced/multi-user/
- File upload implementation: https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/files.py
- oikb: https://docs.openwebui.com/ecosystem/knowledge-base-sync/
- Reasoning: https://docs.openwebui.com/features/chat-conversations/chat-features/reasoning-models/
- Permissions: https://docs.openwebui.com/features/authentication-access/rbac/permissions/
- Environment: https://docs.openwebui.com/reference/env-configuration/

Gemini:

- Video: https://ai.google.dev/gemini-api/docs/video-understanding
- Audio: https://ai.google.dev/gemini-api/docs/audio
- Files API: https://ai.google.dev/api/files

External/community:

- NEURA DOCX: https://github.com/ianustec/openwebui-generate-documents
- NEURA XLSX: https://github.com/ianustec/openwebui-generate-spreadsheets
- NEURA Browser: https://github.com/ianustec/neura-for-browser
- Inline Visualizer: https://github.com/Classic298/open-webui-plugins
- PrismAI: https://github.com/rajarshighoshal/PrismAI
- Mem0: https://github.com/mem0ai/mem0
- Letta: https://github.com/letta-ai/letta
- Graphiti: https://github.com/getzep/graphiti
- ToolHive: https://github.com/stacklok/toolhive
- Kreuzberg/Xberg: https://github.com/kreuzberg-dev/kreuzberg
- Odysseus custom: https://github.com/amangupta20/odysseus-custom/tree/dev

## 19. Continuation update — conversation recall, memory lifecycle, tools, and context budgets

Update date: 2026-08-09

This section is authoritative for decisions made after the original checkpoint. Where it conflicts with an earlier unanswered question or provisional recommendation, this section wins.

### 19.1 Working method remains lightweight

- Continue targeted requirements questions one at a time.
- The brainstorming skill is being used only to encourage good questions and architecture comparison.
- Do not follow the complete Superpowers spec/commit ceremony unless the user later asks for it.
- Do not implement yet. Continue capability discovery, native/community/alternative audits, and architecture decisions.

### 19.2 Native compaction observability and missing `/status`

The user has enabled native Context Compaction and can run `/compact`, but does not see `/status`.

Current Open WebUI v0.11 documentation says:

- `/compact` manually triggers compaction.
- `/status` opens a chat status panel showing context usage, queued messages, running tasks, and the chat ID.
- Built-in slash commands appear in a saved chat with messages and while no response is generating.
- `GET /api/v1/chats/{id}` and `POST /api/v1/chats/{id}/compact` expose `context_usage` even if the UI command is unavailable.

Because `/compact` is visible but `/status` is not, likely causes to audit are:

- The running `ghcr.io/open-webui/open-webui:main` container uses an older image digest; a running `:main` container does not update itself.
- Stale frontend/PWA assets do not match the backend.
- The command is being tested in a temporary/unsaved or actively generating chat.
- A current-main frontend regression.

First audit steps later:

1. Type `/status` exactly on its own in an existing idle chat, even if not suggested.
2. Record the actual displayed Open WebUI version and running image digest/creation time.
3. Hard-refresh/reset PWA assets if the backend is current.
4. Confirm `context_usage` through the chat API.

Observability preference:

- Conversation recall should use choice A: show a native tool/status card such as `Searching earlier conversation...` and expandable sources while working.
- Keep the final natural-language answer uncluttered.
- Use native `status` and `citation/source` events where possible.

Sources:

- https://docs.openwebui.com/features/chat-conversations/chat-features/
- https://docs.openwebui.com/troubleshooting/context-window/
- https://docs.openwebui.com/features/extensibility/plugin/development/events/

### 19.3 Live conversation recall beside compaction

The user does not want extra milestone checkpoints merely to protect active-chat continuity. Native compaction already occurs significantly below the real model limit.

Instead, use live conversation recall:

    Full transcript in Open WebUI — canonical and uncompacted on disk
      -> native rolling compaction for the normal prompt
      -> native chat-history search for simple/exact recovery
      -> optional hybrid semantic transcript index for vague/bounded recovery

Confirmed Open WebUI behavior:

- The full conversation remains stored and visible. Compaction only changes what is sent to the model.
- Native tools include `search_chats`, which searches titles/message content and returns chat IDs/snippets.
- Native `view_chat` returns the full history of a chat.

Native-only first version:

- Enable Builtin Tools -> Chat History.
- Instruct the model to use `search_chats` when the user references missing earlier context.
- Use `view_chat` only for reasonably small chats or when the complete history is truly required.

Why a semantic layer may still be necessary:

- Native chat search is text-oriented, so vague references such as `the option we picked earlier` may miss.
- `view_chat` returns a whole conversation and can recreate a context-overflow problem for a huge compacted chat.

External semantic recall, if evaluation justifies it:

- Index completed user/assistant turns or meaningful bounded segments immediately.
- Store user ID, chat ID, message ID, role, timestamp, topic/entity metadata, and neighbor links.
- The Open WebUI transcript remains canonical; the index is only a retrieval layer.
- Hybrid retrieval: exact/full-text + semantic similarity + current-chat/recency weighting + reranking.
- Return roughly 3-6 passages plus neighboring messages rather than reopening the whole chat.
- Progressive scope: current chat -> current folder/project -> all chats belonging to that user.
- Trigger proactively on words such as `earlier`, `we decided`, `you already asked`, contradictions, missing source IDs, or model uncertainty.
- Show a native status/tool card and source message links, but keep the final answer normal.

Important accuracy note:

- This resembles the observable behavior of ChatGPT/Claude/Gemini-style assistants, but their precise internal retrieval architectures are proprietary and must not be claimed as confirmed.

Sources:

- https://docs.openwebui.com/features/chat-conversations/chat-features/history-search/
- https://docs.openwebui.com/features/extensibility/plugin/tools/

### 19.4 Conversation-index deletion, forks, and garbage collection

The user requires deleted chats/messages to disappear from embeddings so storage does not grow forever, without breaking forks.

Confirmed native behavior:

- Open WebUI forks are independent copies of history up to the selected message.
- Current Open WebUI chat deletion explicitly deletes its own `chat_message` rows and publishes `chat.deleted`.
- Event Functions can react to lifecycle events such as `chat.deleted` without modifying Open WebUI source.

Use content-addressed canonical segments plus chat-message references:

    canonical_segment
      id/content_hash
      normalized_text
      embedding

    chat_message_reference
      user_id
      chat_id
      message_id
      canonical_segment_id
      branch/position metadata

Fork behavior:

- A fork receives independent reference rows.
- Identical copied text reuses the canonical segment/embedding instead of embedding again.
- Deleting the parent removes only the parent's references.
- The canonical segment remains while the fork still references it.
- When the final reference disappears, delete the canonical text and vector.

Lifecycle handlers:

- `chat.deleted`: tombstone and remove all references for the chat.
- `message.deleted`: remove only that message's references.
- `message.updated`: rehash/reindex and garbage-collect the old orphan if unused.
- `chat.created`/fork/import: create independent references while reusing canonical embeddings.
- User deletion: delete all indexed conversation records for that user.

Reliability and maintenance:

- Make event processing idempotent and safe under duplicate delivery/multiple workers.
- Make deletion unavailable to retrieval immediately; physical orphan purge may follow shortly.
- Run nightly reconciliation against live Open WebUI chat/message IDs to catch missed events, interrupted operations, restorations, and old bugs.
- Observe total chats/messages/segments, reused embeddings, orphan count, deletion lag, and reconciliation failures.

Selective provenance cascade chosen: A.

When a chat is deleted:

- Delete transcript embeddings and episode records belonging to it.
- Delete inferred memories supported only by that chat.
- Preserve explicitly confirmed memories and independently saved canonical documents/artifacts.
- If the same evidence survives in a fork or another source, retain it and recompute provenance/confidence.

Sources:

- https://docs.openwebui.com/features/chat-conversations/chat-features/
- https://docs.openwebui.com/features/extensibility/plugin/functions/event/
- https://github.com/open-webui/open-webui/blob/main/backend/open_webui/models/chats.py
- https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/chats.py

### 19.5 Conflict resolution selected: automatic precedence

Choice A selected.

When surviving sources conflict, resolve automatically using authority, recency, confidence, and provenance. Mention the conflict only when it could materially affect the answer/action or when neither source clearly wins.

Current precedence:

1. Current explicit user instruction.
2. Current verified canonical document/repository/artifact.
3. Confirmed memory with provenance.
4. Current KB extraction/index of a canonical source.
5. Inferred memory with confidence/evidence.
6. Episodic summary used for discovery.
7. Old raw chat or unverified web information.

Rules:

- Indexes discover; canonical sources decide.
- Explicit current correction overrides old memory.
- Current primary web sources decide current external facts.
- Do not silently merge two genuinely incompatible authoritative sources.

### 19.6 Memory backend: hybrid A with clean fallback to external-only C

The user prefers hybrid A if possible but reports that native Open WebUI Memory feels poor and rarely saves anything.

Current native facts:

- Merely enabling Memory does not guarantee proactive saving.
- Native Function Calling must be enabled and the model's Builtin Tools -> Memory category must be enabled.
- The chat model normally decides whether to call `add_memory`.
- Background review is disabled by default (`ENABLE_MEMORY_BACKGROUND_REVIEW=False`).
- Default review interval is 10 turns when enabled.
- Native docs explicitly say extraction/recall quality is model-dependent.
- Native user/context injection budgets default to 2000 characters each.

Revised hybrid architecture:

    External memory ledger — authoritative
      inferred + confirmed memories
      confidence/evidence/provenance/decay/deletion cascade
        -> on-demand external retrieval
        -> synchronize confirmed active subset
             -> native Open WebUI Memory as cache/review convenience

Do not rely on Open WebUI to notice and save memories.

- An external background extractor uses the configured cheap Gemini task model.
- It writes directly to the external authoritative ledger.
- Confirmed/high-confidence active memories may be copied into native Memory for built-in injection and the familiar review screen.
- Main-model native writes remain allowed and should be mirrored into the external ledger.
- Native edits/deletions should synchronize back where supported, with periodic reconciliation as a safety net.
- Native background review will probably be disabled to avoid opaque duplicate extraction.
- If native synchronization is brittle, disable the native cache and operate as C without migrating the real data.

Extraction cadence chosen: A, every completed turn.

- Run asynchronously after every completed user/assistant exchange so response latency is unaffected.
- Detect explicit facts, corrections, preferences, conversation-style patterns, project context, decisions, and open loops.
- Explicit corrections/preferences upsert immediately.
- Inferred items include confidence and exact supporting message IDs.
- Reinforce/merge existing items instead of blindly appending duplicates.
- Run broader deduplication, contradiction checks, confidence recalculation, and decay about every 10 turns.
- Respect chat/message deletion through the provenance cascade.

Source:

- https://docs.openwebui.com/features/chat-conversations/memory/

### 19.7 Episodic generation selected: incremental topic-shift episodes

Choice A selected.

- Raw messages are indexed immediately for live recall.
- An episode groups a meaningful segment such as a completed decision, resolved problem, major correction, or clearly changed topic.
- Close/update episodes asynchronously when a semantic boundary is detected.
- Each episode links exact source messages and never replaces them.
- This is not an additional compaction checkpoint.
- Deletion and fork behavior follows the reference/provenance design above.

### 19.8 Tool loading: adaptive broad core plus automatic semantic gateway

Native Open WebUI findings:

- Native Mode lets the model choose among attached tools.
- Built-in categories inject their available schemas.
- Workspace/MCP/OpenAPI tools are attached at model/chat level.
- MCP Function Name Filter List is a static exposure filter.
- Current Open WebUI does not appear to offer general semantic on-demand tool loading.

Relevant alternatives/references:

- Community AutoTool/Advanced Tool Use experiments implement tool selection/search, but require code/security/current-version audit.
- Anthropic's advanced-tool-use pattern combines tool search, programmatic invocation, and examples to avoid enormous schema payloads.
- ToolHive currently advertises self-hosted gateway/registry/runtime components, semantic tool search, virtual MCP aggregation, access policy, audit, Prometheus, and OpenTelemetry.
- MCP recommends caching tool definitions and refreshing them when servers send `notifications/tools/list_changed`.

Audit order before custom building:

1. Open WebUI static attachment/filtering baseline.
2. ToolHive gateway/runtime pieces, avoiding the full platform if unnecessary.
3. Current community Advanced Tool Use plugin.
4. Thin custom gateway only if the above fail requirements.

Primary choice: A, provided it meets reliability targets. Fall back toward B static domain packs or C larger default sets if real evaluation warrants it.

Reliable A shape, without mutating Open WebUI internals per request:

    search_capabilities(query)
    describe_capabilities(tool_ids)
    invoke_capability(tool_id, arguments)

Gateway responsibilities:

- Discover MCP/OpenAPI tools and cache/version exact schemas.
- Search names, descriptions, examples, domains, permissions, and usage history.
- Return a small candidate set, normally 3-6.
- Validate arguments using the authoritative JSON schema before invoking.
- Enforce user identity, authorization, action risk, confirmations, timeouts, retries, and audit.
- Refresh catalog on MCP list-change notifications and periodic reconciliation.
- Show native statuses such as `Loading office capabilities...`.
- Fail gracefully to the always-loaded core/manual packs.

Always-loaded core policy:

- Start broad, about 15-25 callable functions.
- Core includes memory, conversation recall, files/KB, web, notes, tasks, time/calculation, artifact access, and basic read-only terminal access.
- `Light` means small schema, frequent, reliable, low-risk, and low-auth-friction—not necessarily low compute.
- Frequently selected compact tools may graduate into the core after evaluation.
- If routing becomes extremely reliable, shrink toward B/minimal core.
- If it misses or adds excessive latency, expand selectively toward C.
- Never change the permanent/global core merely because one chat used a tool once.

Sources:

- https://docs.openwebui.com/features/extensibility/plugin/tools/
- https://docs.openwebui.com/features/extensibility/mcp/
- https://www.anthropic.com/engineering/advanced-tool-use
- https://modelcontextprotocol.io/docs/develop/clients/client-best-practices
- https://github.com/stacklok/toolhive
- https://www.reddit.com/r/OpenWebUI/comments/1uf99y9/advanced_tool_use/

### 19.9 Specialized-tool promotion: gateway-side chat binding

The user initially chose permanent promotion after successful use, then clarified that it must attach to the current chat rather than the model's global defaults.

Desired behavior:

- Successfully discovered/used capability remains active for that chat.
- It does not modify the model's defaults or other chats.
- Forks inherit independent copies of the active bindings.
- Deleting a chat removes its bindings.
- User can remove/disable a promoted capability.

Current Open WebUI limitation found in main source:

- `selectedToolIds` is frontend state and is sent on each request.
- The current chat controls autosave persists `params` and `files`, not selected tool IDs.
- No documented supported server API lets an external tool tick another tool in the native picker.
- Browser `execute` manipulation or URL parameter tricks would be brittle and require a live browser.
- A literal native-checkbox synchronization would require a small frontend patch/upstream feature.

Choice A selected to preserve the no-fork preference:

    chat_capability_binding
      user_id
      chat_id
      tool_id
      activated_at
      last_used_at

- Persist promotion inside the gateway.
- Skip rediscovery or strongly prioritize bound capabilities in later turns.
- Display active capabilities through native status events and a compact Rich UI card.
- Do not patch Open WebUI merely to tick the native checkbox.

Sources:

- https://github.com/open-webui/open-webui/blob/main/src/lib/components/chat/Chat.svelte
- https://docs.openwebui.com/features/chat-conversations/chat-features/url-params/

### 19.10 Context budget: use the full configured compaction budget

The user does not want an additional arbitrary 60-70% assembler quota. Their relevant models advertise roughly 1M tokens; Open WebUI is currently configured around a 300k compaction threshold with a 500k token cap.

Clarified Open WebUI semantics:

- `CONTEXT_COMPACTION_TOKEN_THRESHOLD=300000` means automatic compaction normally begins around 300k.
- A model's `compact_token_threshold` can override the global threshold.
- `CONTEXT_COMPACTION_TOKEN_CAP=500000` only bounds how high a per-model threshold may be.
- The cap does not truncate the conversation, limit the actual request, or force compaction by itself.
- `/status` percentage is relative to the effective compaction threshold, not the provider's advertised context window.
- Thus 100% means 300k with no override, or 500k with a 500k per-model threshold.
- Compaction is not a hard ceiling: it may decline on unsuitable message boundaries or fail, and RAG/tool/inlet content can add tokens after earlier estimates.

Architecture decision:

- Allow the context assembler to use effectively 100% of the configured compaction budget.
- Do not reserve another large percentage inside that threshold.
- The unused gap between a 300k/500k threshold and a roughly 1M provider window already provides substantial operational headroom for response, reasoning, tools, RAG, estimation error, and a failed/delayed compaction.
- Keep only an emergency ceiling below the actual provider limit to prevent a single enormous attachment/tool result from leaping past it.
- Verify the dedicated compaction model can accept the entire block it must summarize and use thinking disabled because of the confirmed 1000-output-token/reasoning bug.

Final decision:

- Use a 300k compaction threshold for every model, including Gemini models with a verified roughly 1M window.
- Set the global token cap to 300k as well, so no unnoticed per-model override can grow routine prompts beyond the chosen threshold.
- Do not keep a standing 500k exception. If a genuinely unusual task needs more raw context, raise the threshold/cap explicitly and temporarily, then restore 300k.
- The remaining provider window is operational headroom for the response, reasoning, tools, RAG, token-estimation error, and compaction failure or delay.
- Emergency filtering still acts near the real provider limit rather than imposing another routine 60-70% quota.

Rationale:

- The purpose of rolling compaction plus targeted live transcript retrieval is to avoid repeatedly sending enormous raw histories.
- 300k is already a very large working set. Increasing it routinely would add latency and cost while postponing the point at which the compaction and recall system proves that it works.
- Full uncompacted transcripts remain canonical on disk, so selected omitted details can be recovered through chat-history or semantic retrieval without placing the entire history in every request.

Sources:

- https://docs.openwebui.com/troubleshooting/context-window/
- https://docs.openwebui.com/reference/env-configuration/

### 19.11 Context assembly selected: elastic protected core

Choice A selected.

Use an elastic context assembler rather than fixed per-source quotas or allowing a planning model to control the entire prompt without guardrails.

Protected core, retained first:

1. System, safety, deployment, and active model/tool instructions.
2. The current user request and directly attached or explicitly referenced inputs.
3. Recent turns needed for immediate conversational coherence.
4. The latest valid rolling-compaction state and active task/open-loop state.

Elastic remainder:

- Confirmed personal memory and preferences relevant to the request.
- Canonical documents or narrowly selected KB passages.
- Targeted current-chat or cross-chat transcript recovery.
- Current web evidence for informational/current questions.
- Tool results, generated intermediate state, and supporting source excerpts.

Packing and eviction rules:

- Allocate the remainder dynamically according to the current task rather than reserving fixed percentages for chat, memory, KB, web, or tools.
- Distill very large attachments, web pages, transcripts, and tool outputs into source-linked extracts; retain raw content only when exact structure or wording is necessary.
- Evict stale, redundant, low-authority, and low-relevance material first.
- Never let retrieved background displace the current request or the minimum state required to execute it correctly.
- Retrieval relevance controls what enters the prompt; the separate authority precedence in section 19.5 controls what wins when included sources disagree.
- The assembler may consume effectively the full 300k threshold, with the provider's remaining context window serving as operational headroom.

### 19.12 Artifact/media boundary selected: unified content backbone

Choice A selected.

Use one canonical identity, storage, reference, version, ownership, and lifecycle layer for all durable binary content:

- Uploaded durable documents.
- Agent-generated DOCX/XLSX/PPTX/PDF artifacts.
- Audio and video promoted to the durable library.
- Durable images and generated visual assets.
- Extracted text, transcripts, previews, thumbnails, chunks, embeddings, and processing records as derivatives of the canonical binary.

Keep processors independent behind this shared backbone:

- Office artifact creation/editing and OnlyOffice callbacks.
- Document extraction, OCR, chunking, and embedding.
- Gemini-native audio/video/YouTube understanding and transcription fallback.
- Image generation/editing and visual metadata.
- Preview/render generation and visual verification.

The backbone is not a monolithic processing service. It owns content identity and lifecycle; specialized workers own format-specific processing. This provides one deduplication and deletion model without coupling every processor together.

Selective durability still applies:

- Temporary screenshots, disposable exports, tiny snippets, and explicitly chat-local attachments do not enter durable canonical storage.
- Promoted content uses a canonical content hash plus independent user/chat/KB/artifact references.
- Exact duplicates reuse the binary and compatible derivatives while retaining distinct references and aliases.
- Version chains apply to intentional edits of an existing artifact; independently uploaded changed bytes remain separate unless the user explicitly groups them.

### 19.13 Native Open WebUI storage check before backend selection

Current official Open WebUI support was rechecked on 2026-08-09:

- Local filesystem storage is the default.
- `STORAGE_PROVIDER` natively supports S3-compatible storage, Google Cloud Storage, and Azure Blob Storage.
- S3-compatible endpoints include deployments such as MinIO and R2, subject to endpoint/TLS compatibility.
- `STORAGE_LOCAL_CACHE` controls whether a local processing copy remains after a cloud upload.
- Official scaling guidance describes a shared filesystem as the simplest option for most deployments and object storage as useful for cloud-native/large-scale deployments or managed durability.
- The storage provider controls uploaded-file binary placement. It does not replace the database, vector store, or the richer canonical catalog/version/reference/deduplication behavior required by this design.

Design implication:

- Do not deploy a separate object store merely because Open WebUI needs one; reuse its native storage provider and existing physical backend where possible.
- The unified content backbone still needs its own canonical metadata/reference layer, but its binary-storage adapter should align with Open WebUI's local/S3-compatible choices and should register or link user-visible files through supported Open WebUI APIs rather than copying binaries unnecessarily.

Sources:

- https://docs.openwebui.com/reference/env-configuration/
- https://docs.openwebui.com/getting-started/advanced-topics/scaling/
- https://docs.openwebui.com/tutorials/maintenance/s3-storage/
- https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/files.py

### 19.14 Physical backend selected: existing Garage S3 with a local-capable adapter

The user initially selected local-first A but disclosed that Garage S3 is already deployed. Because this removes almost all incremental infrastructure cost, the final recommendation shifts to B for production while retaining A's adapter/fallback property.

Decision:

- Use the existing Garage deployment as the production binary backend for Open WebUI uploads and the unified content backbone.
- Keep a storage interface that can use a local filesystem for development, recovery, or a deliberately simplified deployment.
- Store authoritative metadata, content hashes, references, ownership, derivative records, and application-level version chains in Postgres; Garage stores binaries/derived binary objects.
- Keep `STORAGE_LOCAL_CACHE=True` initially so Open WebUI extraction and compatibility behavior remain conservative; reconsider cleanup only after end-to-end processing tests pass.

Compatibility findings:

- Open WebUI exposes `S3_ENDPOINT_URL`, `S3_REGION_NAME`, `S3_BUCKET_NAME`, `S3_ADDRESSING_STYLE`, `S3_KEY_PREFIX`, and credentials for S3-compatible providers.
- Garage implements Signature V4, path-style and virtual-host addressing, presigned URLs, core object operations, and multipart upload.
- Garage does not implement S3 object tagging. Leave Open WebUI `S3_ENABLE_TAGGING=False`.
- Garage does not implement S3 bucket versioning. This is acceptable because artifact/document versions are explicit immutable objects connected by Postgres version records; do not rely on bucket versioning.
- Exact endpoint, region, addressing style, TLS CA, checksum behavior, multipart upload, download, deletion, and presigned-link behavior must be covered by a deployment smoke test before migration.

Sources:

- https://docs.openwebui.com/reference/env-configuration/
- https://garagehq.deuxfleurs.fr/documentation/quick-start/
- https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/
- https://github.com/open-webui/open-webui/issues/16758

### 19.15 Garage isolation selected: separate native-upload and canonical-content buckets

Choice A selected.

Use separate Garage buckets and service credentials:

- `openwebui-uploads`: native Open WebUI uploads, temporary processing copies, and normal paperclip lifecycle.
- `assistant-content`: durable canonical documents, artifacts, audio/video, durable images, versions, and binary derivatives owned by the unified content backbone.

Access model:

- The Open WebUI storage credential can read/write only `openwebui-uploads`.
- The content-backbone credential can read `openwebui-uploads` for ingestion and read/write `assistant-content`.
- Only the content backbone performs canonical deletion, garbage collection, version writes, and derivative lifecycle operations.
- User-facing downloads and OnlyOffice access use short-lived signed application URLs or presigned URLs rather than exposing Garage credentials.

Promotion behavior:

- `Add to Library` writes directly into canonical storage where feasible.
- A normal paperclip upload lands in `openwebui-uploads`; if promoted, the worker hashes it, reuses an existing canonical object or writes a new one, creates references, and only cleans the native duplicate when doing so cannot break its chat/file reference.
- Temporary overlap during asynchronous promotion is acceptable; steady state should converge to one canonical durable binary plus references.
- Reconciliation detects promotion failures, dangling references, and redundant native objects safe to purge.

Why not prefixes in one bucket:

- Garage authorization is based on access-key-per-bucket rights rather than AWS IAM-style prefix policies. Separate buckets provide meaningful service isolation with little extra operational cost.

### 19.16 Native evaluation and observability audit

Current official Open WebUI support was rechecked on 2026-08-09:

- Native message thumbs-up/down feedback and sibling-response comparison.
- Arena models for randomized head-to-head model comparison.
- Per-instance Elo-style leaderboards, activity history, and topic tags.
- Evaluation data stays on the self-hosted instance unless community sharing is explicitly used.
- Native OpenTelemetry export for traces, metrics, and logs, including FastAPI, SQLAlchemy, Redis, and outbound HTTP instrumentation.
- Health/model-connectivity endpoints and structured logging for production diagnostics.

These features cover human preference/model comparison and runtime observability. They do not replace deterministic automated behavioral regression tests for this customized assistant architecture.

The required automated checks must exercise system behavior such as:

- Extracting a preference, correcting it, and retrieving the corrected value later.
- Recovering an omitted detail from a compacted conversation without reopening the full transcript.
- Deleting a source chat and removing dependent embeddings/inferred memories while preserving references held by a fork.
- Uploading an exact duplicate and proving that compatible extraction/embedding work is reused.
- Editing an artifact, retaining its prior version, and refreshing extraction/search.
- Selecting the correct tool through the semantic gateway and respecting chat-scoped promotion.
- Surviving compaction failure/delay without exceeding the real provider context limit.

Sources:

- https://docs.openwebui.com/features/administration/evaluation/
- https://docs.openwebui.com/reference/monitoring/otel/
- https://docs.openwebui.com/reference/env-configuration/
- https://docs.openwebui.com/enterprise/deployment/

### 19.17 Evaluation architecture selected: layered and lightweight

Choice A selected.

Use three complementary layers:

1. Native human evaluation
   - Keep Open WebUI message ratings and Arena/model comparisons available.
   - Use them for subjective qualities such as usefulness, directness, personalization, writing style, and model preference.
   - Keep evaluation data private by default; community sharing is optional rather than required.

2. Native operational observability
   - Enable OpenTelemetry traces, metrics, and logs plus structured application/audit logs.
   - Track latency, token/context size, compaction runs/failures, retrieval activity, tool selection, provider errors, extraction jobs, dedup reuse, deletion lag, and background-worker failures.
   - Prefer status cards/events in the chat for user-relevant activity and the telemetry stack for detailed diagnosis.

3. Automated behavioral regression suite
   - Start with a small deterministic set focused on high-risk cross-service behavior rather than broad academic benchmarks.
   - Use synthetic fixtures containing known preferences, contradictions, forks, duplicate files, versioned artifacts, and compacted transcript details.
   - When a real failure occurs, sanitize and add it as a permanent regression case.
   - Run the relevant subset before upgrades/config changes and the full suite after substantial changes.

Initial upgrade-gate behaviors:

- Memory extraction does not invent unsupported facts and applies explicit corrections.
- Personalization influences style without overriding the current instruction.
- Compacted-chat recovery retrieves the correct bounded passage and cites its source message.
- Deleting a chat removes its exclusive retrieval/memory evidence; a surviving fork retains shared evidence.
- An exact duplicate does not repeat compatible extraction/embedding work.
- Artifact edits create a new version, preserve the old binary, and refresh derivatives.
- Semantic tool routing selects an appropriate capability and respects chat-scoped promotion.
- Web/current questions use current web sources while personal questions prefer local sources.
- Failure paths are visible, retryable/idempotent, and do not leave accessible half-deleted or falsely completed state.

Do not deploy a large evaluation platform initially. Reassess Promptfoo, DeepEval, Langfuse, or similar tooling only if the small harness becomes difficult to maintain or deeper experiment tracking/model judging becomes valuable.

### 19.18 Regression execution selected: deterministic core plus live canaries

Choice A selected.

Split execution into three cost/reliability tiers:

1. Deterministic mocked/replayed suite
   - Run on every relevant code/configuration change.
   - Cover database/reference transitions, deletion and fork garbage collection, hash/cache reuse, version chains, packing/eviction rules, tool-registry filtering, permissions, retries, and idempotency.
   - Replay captured provider responses where orchestration logic needs realistic payloads without live variability.

2. Small live-provider canary
   - Run after deployment and on a modest schedule.
   - Verify authentication, request/response compatibility, model reachability, embedding dimensions, reranking, web search/fetch, Gemini media processing, image generation, Garage object operations, and compaction-model output.
   - Use tiny fixtures, tight cost/time limits, and explicit provider/model/version labels.

3. Expensive end-to-end suite
   - Run manually before major Open WebUI, model, extraction, storage, or architecture changes.
   - Exercise long-chat compaction and recovery, full document/media ingestion, Office editing/version refresh, cross-chat memory, and failure recovery with real services.

Do not make every regression test live. Logic correctness should be deterministic and cheap, while live tests exist specifically to detect provider/configuration drift.

### 19.19 Evaluation strictness selected: absolute lifecycle invariants plus repeated semantic checks

Choice A selected.

General rules:

- Deterministic storage, ownership, permission, deletion, fork/reference, deduplication, and versioning invariants must pass 100%.
- Nondeterministic model behaviors run three times and must satisfy their semantic invariants in at least two runs.
- Any critical data-loss, privacy, permission, destructive-action, or false-success violation fails the gate immediately regardless of the aggregate score.
- Live canaries retry once to distinguish a transient provider/network failure from persistent incompatibility; a second failure alerts and blocks an upgrade when the provider is required by that upgrade.
- Assert required facts, sources, tools, and state transitions rather than exact response wording.

Proposed concrete baseline gates:

Memory and personalization:

- An explicit durable preference/correction is written or queued during the same completed turn and becomes retrievable by the next turn.
- An explicit correction supersedes the previous value in all three deterministic retrieval checks.
- Inferred memories include evidence/provenance and confidence; unsupported high-confidence personal facts are forbidden.
- Current explicit instructions override stored style preferences every time.
- Deleting the sole source removes an inferred memory from retrieval immediately; confirmed memory survives according to the selected provenance policy.

Conversation/episode recall:

- A known detail removed from the assembled prompt by compaction is recovered in at least two of three live semantic runs.
- Recovery returns bounded passages with correct user/chat/message provenance rather than injecting the entire large transcript.
- Current-chat scope is searched before broader user history unless the request explicitly indicates another project/chat.
- A false confident claim of remembering unavailable evidence fails the check.

Tool routing:

- Required tool/capability appears in the candidate set and is selected in at least two of three semantic runs for representative requests.
- Forbidden, unauthorized, unavailable, or incompatible tools are selected zero times.
- A capability promoted for a chat is reused or strongly prioritized on the next matching request.
- Tool failure is surfaced truthfully and never presented as completed work.

Document/media deduplication:

- Two exact-byte uploads converge to one canonical object and one compatible derivative set, with two independent references.
- Compatible extraction and embedding job counters do not increment for the second exact duplicate.
- Changed processing versions create only the newly required derivatives, not another canonical binary.
- Deleting one reference preserves the shared object; deleting the last reference makes it unavailable immediately and schedules physical garbage collection.

Deletion and forks:

- Deleting a parent chat removes all parent-only transcript/episode references from retrieval immediately.
- A surviving fork retains its own copied references and shared canonical segments.
- Deleting the final referencing branch removes the canonical segment/vector through garbage collection.
- Repeated/duplicated lifecycle events produce the same final state and no negative reference counts.

Artifacts:

- A successful edit creates exactly one new version, preserves the prior binary, advances the current-version pointer, and refreshes extraction/preview/search derivatives.
- A failed OnlyOffice callback or AI edit leaves the previous version current and intact.
- The user can download/open every retained version and sees accurate version/status metadata.

Compaction and recovery:

- Automatic compaction targets the universal 300k threshold and never relies on a standing 500k override.
- The compacted state retains enumerated decisions, open loops, source/artifact IDs, and current task state from the fixture.
- The next turn stays below the real provider limit after retrieval/tool additions or activates the emergency oversized-input path instead of failing opaquely.
- A deliberately omitted old detail can be recovered through targeted transcript retrieval without replacing the rolling summary or loading the complete huge chat.

Operational/live-provider canaries:

- Each configured critical provider completes a minimal authenticated request and returns a schema/capability-compatible result.
- Embedding dimension, reranker shape, media timestamps, web citations, image output, and Garage object round trips match configured expectations.
- Metrics/traces include correlation IDs across Open WebUI, the gateway, content service, and workers for the test request.

### 19.20 Evaluation scope correction: lightweight maintenance smoke checks only

This section supersedes the heavyweight release-gate framing in sections 19.17-19.19.

The user correctly pointed out that this is a personal extension of Open WebUI, not a separately released multi-user product. Do not introduce a formal release process, extensive CI evaluation platform, or repeated model-grading program.

Final evaluation/maintenance decision: lightweight A.

- The Open WebUI deployment remains the product being configured and extended.
- Create a small deterministic smoke-check script only for custom components whose state transitions are risky: memory provenance/correction, transcript-reference cleanup, fork reference counting, content-hash reuse, artifact version preservation, and basic tool-gateway reachability.
- Keep the useful hard invariants from section 19.19 as test ideas, not as a formal release bureaucracy.
- After a meaningful Open WebUI/custom-service upgrade, manually run a handful of representative prompts/uploads/actions in the UI.
- Live-provider checks should be tiny connectivity/schema checks performed only when that provider/configuration changed or when diagnosing a problem.
- Native ratings/Arena remain optional everyday feedback features, not required test infrastructure.
- OpenTelemetry/logging remain production observability and debugging aids, not an evaluation platform that must be built before use.
- Do not run three repeated semantic trials by default. Add a focused regression example only after a real failure proves it valuable.

Suggested manual post-upgrade checklist:

1. Save and then correct one preference; verify the corrected value is recalled.
2. Recover one known detail from a compacted/older portion of a chat.
3. Upload the same small document twice; confirm canonical reuse/status.
4. Fork and delete a parent test chat; verify the fork remains usable and parent-only retrieval disappears.
5. Edit a disposable Office artifact; verify the previous version remains downloadable.
6. Trigger one dynamically selected tool and one web-backed informational query.

Use plain terms such as `maintenance smoke checks` or `post-upgrade checks`, not `release gates`.

## 20. Updated immediate continuation

The context-threshold question is resolved: use 300k for every model and raise it only temporarily for an exceptional task.

Continue roughly in this order:

1. Build the master capability matrix: native Open WebUI support vs community extension vs external service vs custom gap.
2. Propose the complete phased architecture and get approval before implementation.

Still do not implement until the design has been presented and approved.

## 21. Master capability matrix — current native audit and chosen posture

Audit snapshot: 2026-08-09 against current Open WebUI documentation/main behavior. Verify the actually deployed image/UI before applying configuration because the user tracks `main` and persistent ConfigVars can override environment values.

Legend:

- `Native`: use Open WebUI directly.
- `Native + thin extension`: configure the native feature and add only a small Function/adapter where needed.
- `Companion`: an external self-hosted service connected through supported OpenAPI/MCP/Function/event surfaces; no Open WebUI source fork.
- `Optional/deferred`: useful but not required for the first coherent assistant.

| Capability | Current Open WebUI/native position | Chosen posture | Loading/lifecycle |
|---|---|---|---|
| Current request and recent conversation | Native chat context and attachment handling | Native; protect current request, direct inputs, recent turns, and current task state in the elastic assembler | Always |
| Long-chat compaction | Native rolling Context Compaction, manual `/compact`, API `context_usage` | Native at a universal 300k threshold/cap; dedicated non-thinking compaction model; no standing 500k override | Automatic/background |
| Context visibility | Native APIs expose usage; user does not currently see `/status` | Native + thin status Function/card attached to chat controls if the deployed UI still lacks an adequate display | On demand/native UI |
| Exact old-chat recovery | Native `search_chats` and `view_chat`; full transcript remains canonical | Native first for literal/small recovery | Dynamic |
| Vague/semantic transcript recovery | Native history search is primarily text-oriented and full `view_chat` can be too large | Companion semantic conversation index returning bounded message passages with chat/message provenance | Dynamic, only on recall signals/uncertainty |
| Context assembly and truth precedence | Native prompt/RAG/tool assembly exists but not the selected cross-source authority policy | Thin Filter/gateway using protected core, relevance packing, and authority precedence; canonical sources decide, indexes discover | Every turn, minimal core plus dynamic retrieval |
| Personal memory | Native Memory now supports user/context types, paths, search, correction, background review, and configurable injection, but the user's real experience is that proactive saving is too unreliable to serve the goal | Companion external provenance-aware ledger is the only active authoritative memory path. Disable native automatic injection/background review for the Personal Assistant rather than weakening the design for apparent simplicity | Background extraction plus dynamic retrieval |
| Episodic/project continuity | No complete native topic-shift episode/provenance system | Companion worker creates incremental topic episodes and open-loop records; raw chats remain canonical | Background at topic boundaries |
| Working notes/drafts | Native Notes support model search/read/create/update and attach full content | Use Native Notes for living drafts, scratchpads, and explicit assistant notes; do not make them a duplicate authoritative memory database | Manual or agent-written |
| Structured task lists | Native chat-level Task Management with visible statuses | Native only | Dynamic for multi-step work |
| Calendar and scheduled prompts | Native Calendar and Automations with model tools and run history | Native only unless a later external calendar account is connected | Manual/tool-called/background schedule |
| Knowledge bases and document RAG | Native focused/full-context modes plus query/search/grep/view tools | Native; keep focused retrieval globally and full-source escalation through tools | Dynamic |
| Obsidian vault | Native KB can query it but cannot be the authoritative Git repository editor | Existing GitHub MCP for authoritative reads/writes plus oikb for synchronization; KB for discovery/citations | Dynamic reads/writes; background sync |
| KB synchronization | Native directory sync exists; oikb is an official ecosystem companion for large/remote sources | Keep existing oikb and immediate trigger tool | Background plus explicit trigger after writes |
| Document extraction/OCR | Native pluggable extraction engines include Docling and an external loader interface | Benchmark Xberg as a drop-in Docling/external backend; adopt only if representative PDFs/tables/scans beat current extraction. No source patch required | Background ingestion |
| Embeddings and reranking | Native external embedding/reranking configuration | Keep Gemini Embedding 2 at 1536 through LiteLLM with batch size 1 and current Voyage reranker; tune only from observed failures | Background indexing and dynamic retrieval |
| Current-chat files | Native list/query/grep/view file tools | Native | Dynamic |
| Global file administration | Native Centralized File Manager can view/search/manage uploaded files | Native for human administration; it does not replace semantic durable-library retrieval | Manual UI |
| Durable-file promotion | Native uploads create file hashes but do not provide the selected pre-processing global reuse and selective promotion policy | Native Add-to-Library adapter + companion content backbone; normal paperclip remains native with post-upload reconciliation | Explicit library flow or background promotion |
| Exact deduplication and derivatives | Native dedup is partial and context-specific | Companion content backbone: one content hash, canonical binary, compatible derivative/cache keys, many references | Background/ingestion |
| Canonical binary storage | Native supports local/S3/GCS/Azure | Native S3 provider plus existing Garage; separate `openwebui-uploads` and `assistant-content` buckets/keys; Postgres owns metadata and versions | Background/service |
| Chat deletion, forks, and garbage collection | Native chats/forks exist and native deletion removes its own records/events | Event Function + companion reference-counted index/content cleanup; logical removal immediately, orphan GC/reconciliation later | Event-driven/background |
| One-shot DOCX/XLSX/PPTX creation | Not a complete native Office generation engine; Tools/Open Terminal can create files | Review NEURA generators as useful MIT prototypes/references for structured creation, not as the canonical artifact lifecycle | Tool-called |
| Durable Office artifacts and human editing | Native Rich UI/actions can present controls but do not supply full OnlyOffice version lifecycle | Companion artifact service using the audited Odysseus OnlyOffice protocol, immutable versions, signed callbacks, previews, extraction refresh, and native chat artifact cards | Tool-called; manual editor |
| Existing-document structural editing | Community NEURA tools primarily generate; existing-format-preserving editing remains incomplete | Artifact service with format-specific structural editors; OnlyOffice for human edits; verify round-trip fidelity | Tool-called/manual |
| Resume/high-quality PDF generation | Native code/terminal can generate PDFs but no opinionated resume pipeline | Artifact service using Typst or LaTeX when advantageous, plus render/visual verification and downloadable versioning | Tool-called |
| Inline visualizations and interactive artifacts | Native code execution, Mermaid, HTML/SVG/JS artifacts, Rich UI, previews | Use native first. Inline Visualizer is optional only if its marker-based convenience materially improves UX; offline mode required if adopted | Dynamic output/optional tool |
| Instance pruning/cleanup | Native lifecycle plus file manager; external indexed/canonical state needs custom reconciliation | Do not make community Prune the correctness mechanism. Optionally audit/use its preview UI for old native debris after backups; canonical cleanup remains owned by our services | Manual maintenance/background reconciliation |
| Image generation/editing | Native Gemini/OpenAI/ComfyUI/Automatic1111 support | Keep existing Gemini Image/Nano Banana 2 integration | Tool-called |
| Speech input/output | Native local/browser/remote STT and multiple TTS providers | Use native STT/TTS for voice UX; configure an API provider or lightweight local Whisper only if useful | User-triggered/native |
| Audio/video/YouTube understanding | Native STT handles transcription and voice/video-call UX, but normal attachments do not guarantee original multimodal media reaches Gemini natively | Companion Gemini-native media ingestion tool with durable transcript/timestamps/visual-audio understanding; Whisper fallback | Tool-called plus background indexing |
| Music generation | Not required by any confirmed user scenario; possible through ComfyUI/external tools | Defer entirely until explicitly requested | Manual/optional |
| Web search and page fetch | Native agentic web tools with many search providers and configurable loaders | Native web search/fetch, used aggressively for general/current information; compare configured providers/loaders with representative queries | Dynamic |
| Time and calculation | Native builtin tools | Native only; no custom calculator unless a real correctness gap appears | Dynamic/core tool |
| Skills/procedural memory | Native Skills support manifests, lazy `view_skill`, `$` mention, and per-chat toggles | Native only; attach broad manifest set while keeping full instructions lazy | Lazy/on demand |
| Reusable prompt commands | Native versioned Prompts/slash commands | Native for explicit workflows such as capture, incident, process-inbox, or resume | Manual |
| Tool/service connectivity | Native Tools, Functions, OpenAPI, and Streamable HTTP MCP | Native connectivity layer; external heavy/stateful capabilities remain separate services | Dynamic |
| Dynamic tool-schema routing | Native models choose only from tools already attached/enabled; no complete semantic registry retrieval | Companion semantic capability gateway with a small broad core and retrieved specialized schemas; chat-scoped binding after successful use | Dynamic |
| Chat-scoped tool/skill selection | Native per-chat toggles exist for skills/tools, but an external gateway cannot reliably tick native checkboxes without core/UI work | Persist promotion inside the gateway and display it through native status/Rich UI; do not patch UI merely to mirror a checkbox | Dynamic/chat state |
| Terminal/computation | Native recommended Open Terminal integration; current versions support a shared container with per-user Linux accounts for small trusted groups | Keep the already integrated Open Terminal. Shared or built-in multi-user mode is sufficient for one/two trusted users; no enterprise Terminals dependency | Manual toggle/tool-called |
| Browser page context/actions | Core Open WebUI does not live inside arbitrary tabs. NEURA Browser supplies page-scoped sidebar/DOM actions but is an early browser extension; official Open WebUI Computer instead exposes a whole machine/workspace | Defer all custom browser integration. Use NEURA Browser unchanged as an optional manual extension when page context/actions are wanted; reassess only if its real limitations become blocking | Manual opt-in |
| Model/provider compatibility | Native multi-provider/OpenAI-compatible support; capabilities differ by provider/model | Keep LiteLLM/provider layer and a small capability registry for tools, vision, audio/video, thinking, structured output, and context limits | Always metadata; routing dynamic |
| Model role routing | Native task model and per-model settings exist; user already has a cheap Gemini Lite task model | Use explicit roles only: user-selected chat model, cheap task/background model, dedicated compaction model, media/image providers. No autonomous multi-model router initially | Native configuration |
| Source citations and freshness | Native RAG/web citations exist but authority/freshness conflicts need policy | Native citations plus provenance metadata and selected precedence rules; web primary sources for current facts, canonical personal sources for user facts | Every retrieved answer |
| Actions and confirmation | Native tools/events can ask for confirmation; no universal finished HITL policy for every external tool | Thin policy layer: autonomous reads and assistant-owned reversible writes; confirm communication, purchases, permissions, destructive operations, and consequential external writes | Every action |
| Identity/access | Native users, RBAC, groups, SSO/OIDC/LDAP, tool user metadata/OAuth forwarding | Minimal native user separation for one/two trusted users; no SSO project unless a real need appears; use separate service identities and owner IDs | Always |
| Observability | Native OpenTelemetry, structured logs, audit logs, health endpoints, ratings/Arena | Enable native observability; add service-specific metrics/status for memory, gateway, indexing, dedup, and compaction | Background/native UI |
| Maintenance verification | Native ratings/telemetry are not behavioral regression tests | Small custom smoke-check script plus six manual post-upgrade checks; no formal release/evaluation platform | Manual after meaningful changes |

### 21.1 Community references disposition

- NEURA Documents/Spreadsheets/Slides: useful structured-generation code and template ideas. They generate editable Office binaries through Workspace Tools, but reports still show download/dependency rough edges and existing-document editing is not yet the durable, versioned round-trip workflow required here. Treat as candidates/components, not the architecture.
- NEURA Office: a directory/front door for those three tools, not itself a new execution or storage layer.
- NEURA Browser: genuinely interesting page-context/action UX, but currently an early extension with acknowledged auth/layout/DOM edge cases and page-scope limits. Evaluate later rather than putting it in the assistant core.
- Inline Visualizer: useful marker-driven live visual output and now offers an offline mode. Native Rich UI/artifacts/code execution cover much of the same category, so install only after a direct UX comparison.
- Prune: useful previewable cleanup utility, but cannot own referential correctness for the custom transcript/content/memory stores. It may supplement—not replace—our event cleanup and reconciliation.
- Xberg: now MIT-licensed and can present Docling-compatible or external-loader endpoints, making it a true no-core-patch candidate. Claims about throughput/quality are vendor/community claims until benchmarked against the user's real documents; image captioning concurrency also had a reported limitation.
- Open WebUI Computer: an official companion and a stronger alternative when the goal is access to a real persistent machine/workspace. It overlaps with, but is not identical to, a browser-tab DOM assistant; existing Open Terminal remains the simpler execution choice for this deployment.

### 21.2 Loading policy summary

Always/minimal:

- Current instruction and direct inputs.
- Recent turns and current task state.
- Compact deployment/personality/authority policy.
- Model capability metadata and the tiny core tool set.

Dynamically retrieved:

- Confirmed personal memory and topic episodes.
- KB/document/chat passages.
- Web evidence.
- Specialized tool schemas.
- Artifact/media metadata and relevant tool results.

Lazy:

- Full Skill bodies via native `view_skill`.
- Large source files through query/grep/view escalation.

Manual/explicit:

- Destructive or consequential external actions.
- Full OnlyOffice editing.
- Heavy Terminal or browser-agent sessions.
- Optional visualization/music/experimental tools.

Background:

- Compaction.
- Memory/episode extraction.
- oikb and durable content indexing.
- Deduplication, derivative generation, garbage collection, and reconciliation.
- Native scheduled Automations.

### 21.4 Memory non-regression rule

The project began because stock Open WebUI Memory was not reliably producing the desired continuity. Native feature growth does not change the user's observed result and must not silently collapse the architecture back into stock Memory.

Final active-memory posture:

- The external memory ledger owns automatic extraction, inferred personalization, confirmed facts, corrections, confidence, provenance, decay/supersession, and retrieval.
- It may write without confirmation inside its assistant-owned store, consistent with the selected autonomy policy.
- Every memory remains attributable to source chat/message/document evidence or an explicit user confirmation.
- Explicit user corrections update/supersede immediately.
- Deleting the only supporting chat removes dependent inferred memory; independently confirmed/canonical evidence survives.
- Native Open WebUI Memory is not used as an active cache in v1 because two synchronized memory stores add stale/conflict risk without solving a user need.
- Disable native memory system-context injection and background review for the Personal Assistant. Native Memory may remain enabled elsewhere only for isolated experiments, not as a dependency of this architecture.
- Simplicity work must remove glue and optional features before reducing memory quality, provenance, or automatic capture.

### 21.5 Browser scope selected

Choice A selected with the user's clarification:

- Do not build or modify a browser integration in the initial architecture.
- NEURA Browser may be installed/used unchanged as an optional client for current-tab context and actions.
- Keep it outside the assistant's core correctness guarantees and canonical memory/file lifecycle until real usage justifies deeper integration.
- Do not duplicate it with a custom browser MCP, Open WebUI source changes, or an Open WebUI Computer deployment merely for feature completeness.

### 21.3 Current audit sources added after the checkpoint

- https://docs.openwebui.com/features/
- https://docs.openwebui.com/features/extensibility/
- https://docs.openwebui.com/features/extensibility/plugin/functions/
- https://docs.openwebui.com/features/extensibility/mcp/
- https://docs.openwebui.com/features/workspace/knowledge/
- https://docs.openwebui.com/features/workspace/skills/
- https://docs.openwebui.com/features/notes/
- https://docs.openwebui.com/features/chat-conversations/chat-features/task-management/
- https://docs.openwebui.com/features/chat-conversations/chat-features/automations/
- https://docs.openwebui.com/features/calendar/
- https://docs.openwebui.com/features/chat-conversations/memory/
- https://docs.openwebui.com/features/chat-conversations/data-controls/files/
- https://docs.openwebui.com/features/chat-conversations/chat-features/code-execution/
- https://docs.openwebui.com/features/open-terminal/
- https://docs.openwebui.com/ecosystem/computer/
- https://docs.openwebui.com/features/chat-conversations/image-generation-and-editing/
- https://docs.openwebui.com/features/chat-conversations/audio/speech-to-text/stt-config/
- https://docs.openwebui.com/reference/monitoring/otel/
- https://docs.openwebui.com/ecosystem/knowledge-base-sync/
- https://github.com/xberg-io/xberg
- https://docs.xberg.io/
- https://github.com/ianustec/neura-office
- https://github.com/ianustec/neura-for-browser
- https://github.com/Classic298/open-webui-plugins

## 22. Immediate continuation after the master matrix

The master capability matrix is now drafted.

### 22.1 Memory clarification: do not regress to native Open WebUI Memory

The project began because the user found stock Open WebUI Memory effectively useless in real use. Native improvements discovered during the audit do not change the selected architecture.

Final hybrid meaning:

- The external provenance-aware memory ledger is authoritative.
- It owns automatic per-turn extraction, confirmed versus inferred classification, confidence, source chat/message references, corrections, conflict handling, decay, deletion cascades, and retrieval.
- Native Open WebUI Memory is never the authoritative source and the chat model is never solely responsible for deciding whether something is remembered.
- Native Memory may be retained only as a synchronized convenience/cache or user-visible compatibility surface if it stays consistent and genuinely adds value.
- If native synchronization is stale, lossy, or confusing, disable native Memory without weakening the external memory system.
- Hybrid refers to the whole native-plus-external assistant architecture, not to splitting truth equally between the two memory stores.

### 22.2 Browser decision: stock NEURA as an optional uncoupled extension

Choice A selected with clarification.

- Do not build or modify browser-tab assistance in the initial architecture.
- The user may install/use NEURA Browser unchanged for current-page context and its existing agent mode.
- Treat NEURA as optional and uncoupled: the memory, content, transcript, and tool-routing core must not depend on it.
- Review permissions/network behavior before installation and track upstream updates, but do not fork/customize it unless real use later exposes a necessary gap.
- Open WebUI Computer remains a separate future option for whole-machine/workspace access, not a prerequisite.

### 22.3 Next architecture decision

Choice A selected: one modular assistant-core service with clear internal modules.

This simplifies deployment rather than reducing capability. Keep module contracts explicit so a future workload can be split out without redesigning callers.

Do not put the stateful companion logic directly inside Open WebUI Functions and do not begin with separate memory/transcript/tool/content microservices.

## 23. Phased architecture design — section 1: component boundaries

Approved by the user: choice A.

### 23.1 Open WebUI remains the product and frontend

Keep upstream Open WebUI unchanged and let it own:

- Authentication, users, chats, branches/forks, folders, native Files UI, Notes, Tasks, Calendar, Automations, Skills, Prompts, Knowledge UI/RAG tools, web tools, model selection, Terminal integration, image generation, audio UX, ratings, and admin configuration.
- Its existing SQLite application database/volume remains in place.
- Current native pgvector, embedding, reranking, oikb, Open Terminal, and Gemini Image configurations remain external integrations rather than being reimplemented.

### 23.2 Thin Open WebUI adapter pack

Install a small reviewed set of supported extensions, not a source patch:

1. Context Filter
   - On each user turn, sends user/chat/message identity plus the bounded current request to `assistant-core`.
   - Receives a compact personalization/memory/episode context block and injects it before inference.
   - Does not duplicate native current-chat, Knowledge, Files, or web tool behavior.

2. Lifecycle Event Function
   - Publishes completed-turn, message update/delete, chat delete, fork/import, and user-delete events to `assistant-core`.
   - Delivery is idempotent; the chat response does not wait for background extraction/indexing.

3. Assistant Core Tool
   - Exposes explicit memory search/correction, semantic conversation recall, capability discovery/execution, library promotion, artifact/media actions, and status operations.
   - Keeps secrets and service credentials outside model-visible schemas.

4. Status/Action UI adapters
   - Emit native status events and Rich UI/artifact cards.
   - Provide user-triggered controls such as Add to Library, Edit, Versions, Retry, or remove/correct memory.

The adapter pack should be mostly stateless. Durable truth belongs in `assistant-core`, Postgres, Garage, Git/Obsidian, or native Open WebUI stores according to the authority rules.

### 23.3 Modular `assistant-core`

One deployable API with focused internal modules:

- Identity and policy: maps Open WebUI users/chats/messages, enforces ownership and action policy.
- Memory ledger: confirmed/inferred memories, personalization, confidence, evidence, correction, decay, and provenance deletion.
- Conversation index: canonical segment references, hybrid transcript retrieval, topic episodes, neighboring-message expansion, and fork-safe garbage collection.
- Context planner: protected-core budget, request classification, memory/episode retrieval, precedence, packing, and source metadata.
- Capability registry/gateway: normalized tool descriptions, embeddings, compatibility/authorization filtering, semantic selection, execution proxying, and chat-scoped promotion.
- Content catalog: hashes, canonical objects, aliases/references, derivative cache keys, durable-library membership, and garbage collection.
- Artifact coordinator: version chains, Office/PDF generation/edit workflows, OnlyOffice sessions/callbacks, preview/extraction refresh, and artifact cards.
- Media coordinator: Gemini-native audio/video/YouTube processing, timestamps/transcripts, durable indexing, and provider job state.
- Operations: job state, retries, reconciliation, health, audit records, and metrics.

Each module exposes a narrow internal interface and owns its tables. They share one process/deployment initially but do not read each other's tables directly; coordination happens through service methods and durable internal events.

### 23.4 Persistence and existing companions

- Supabase PostgreSQL: separate assistant-core schemas/tables for metadata, provenance, references, jobs, and module state.
- Existing pgvector: keep Open WebUI's document vectors isolated; assistant-core uses separate collections/tables for memory, episodes, transcript segments, and capability descriptions.
- Garage:
  - `openwebui-uploads` remains native upload storage.
  - `assistant-content` holds canonical durable binaries and derivatives.
- Open WebUI SQLite: remains authoritative only for native application objects such as chats, native files, Notes, Skills, settings, and workspace state.
- GitHub/Obsidian: canonical vault source; oikb remains the synchronizer into native Knowledge.
- OnlyOffice: human Office editor, reached through the artifact coordinator.
- Open Terminal: execution environment, not the canonical artifact store.
- LiteLLM/OpenRouter/Gemini and other APIs: provider layer, selected through explicit capability metadata rather than hidden autonomous routing.

### 23.5 Background work without premature infrastructure

- Use a Postgres-backed job/outbox table and one worker process initially.
- Completed-turn extraction, episode creation, embeddings, content processing, media jobs, derivative refresh, and garbage collection run asynchronously.
- Do not add Valkey/Redis or a separate message broker merely for architectural fashion. Add one only if measured concurrency, multi-replica coordination, or delivery latency requires it.
- Job handlers are idempotent and keyed by event/content/processing identity so retries do not duplicate state.

### 23.6 Failure boundaries

- If assistant-core retrieval is temporarily unavailable, ordinary Open WebUI chat should fail open using native current context/tools, while showing a small degraded-memory status.
- Writes/actions must not claim success when assistant-core or a downstream service fails.
- Background event delivery uses an outbox/retry path; missed events are caught by reconciliation.
- A failed extraction/media/artifact job preserves the canonical source and prior valid version.
- Retrieval/index stores are disposable derivatives; canonical transcripts, Git sources, Postgres ledger records, and Garage binaries remain rebuildable sources of truth.

Next design section after approval: detailed request/response and background-event data flows.

## 24. Phased architecture design — section 2: data flows

Approved by the user: choice A.

### 24.1 Normal conversational turn

1. The user submits a message through normal Open WebUI chat, optionally with native attachments and per-chat tools/skills.
2. The thin Context Filter sends only a bounded request envelope to `assistant-core`:
   - user/chat/message IDs;
   - current user text;
   - small recent-turn/task hints already available to the Filter;
   - model ID/capability metadata;
   - attached native file IDs/metadata, not redundant binary copies;
   - active folder/project and promoted-capability IDs when present.
3. The `context planner` classifies the request and concurrently queries only relevant assistant-owned layers:
   - confirmed/inferred personal memory;
   - current-project/topic episodes;
   - bounded semantic transcript segments when recall signals justify it;
   - durable content/artifact metadata when an entity is referenced;
   - existing chat-scoped capability bindings.
4. It resolves conflicts through authority/recency/confidence rules and returns a compact, source-labeled context block within its elastic budget.
5. The Filter injects that block. Open WebUI then continues its ordinary inference path and remains responsible for:
   - current conversation messages and native compaction state;
   - current-chat file handling;
   - attached Knowledge/RAG and built-in query/grep/view tools;
   - native web search/fetch;
   - Skills, native tools, Terminal, image generation, tasks, Notes, Calendar, and Automations.
6. The model answers normally and may call the Assistant Core Tool for explicit memory/recall/library/artifact/media/capability operations.
7. Status events show only meaningful work such as `recalling an earlier discussion`, `processing document`, or `creating artifact`; the final response stays clean.

Latency/failure rule:

- The context call has a short timeout and a bounded payload/response.
- If it times out or fails, the Filter records degraded status and lets native Open WebUI continue rather than blocking ordinary chat.
- A context failure cannot be treated as successful completion of a requested write/action.

### 24.2 Memory extraction, correction, and recall

After a completed assistant turn:

1. The Lifecycle Event Function emits a stable completed-turn event containing the new user/assistant messages and their IDs—not the entire chat.
2. The event is accepted into the Postgres outbox/job table and acknowledged quickly.
3. A cheap task model extracts zero or more candidates:
   - explicit facts/preferences/instructions;
   - inferred style or interaction preferences;
   - project context, decisions, commitments, and open loops;
   - evidence references and confidence;
   - correction/supersession signals.
4. Deterministic validation rejects empty, unsupported, duplicate, overly transient, or secret-like candidates according to policy.
5. Explicit statements/corrections can create or update confirmed records immediately. Inferences remain labeled as inferred with evidence and confidence.
6. About every ten turns or during low-priority maintenance, broader deduplication, contradiction resolution, confidence recalculation, and decay run in the background.
7. The next relevant request retrieves a small ranked subset by semantic relevance, path/topic, authority, recency, confidence, and project scope.

Native Memory mirror:

- Mirroring is asynchronous and optional.
- Failed mirroring never rolls back or weakens the authoritative ledger.
- Retrieval never trusts a divergent native copy over the ledger.

### 24.3 Conversation indexing, compaction, and live recall

1. Every completed user/assistant turn is normalized into bounded canonical transcript segments.
2. Exact duplicate segment text may reuse an embedding, while chat/message reference rows remain independent.
3. Topic boundaries incrementally create or update episode records containing decisions, durable context, artifacts, source message IDs, and open loops.
4. Native Open WebUI rolling compaction remains the only normal active-chat compactor at 300k.
5. The transcript index remains independent of what native compaction sends to the model; the uncompacted Open WebUI transcript remains canonical.
6. Recall uses progressive scope:
   - current chat;
   - current folder/project;
   - all chats for that user.
7. Hybrid retrieval combines exact/full-text, semantic similarity, recency/current-scope weighting, and reranking, returning roughly 3-6 passages plus necessary neighbors.
8. The model can request explicit recall through a tool, while the Context Filter may retrieve proactively for phrases such as `earlier`, `we decided`, `you already asked`, or when relevant context is clearly missing.
9. Never inject an entire huge historical chat merely to resolve one reference.

### 24.4 Capability discovery and execution

Keep a native broad core of roughly 15-25 lightweight capabilities attached: memory/recall, Files/Knowledge, web, Notes/tasks/time/calculation, content/artifact access, and basic read-only Terminal access.

Specialized capability path:

1. The model calls `find_capabilities(intent, constraints)` on the always-attached gateway.
2. The registry filters by user access, availability, model/tool compatibility, risk class, and current environment.
3. Semantic search/reranking returns a small set of normalized capability descriptions with IDs, required arguments, cost/risk hints, and whether confirmation is needed.
4. The model calls a generic validated `run_capability(capability_id, arguments)` operation.
5. The gateway validates arguments against the stored native schema and executes the underlying OpenAPI/MCP/native adapter.
6. A successfully used specialized capability becomes bound/prioritized for that chat inside the gateway. It need not mutate Open WebUI's native checkbox UI.
7. Status/Rich UI shows the active capability and offers an unbind control.

Important safety rule:

- Dynamic discovery cannot bypass the selected action policy. A discovered capability still requires confirmation for communications, purchases, permission changes, destructive operations, or consequential writes outside assistant-owned stores.

### 24.5 Files, durable promotion, and deduplication

Explicit `Add to Library` path:

1. The adapter streams the input to the content backbone, which calculates SHA-256 before expensive processing.
2. If compatible canonical content/derivatives already exist, create only the new user/chat/KB alias/reference and return immediately.
3. Otherwise write an immutable canonical object to `assistant-content`, create metadata, then enqueue extraction/OCR, preview, chunking, embedding, and classification.
4. Place the durable item in the searchable inbox with visible/reversible classification suggestions.

Normal paperclip path:

1. Open WebUI uploads and processes normally into `openwebui-uploads`; do not intercept the request through a source patch.
2. The completed-turn/background worker observes attached file IDs, reads native metadata/binary through supported APIs/storage credentials, and computes canonical identity.
3. It links to an existing canonical item or promotes a new one.
4. It cleans a redundant native binary/record only when chat/file references can remain valid; otherwise temporary duplication is tolerated and surfaced to reconciliation.

Processing identity is the content hash plus extractor/model/options/chunking/embedding versions, so a new processing version creates only missing derivatives rather than another canonical binary.

### 24.6 Artifacts and Office editing

Creation:

1. The model calls a typed artifact operation such as `create_document`, `create_workbook`, `create_presentation`, or `create_pdf`.
2. The artifact coordinator chooses a format workflow/template, executes generation through a reviewed library/worker, renders a preview, validates that the file opens, and stores version 1 in Garage.
3. Extraction and indexing run, and a persistent native Rich UI artifact card appears in chat.

AI revision:

1. The model identifies the artifact/version and requests a typed structural change.
2. The coordinator edits a copy, validates/renders it, then atomically records a new immutable version and advances the current pointer.
3. If any step fails, the previous version stays current and intact.

Human revision:

1. `Edit` requests a short-lived signed OnlyOffice configuration and protected download URL.
2. OnlyOffice opens in a separate tab.
3. The verified callback retrieves only from the configured OnlyOffice origin, enforces owner/version/size rules, writes a new immutable object, updates the version transactionally, and refreshes derivatives.
4. Old versions remain downloadable and searchable through version metadata.

### 24.7 Audio, video, and YouTube

1. The tool normalizes URL/media identity and checks canonical/dedup state.
2. Native audio UX/STT remains available for dictation and simple transcription.
3. Rich understanding uses Gemini's native media/Files/YouTube interface so the model receives original audio/visual streams rather than only an OpenAI-compatible transcription.
4. Persist source metadata, transcript, timestamped observations, thumbnails where useful, model/version information, and embeddings as derivatives of the durable media item.
5. Later questions retrieve bounded timestamped passages and may re-query the native media provider only when the stored derivatives are insufficient.

### 24.8 Deletion, forks, and reconciliation

1. A native message/chat delete event immediately tombstones its assistant-core references so retrieval cannot return them.
2. Remove transcript, episode, and inferred-memory evidence owned only by those references.
3. Confirmed memory and independently saved canonical artifacts/documents follow their selected independent-retention rules.
4. A fork owns separate reference rows, so deleting its parent does not delete shared canonical segments still referenced by the fork.
5. When the final reference disappears, enqueue physical vector/object/derivative garbage collection.
6. Duplicate/out-of-order events are safe through idempotency keys and state checks.
7. Periodic reconciliation compares live native chat/message/file IDs with assistant-core references and repairs missed events, stale tombstones, and orphans.

### 24.9 Observability without chat clutter

- A request correlation ID follows the Filter, assistant-core, workers, provider calls, and emitted UI events.
- Native OpenTelemetry/logging records detailed timings/errors.
- Assistant-specific metrics include memory extraction/retrieval, transcript hits, tool discovery/execution, dedup reuse, job age/failure, deletion lag, orphan counts, compaction/degraded-context status, and artifact/media processing.
- User-visible status stays concise and collapsible; detailed diagnostics live outside the final model response.

Next design section after approval: security, permissions, data retention, and recovery behavior.

## 25. Phased architecture design — section 3: permissions, retention, and recovery

### 25.1 Trust model

- Optimize for one primary trusted user and at most one additional trusted user.
- Preserve owner/user IDs everywhere, but do not build hostile enterprise multi-tenancy or an approval bureaucracy.
- External configured AI providers may receive relevant content; minimize payloads but do not add a local-only classification tier for v1.
- Open WebUI remains internet-facing only through the user's existing protected deployment path; assistant-core, databases, Garage, OnlyOffice callbacks, workers, oikb, and Open Terminal should communicate on private/internal networks wherever possible.

### 25.2 Action classes

Class 0 — autonomous reads:

- Search memory/chats/KB/files/web.
- Read repositories and assistant-owned stores.
- Analyze, calculate, retrieve, inspect, and generate previews.
- No confirmation.

Class 1 — autonomous assistant-owned reversible writes:

- Add/correct/infer memory and personalization.
- Create/update Notes, topic episodes, indexes, classification metadata, tasks, and internal job state.
- Create artifacts, new artifact versions, durable-library entries, KB metadata, and routine oikb synchronization.
- Clean verified orphaned derivatives after the selected grace period.
- No confirmation, but every change is attributable, visible where useful, and reversible/versioned when practical.

Class 2 — reversible external/project writes:

- Git/Obsidian changes, file edits in an explicitly selected working directory, calendar edits, and other scoped writes that retain history or have an obvious undo.
- May run without repeated confirmation when the user explicitly enabled the capability for that chat/project and the operation remains inside that scope.
- Show a concise action/status record.

Class 3 — consequential or externally communicative actions:

- Sending messages/email/forms to other people.
- Purchases, bookings, payments, account/permission/security changes.
- Public publishing/deployment, destructive operations, bulk deletion, irreversible overwrite, or writes outside the selected assistant/project scope.
- Require explicit confirmation immediately before execution with exact target and effect.

Dynamic tool discovery cannot downgrade a capability's risk class. The gateway enforces policy independently of the model's wording.

### 25.3 Identity and service authorization

- Every assistant-core request carries user/chat/message IDs plus a service-authenticated assertion from the trusted Open WebUI adapter; do not trust arbitrary public `X-User-Id` headers.
- Assistant-core maps external/native object IDs to stable internal owner references.
- Separate credentials and least-required rights for:
  - Open WebUI native Garage bucket;
  - canonical assistant-content bucket;
  - assistant-core Postgres schemas;
  - vector collections;
  - OnlyOffice callbacks/downloads;
  - GitHub/oikb/provider APIs.
- Secrets stay in Dokploy/environment/secret storage or server-side valves, never in model-visible prompts, tool results, object metadata, or signed URLs beyond their short validity.
- Regular users cannot install arbitrary Tools/Functions/MCP servers. Review all imported community code because Open WebUI extensions execute server-side code.
- SSO is optional and deferred; native accounts/RBAC are sufficient for the trusted-user scope.

### 25.4 Content and prompt-injection boundaries

- Treat web pages, uploaded documents, emails, retrieved chats, and tool output as untrusted data, never as higher-priority instructions.
- Retrieved content cannot authorize a tool, change confirmation policy, reveal secrets, or alter source precedence.
- External URLs and callbacks use allowlists/SSRF protections, strict timeouts, size limits, redirect rules, and MIME/content validation.
- OnlyOffice uses short-lived signed configs/downloads, verifies origin/owner/version, refuses unexpected redirects, and writes through an atomic new-version transaction.
- NEURA Browser is optional and uncoupled; its agent mode must remain manually enabled and its own confirmation/origin boundaries remain in force.
- Open Terminal may be permissive for the trusted-user deployment, but filesystem mounts define its real authority. Do not mount secrets or unrelated host roots merely for convenience.

### 25.5 Retention by data class

Native chats:

- Open WebUI retains full chats until the user deletes them according to native behavior.
- Compaction never deletes the stored transcript.

Transcript index and episodes:

- Mirror the lifetime of their chat/message references.
- Logical deletion/tombstoning removes them from retrieval immediately.
- A surviving fork or another source keeps shared canonical segments alive through its own reference rows.

Memory:

- Explicit confirmed memories persist until corrected, superseded, or explicitly deleted, even if an originating chat is later removed, according to the already selected provenance policy.
- Inferred memories supported only by deleted evidence are removed.
- Inferred memories may decay/become inactive when stale; inactive records remain auditable until maintenance purges them.
- Corrections preserve supersession/audit metadata without injecting obsolete values.

Durable documents/media/artifacts:

- Explicitly promoted library items and saved artifacts are independent user-owned objects and survive deletion of the chat that created/referenced them.
- Versions remain until the user deletes the artifact/version chain or an explicit retention policy is later chosen.
- Exact canonical objects are physically eligible for deletion only when no live user/chat/KB/artifact/version reference remains.

Temporary/native uploads:

- Chat-local files follow the native chat/file lifecycle plus reconciliation.
- Redundant processing copies and orphaned derivatives are eligible for garbage collection after references are proven absent.
- Do not automatically treat a screenshot/tiny temporary attachment as durable.

Operational data:

- Job/audit/telemetry detail can be rolled up or expired independently; retain enough recent detail for debugging without making logs another permanent copy of user content.
- Avoid logging full prompts, binaries, secrets, signed URLs, or document contents by default.

### 25.6 Deletion behavior

1. Tombstone deleted content immediately so no retrieval path can return it.
2. Remove reference rows and recompute memory provenance/confidence transactionally or through an idempotent job.
3. Preserve independently referenced fork segments, confirmed memories under the selected rule, and explicitly durable library/artifact objects.
4. Queue physical vector/object/derivative deletion only after reference count and current native state are verified.
5. Record a compact audit event containing IDs/reason/result, not deleted content.
6. Periodic reconciliation detects missed native events and finishes orphan cleanup.

Physical purge timing selected: 24-hour grace.

- Logical deletion/tombstoning still removes content from every retrieval path immediately.
- After 24 hours, a garbage-collection job re-verifies that no live chat/fork/user/KB/artifact/version reference exists, then deletes orphaned vectors, derivatives, and canonical objects.
- A repeated delete or a restored reference during the grace period is handled idempotently and cancels physical deletion when the object is live again.
- This is a safety buffer for accidental or incorrect lifecycle events, not a user-visible recycle bin or long-term retention tier.

### 25.7 Backup and recovery

Canonical backup set:

- Open WebUI persistent volume/SQLite and configuration export.
- Supabase/Postgres assistant-core schemas plus existing vector metadata as required.
- Both Garage buckets and Garage cluster metadata/configuration.
- Git/Obsidian repositories through their existing remote history.
- OnlyOffice itself is replaceable; artifact binaries and metadata are not.
- Dokploy Compose/configuration and secret references, stored securely rather than copied into the design repo.

Recovery principles:

- Canonical data is backed up; derived embeddings, previews, transcripts, chunks, capability vectors, and indexes are rebuildable.
- Restore order: infrastructure/secrets -> Open WebUI/native DB -> assistant-core Postgres -> Garage objects -> workers/reconciliation -> rebuild disposable indexes.
- Validate referential consistency after restore before enabling garbage collection.
- Perform an occasional small restore drill rather than assuming backup jobs imply recoverability.

### 25.8 Upgrade and outage behavior

- Prefer a pinned Open WebUI release tag or immutable digest for the production instance rather than unknowingly changing `main`; upgrades remain deliberate and rollbackable.
- Before a meaningful upgrade, snapshot native state and assistant-core metadata and note the previous image digest/config.
- After it, run the lightweight maintenance smoke checks selected in section 19.20.
- Assistant-core retrieval outage: native chat continues with degraded-memory status.
- Postgres outage: reads may use safe short-lived caches, but authoritative writes pause and report failure.
- Garage outage: existing metadata remains readable, but binary creation/edit/download reports unavailable and never claims completion.
- Provider outage: use an explicitly compatible fallback only where configured; otherwise surface the provider failure without silently changing semantics.
- Worker outage: accepted jobs remain queued and visible; retry on recovery.
- Reconciliation is the repair path for missed events, interrupted promotion, partial derivative processing, and restore drift.

Next design section after the purge-timing decision and approval: phased build sequence and acceptance criteria for each phase.

## 26. Phased architecture design — section 4: incremental build sequence

Recommended approach: continuity-first. Solve the user's original memory/recall problem before dynamic tools, content lifecycle, Office artifacts, and rich media. Every phase should leave a useful working system and should not require later phases to be deployed.

### Phase 0 — native baseline and exact deployed-state audit

Goal: establish which current-main/native features actually exist and work in this instance before adding code.

- Record the running Open WebUI image digest and effective persistent ConfigVars.
- Verify native function calling and required builtin categories for Files, Knowledge, Chat History, Skills, Notes, Tasks, Calendar, Automations, Memory tools if temporarily inspected, web, time/calculation, and Open Terminal.
- Configure/verify universal 300k compaction threshold/cap and dedicated non-thinking compaction model.
- Confirm native `search_chats`, `view_chat`, file/KB query/grep/view, Notes, task, and automation operations.
- Enable native OpenTelemetry/structured logs at a modest level.
- Connect/test Garage for Open WebUI uploads with tagging disabled and local processing cache retained initially.
- Keep native Memory disabled or clearly non-authoritative during the custom-memory build to avoid confusing test results.
- Install stock NEURA Browser only if the user wants it immediately; it remains optional and outside the build dependency graph.

Completion check:

- Normal chat, attachments/RAG, web, Terminal, image generation, compaction, native history search, and Garage upload/download work after restart with runtime settings documented.

### Phase 1 — assistant-core foundation and thin adapters

Goal: deploy the modular companion skeleton without changing assistant behavior yet.

- Create one assistant-core API and one worker deployment.
- Add identity/service authentication, module boundaries, Postgres schemas, migrations, outbox/jobs, idempotency, health endpoints, correlation IDs, and metrics.
- Install the thin Context Filter, Lifecycle Event Function, Assistant Core Tool, and minimal status adapter.
- Context call returns empty context initially; completed-turn events are accepted/observed but do not create memories yet.
- Implement fail-open read behavior and truthful write failure behavior.

Completion check:

- A chat turn receives/propagates stable user/chat/message IDs, queues one idempotent completed-turn event, shows no prompt pollution, and still works normally when assistant-core is intentionally stopped.

### Phase 2 — authoritative personal memory and personalization

Goal: replace reliance on useless stock Memory with the first genuinely useful continuity layer.

- Implement confirmed/inferred ledger records, paths/topics, evidence, confidence, supersession, correction, decay state, and user visibility/search/delete APIs.
- Run cheap-model extraction after every completed turn with deterministic validation and deduplication.
- Implement relevant memory retrieval and compact context injection.
- Apply current-instruction-over-memory precedence.
- Add optional native Memory mirroring only after the external ledger works; skip/disable it if it creates ambiguity.

Completion check:

- State a preference in one chat, retrieve it naturally in another, correct it, and see only the corrected value used. An inferred style preference is labeled/evidenced, and deleting its sole source removes it from retrieval while explicit confirmed memory follows its retention rule.

### Phase 3 — semantic conversation continuity and compaction recovery

Goal: recover relevant past discussion without loading huge chats or depending on compaction summaries alone.

- Index bounded message segments with exact-content reuse and independent chat/fork references.
- Add incremental topic episodes, neighbor links, hybrid retrieval/reranking, progressive scope, and source message links.
- Integrate proactive recall signals and an explicit recall tool.
- Implement message/chat deletion handlers, tombstones, fork-safe reference counting, 24-hour physical GC, and reconciliation.
- Add the compact native status/control card for context/recall observability if the deployed UI still needs it.

Completion check:

- Recover a known compacted/old-chat detail through a bounded cited passage. Fork a test chat, delete the parent, verify parent-only retrieval disappears immediately while the fork still recalls shared history; verify final orphan purge after the grace path in an accelerated test.

### Phase 4 — semantic capability gateway

Goal: make a large tool catalog usable without manually attaching every heavy schema.

- Inventory and normalize native, OpenAPI, MCP, oikb, GitHub, Terminal, and later custom capabilities.
- Keep the selected 15-25 lightweight core capabilities attached.
- Implement semantic discovery, compatibility/access/risk filtering, schema validation, generic execution, chat-scoped promotion, unbind/status UI, and audit records.
- Start with read-heavy tools; enable consequential actions only after confirmation policy is verified.

Completion check:

- Representative requests discover and execute the correct specialized capability, reuse it later in the same chat, reject an unauthorized/incompatible capability, and require confirmation for a deliberately consequential test action.

### Phase 5 — canonical content library and global deduplication

Goal: make durable files/media independent of chat history and stop repeated processing/storage.

- Create the `assistant-content` Garage bucket/key and content/reference/derivative schemas.
- Implement Add to Library pre-hash flow, durable inbox, classification metadata, native file references, and processing cache keys.
- Add normal-paperclip post-upload promotion/reconciliation without intercepting native upload.
- Implement exact dedup, reference-counted deletion, 24-hour GC, and orphan reconciliation.
- Benchmark Xberg against the current extraction backend using the user's actual PDFs, tables, scans, Office files, and ARM resource constraints; adopt only on evidence.

Completion check:

- Upload the same file through library and paperclip paths and converge to one canonical durable binary/compatible derivative set with separate references. Delete one reference without losing the other; delete the last and verify scheduled cleanup.

### Phase 6 — durable document/artifact workspace

Goal: create and revise real Office/PDF artifacts rather than producing throwaway download links.

- Audit/reuse suitable NEURA generator components and compare other structural libraries/CLIs before building format engines.
- Implement artifact metadata/version chains, templates/styles, DOCX/XLSX/PPTX generation, Typst/LaTeX PDF paths, preview rendering, validation, and extraction/index refresh.
- Add persistent chat artifact cards and typed AI revision operations.
- Integrate hardened OnlyOffice launch/download/callback based on the audited Odysseus protocol, fixing identified token/version/old-binary gaps.

Completion check:

- Create a representative document and workbook, edit each through AI and OnlyOffice, preserve/open/download older versions, and confirm failed edits leave the previous current version intact.

### Phase 7 — durable Gemini-native media understanding

Goal: understand audio/video/YouTube beyond plain transcription and reuse the result across chats.

- Implement normalized media/YouTube identity, deduplication, Gemini native Files/YouTube processing, transcripts, timestamped audio/visual observations, thumbnails, embeddings, and durable references.
- Keep native STT/TTS for voice UX and optional Whisper fallback.
- Add bounded timestamp retrieval and re-query original media only when stored derivatives are insufficient.

Completion check:

- Ask about speech plus a visual/non-speech event in a test video, receive timestamped evidence, attach the same media again without repeating compatible processing, and recall it from a later chat.

### Phase 8 — optional polish only after observed need

- Evaluate native Rich UI/artifacts versus Inline Visualizer; install only if it materially improves real output.
- Consider community Prune only as a previewable native-debris maintenance aid after code/security review and backups.
- Consider Open WebUI Computer if whole-machine persistent workspaces become valuable beyond Open Terminal.
- Revisit NEURA Browser customization, music generation, local GTX 1050 Ti workloads, SSO, Valkey/message broker, or service splitting only from actual need/measurements.

### Cross-phase implementation rules

- No Open WebUI source modification unless a later proven blocker has no supported extension path and the user separately approves a fork.
- Verify native support again immediately before implementing each capability because the user tracks a fast-moving upstream branch.
- Keep schema/data migrations reversible and canonical data backed up before lifecycle changes.
- Use lightweight maintenance checks, not formal release machinery.
- Do not implement a later phase merely because it appears in this design; stop when the current system is already sufficiently useful.

Next after approval: consolidate the approved sections into the formal architecture specification, self-review it for contradictions/overbuilding, and then prepare an implementation plan one phase at a time.

Still do not implement until the phased design is presented and approved.
