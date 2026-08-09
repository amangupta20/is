# Assistant Core Operations

This runbook packages the optional Open WebUI companion for a later deployment. The
workstation has no Docker runtime, and no image was built, published, or deployed in
Slice 1F1. Docker build, amd64/arm64 manifest inspection, Dokploy rollout, native
event-shape capture, and the real end-to-end smoke test are **pending Slice 1F2**.

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
- Ensure an amd64/arm64-capable Buildx builder, registry, immutable image digest, and
  an existing private Docker network shared with Open WebUI are available for Slice 1F2.
- Rotate the database credential before any build, adapter import, or deployment work.

For a genuinely new installation only, an administrator may review and run
`deploy/postgres/bootstrap_assistant_role.sql` with `psql` variable
`assistant_password`. It creates or login-enables the role without granting access to
other schemas or tables. This script was not run in Slice 1F1.

## Build and publish (pending Slice 1F2)

Run these only on the approved multi-architecture builder after credential rotation:

```sh
docker buildx build --platform linux/amd64,linux/arm64 --tag REGISTRY/assistant-core:TAG --push assistant-core
docker buildx imagetools inspect REGISTRY/assistant-core:TAG
```

Record the resulting digest and set `ASSISTANT_IMAGE` to that immutable digest. The
build, ARM manifest verification, registry push, and container runtime smoke are
pending Slice 1F2; the commands above have not been executed here.

## Dokploy and startup order

1. Create or select the private network shared with Open WebUI and set
   `ASSISTANT_NETWORK` to its exact name. Do not add a public host-port mapping.
2. Configure every value shown in `deploy/.env.assistant.example` through Dokploy.
   Leave `ASSISTANT_OTLP_ENDPOINT` empty when no approved collector exists.
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

No Compose invocation, migration, database access, or Dokploy action occurred in
Slice 1F1.

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

Adapter import and native event-shape capture are pending Slice 1F2.

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

## Rollback

Disable the adapters first so ordinary Open WebUI chat continues without the
companion. Restore the prior image digest for API and worker, rerun the Compose/Dokploy
rollout, and verify liveness/readiness before re-enabling adapters. Rollback has **no automatic database downgrade**:
do not run a reverse migration automatically. Stop
and review migration compatibility if the prior image cannot use the current schema.
