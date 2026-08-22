# Open WebUI Topic Episodes & Hierarchical Conversation Recall Design

**Date:** 2026-08-22  
**Status:** Approved  
**Target Phase:** Phase 3 — Conversation Continuity Completion  

---

## 1. Purpose & Overview

While turn-level conversation chunking (`conversation-v1`) captures granular dialogue snippets, multi-turn problem-solving sessions require higher-level semantic synthesis. 

This design introduces **Topic Episodes**: high-density structured summaries (decisions made, architecture choices, open loops, mentioned entities) compiled asynchronously after **3 hours of conversation inactivity** (or on-demand). 

These episodes participate in a **unified 3-tier hierarchical search** (Topic Episodes, Turn Passages, Document Chunks) via hybrid PostgreSQL full-text search (`tsvector`) and 1536d cosine vectors (`pgvector`).

---

## 2. Core Principles & Constraints

1. **Non-Blocking & Asynchronous**: Episode compilation happens entirely in the background worker via LiteLLM task models. Chat flow is completely unaffected.
2. **Adaptive Inactivity Threshold**: Automatic compilation triggers after 3 hours of inactivity per chat. On-demand compilation is available via Admin UI and Tools.
3. **Adaptive Multi-Topic Splitting**: If a long session spans multiple distinct topics, the task model adaptively decomposes it into multiple bounded Topic Episodes rather than forcing an overly broad single summary.
4. **Structured Decision & Open-Loop Retention**: Each episode explicitly extracts architectural decisions, key entities, and unresolved tasks/open loops.
5. **Unified Multi-Tier Retrieval**: Search simultaneously evaluates Topic Episodes, Conversation Passages, and Files, fusing matches via Reciprocal Rank Fusion (RRF) with project/folder scoping boosts.
6. **Fast Tombstoning**: Deleting a chat immediately tombstones its associated topic episodes (`tombstoned_at = now()`), excluding them from search.

---

## 3. Architecture & Data Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        3-Hour Inactivity Trigger                       │
│    (Worker periodic sweep OR on-demand dashboard / tool trigger)       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Uncompiled turns for idle chat
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                      Episode Extraction Engine                         │
│   Task Model (LiteLLM) adaptively splits session into Topic Episodes   │
│   • Title & Category                                                   │
│   • High-Density Summary                                               │
│   • Decisions Made                                                     │
│   • Open Loops (Pending tasks/follow-ups)                              │
│   • Key Entities / Technologies                                        │
│   • Source Turn / Message ID Range                                     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
┌─────────────────────────────────────┐           ┌──────────────────────┐
│        PostgreSQL Table             │           │  1536d Cosine Vector │
│      (assistant_core.topic_episode) │           │      (pgvector)      │
│      + Full-Text Search tsvector    │           │ (summary + decisions)│
└─────────────────────────────────────┘           └──────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               Unified Hierarchical Retrieval Engine                    │
│   /v1/personal-context/search (Hybrid RRF across 3 tiers):             │
│   1. 🧠 Topic Episodes  (Macro context, decisions, open loops)         │
│   2. 💬 Conversation Segments (Micro dialogue passages)                │
│   3. 📄 Document Segments     (Uploaded files / Markdown chunks)       │
│                                                                        │
│   /v1/personal-context/read:                                           │
│   Resolves episode UUID to structured summary, decisions, & turns.     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Component Specifications

### 4.1 Database Model (`assistant_core.topic_episode`)
Migration `0017_topic_episodes.py`:
- `id`: `UUID` primary key
- `user_id`: `String(64)` indexed
- `native_chat_id`: `String(128)` indexed
- `project_id`, `folder_id`: `String(128)` nullable (inherited from turns for 3-tier scoping)
- `title`: `String(256)`
- `topic_category`: `String(64)` (e.g., `architecture`, `debugging`, `configuration`, `research`)
- `summary`: `Text`
- `decisions_made`: `JSONB` array of strings
- `open_loops`: `JSONB` array of strings
- `key_entities`: `JSONB` array of strings
- `start_message_id`, `end_message_id`: `String(128)`
- `turn_count`: `Integer`
- `embedding`: `Vector(1536)` (HNSW cosine index)
- `search_vector`: `TSVECTOR` (GIN index over title, category, summary, decisions, entities)
- `tombstoned_at`: `DateTime(timezone=True)` nullable
- `created_at`, `updated_at`: `DateTime(timezone=True)`

### 4.2 Extraction Engine (`src/assistant_core/episodes/`)
- `extractor.py`: Consumes uncompiled turns, constructs task model prompt with JSON schema output, parses response into `TopicEpisodeExtraction` models.
- `models.py`, `schemas.py`, `repository.py`: CRUD, FTS + pgvector hybrid query, project-scoped search, and tombstones.

### 4.3 Background Worker Integration
- Inactivity sweep checks for chats where `max(created_at) <= NOW() - 3 hours` and unprocessed turns exist.
- Enqueues `compile_topic_episodes` jobs.
- Idempotently processes turns and records episodes.

### 4.4 Search & Personal Context Integration
- Updated `/v1/personal-context/search` queries `topic_episode` in addition to conversation & file passages.
- Updated `/v1/personal-context/read` resolves `topic_episode` UUIDs into full formatted episode blocks.
- Search playground in dashboard updated to display `[Topic Episode]` result cards with RRF scores.

### 4.5 Admin UI Dashboard
- New **Episodes** tab in web dashboard displaying compiled topic episodes with search, filter, and detail modal.
- **`⚡ Compile Episodes`** button for on-demand processing.
