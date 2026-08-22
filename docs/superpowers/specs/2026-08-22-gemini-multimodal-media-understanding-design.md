# Gemini-Native Multimodal Media Understanding Design

**Date:** 2026-08-22  
**Status:** Approved  
**Target Phase:** Phase 7 — Media Understanding  

---

## 1. Purpose & Overview

Provide deep, durable multimodal understanding of YouTube videos and uploaded audio/video files using Gemini's native multimodal capabilities (via LiteLLM / Gemini API) rather than plain speech-to-text.

The system extracts narrative overviews, key takeaways, and timestamped intervals capturing both **spoken dialogue** and **visual observations** (slides, code on screen, terminal output, diagrams), storing them as searchable pgvector segments for time-bounded recall.

---

## 2. Architecture & Data Flow

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Media Ingestion Trigger                         │
│  • YouTube URL detected in chat prompt (https://youtube.com/watch?...) │
│  • Audio/Video file uploaded in chat (.mp3, .mp4, .wav, .webm, .m4a)   │
│  • On-demand tool / dashboard action (process_media_url)               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Enqueues Job(kind="index_media")
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   Gemini Multimodal Analysis Engine                    │
│   Calls Gemini 2.0 / Flash via LiteLLM with native media payload       │
│   • Video/Audio Title & High-Level Narrative Overview                  │
│   • Core Takeaways, Commands, Code & Key Learnings                     │
│   • Timestamped Intervals (04:15 - 08:30) with:                        │
│     - Speech Transcript & Key Quotes                                   │
│     - Visual Observations (Diagrams, slides, terminal output on screen)│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
           ┌────────────────────────┴────────────────────────┐
           ▼                                                 ▼
┌─────────────────────────────────────┐           ┌──────────────────────┐
│        PostgreSQL Tables            │           │  1536d Cosine Vector │
│   assistant_core.media_document     │           │      (pgvector)      │
│   assistant_core.media_segment      │           │ (timestamps + visual)│
│   + Full-Text Search tsvector (GIN) │           │                      │
└─────────────────────────────────────┘           └──────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               Unified Hierarchical Retrieval Engine                    │
│   /v1/personal-context/search:                                         │
│   Fuses Media Segments with Episodes, Turns, Files & Memories via RRF  │
│                                                                        │
│   /v1/personal-context/read:                                           │
│   Returns timestamped transcript + visual observations + YouTube jump  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Database Schema (`assistant_core.media_document` & `assistant_core.media_segment`)

### 3.1 `assistant_core.media_document`
- `id`: `UUID` primary key
- `user_id`: `UUID` indexed
- `source_type`: `String(32)` (`youtube`, `audio_file`, `video_file`, `web_media`)
- `source_url_or_id`: `String(500)`
- `title`: `String(256)`
- `duration_seconds`: `Integer` nullable
- `overview`: `Text`
- `key_takeaways`: `JSONB` array of strings
- `metadata`: `JSONB` dictionary
- `content_sha256`: `String(64)` nullable
- `tombstoned_at`: `DateTime(timezone=True)` nullable
- `created_at`, `updated_at`: `DateTime(timezone=True)`

### 3.2 `assistant_core.media_segment`
- `id`: `UUID` primary key
- `media_document_id`: `UUID` foreign key to `media_document` (CASCADE)
- `user_id`: `UUID` indexed
- `segment_ordinal`: `Integer`
- `start_seconds`: `Integer`
- `end_seconds`: `Integer`
- `timestamp_label`: `String(64)`
- `headline`: `String(256)`
- `content`: `Text`
- `embedding`: `Vector(1536)` (HNSW cosine)
- `search_vector`: `TSVECTOR` (GIN)
- `created_at`: `DateTime(timezone=True)`
