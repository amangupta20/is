# Assistant Core Operations

This runbook packages the optional Open WebUI companion for a later deployment. The
workstation has no Docker runtime. Slice 1F2A changes the deployment contract to build
from Git, but Docker build, Oracle ARM64 runtime, Dokploy rollout, adapter import, and
live smoke verification remain **pending**.

Ordinary Open WebUI chat remains usable when the optional companion is disabled or
unavailable. Context enrichment and lifecycle forwarding are designed to fail open.

## Prerequisites and credential safety

- Rotate the assistant database password before the first deployment because it
  appeared in transient local tool output during earlier testing. Do not reuse it.
- Generate a new random HMAC value of at least 32 bytes. Store the database URL and
  HMAC value in Dokploy secrets (or ignored `deploy/.env.assistant` for local Compose),
  never in Git or an image layer.
- Use the already-verified Supavisor endpoint and `assistant_core` role. The current
  role and schema already exist; do not run the bootstrap script for this installation.
- Ensure the target is Oracle ARM64-compatible and an existing private Docker network
  shared with Open WebUI is available. A registry digest is not required for this
  Git-based Compose deployment.
- Rotate the database credential before any build, adapter import, or deployment work.

For a genuinely new installation only, an administrator may review and run
`deploy/postgres/bootstrap_assistant_role.sql` with `psql` variable
`assistant_password`. It creates or login-enables the role without granting access to
other schemas or tables. This script was not run in Slice 1F1.

## Git build contract (pending runtime verification)

Dokploy must be configured as **Docker Compose from Git** with these exact source
settings:

- Git repository: `https://github.com/amangupta20/is`
- Branch: `assistant-foundation`
- Compose path: `./deploy/compose.assistant.yml`

The `assistant-migrate` service owns the single Compose build with context
`../assistant-core` and `Dockerfile`; `pull_policy: build` makes each deployment build
from Dokploy's freshly cloned source. Migration, API, and worker use the same local
`ASSISTANT_IMAGE` tag, avoiding independent source builds. Docker build and Oracle
ARM64 runtime verification remain pending; no registry publication or amd64/arm64
manifest inspection was performed for Slice 1F2A.

## Dokploy and startup order

1. Create or select the private network shared with Open WebUI and set
   `ASSISTANT_NETWORK` to its exact name. Do not add a public host-port mapping.
2. Dokploy stores configuration in its generated .env file; configure the values shown in
   `deploy/.env.assistant.example` in Dokploy's environment UI. Compose explicitly
   interpolates each assistant runtime variable into all three containers, so do not
   commit or mount a `deploy/.env.assistant` service env file. For local use, copy the
   example to ignored `deploy/.env.assistant` and pass it with `--env-file`. Leave
   `ASSISTANT_OTLP_ENDPOINT` empty when no approved collector exists.
3. Invoke the deployment contract from the repository root exactly as follows when
   locally validating on a Docker-capable host:

   ```sh
   docker compose --env-file deploy/.env.assistant -f deploy/compose.assistant.yml config
   docker compose --env-file deploy/.env.assistant -f deploy/compose.assistant.yml up -d
   ```

4. Allow the one-shot `assistant-migrate` service to complete successfully. Only then
   start `assistant-core`; start `assistant-worker` after the same migration gate.
5. Keep port 8080 private. OpenWebUI reaches `http://assistant-core:8080` on the shared
   network.

No Compose invocation, Docker build, migration, database access, or Dokploy action
occurred for Slice 1F2A.

## Adapter Valve configuration and import order

Use the same newly generated HMAC value for the API's `ASSISTANT_HMAC_SECRET` and the
password-marked `hmac_secret` Valve in each adapter. Do not create a second HMAC
environment key. Set each `assistant_core_url` Valve to
`http://assistant-core:8080` and retain the bounded default timeouts unless measured
behavior justifies a change.

After migration and API health checks pass, import and enable adapters in this order:

1. `context_filter.py`, then verify ordinary chat and fail-open behavior.
2. `lifecycle_event.py`, then capture and validate native event shapes before relying
   on delivery.
3. `assistant_core_tool.py`, then call its redacted status action.

Adapter import and native event-shape capture remain pending Slice 1F2A.

## Internal verification

Run checks only from a trusted container on the private network:

- `GET /health/live` returns liveness without touching the database.
- `GET /health/ready` proves the database probe succeeds.
- signed `POST /v1/status` returns the existing redacted status contract.
- `GET /metrics/` is the canonical Prometheus mount path (`/metrics` redirects) and
  exposes process/Python runtime metrics only.

Never log request or response bodies, query strings, headers, adapter HMAC values,
database URLs, job payloads, stored content, or exception text. Correlate using the
bounded `x-correlation-id` response header and the fixed route template in the JSON
completion event. Real health, status, metrics, adapter, queue, and end-to-end checks
are pending Slice 1F2.

## Queue diagnosis

If work stops progressing, first keep the API available and disable the lifecycle
adapter to prevent new submissions. Check the worker process state and redacted
completion events, then check database connectivity with the readiness probe. Inspect
counts and safe error codes only; never print job payloads or stored content. Restart
the worker after the cause is understood. A broker is not part of this foundation.

## HMAC rotation

Dual-secret verification is not implemented. Use a brief adapter disable window:

1. Disable all three adapters.
2. Replace the API secret and restart `assistant-core` and `assistant-worker`.
3. Replace every adapter's password Valve with the same new secret.
4. Re-enable adapters in the documented order and perform the Slice 1F2 smoke checks.

## Admin Dashboard & Search Playground

The assistant core exposes an administrative web interface at `GET /` served from `/static/`:

- **Authentication**: Authenticate using the secret configured in `ASSISTANT_ADMIN_TOKEN` or `ASSISTANT_HMAC_SECRET`. A signed session cookie (`assistant_admin_session`) is established.
- **Overview & Telemetry**: Monitor active entity counts (memories, indexed markdown documents, conversation turns) and dead worker job counts in real time.
- **Search & Retrieval Playground**: Test hybrid search against indexed user data:
  - Select user ID and source filter (`all`, `memories`, `files`, `conversations`).
  - View live Reciprocal Rank Fusion (RRF) scores and pgvector cosine similarity rankings.
  - Preview simulated `<user_profile>` and `<retrieved_context>` prompt blocks with a 1-click clipboard copy.
- **Data Management & Batch Actions**:
  - Checkbox selection allows multi-item batch deletion across memories, documents, and conversation history.
  - **Double-Confirmation System Purge**: Open the Purge modal and type `PURGE` to drop tables or specific subsets during staging/test resets.
- **Memory Consolidation**:
  - Automatically scheduled once every 24 hours per user by the background worker (`consolidate:{user}:{YYYY-MM-DD}`).
  - To trigger an immediate consolidation run, navigate to the **Memories** tab, click **`⚡ Consolidate`**, and inspect the resolved conflicts.

## Rollback

Disable the adapters first so ordinary Open WebUI chat continues without the
companion. In Dokploy, select and redeploy the prior known-good Git commit, then verify
liveness/readiness before re-enabling adapters. Database migrations are never automatically downgraded: rollback has no automatic database downgrade; do not run a reverse migration automatically. Stop and review migration compatibility if the prior Git commit cannot use the current schema.

## Historical Chat Backfill (manual, one-shot)

Imported chats (ChatGPT/Gemini via Open WebUI's Import Chats) exist only in Open
WebUI until backfilled. Run this manually inside the running `assistant-core`
container whenever you want history to become searchable — it is never scheduled:

```bash
# preview counts without writing:
python -m assistant_core.scripts.backfill_chats \
  --open-webui-url http://open-webui:8080 \
  --token "<openwebui-api-key>" \
  --dry-run

# real run (idempotent; safe to rerun):
python -m assistant_core.scripts.backfill_chats \
  --open-webui-url http://open-webui:8080 \
  --token "<openwebui-api-key>"
```

Behavior: walks each chat's active branch, pairs user/assistant messages into
historical completed turns with original timestamps, and enqueues conversation
index jobs only (chunking, dedup, embeddings). Memory extraction is deliberately
skipped for imported history. Topic episodes compile later via the normal
3-hour inactivity sweep. Progress: dashboard Jobs tab; verify recall with the
search playground afterwards.
