# Symgov Backend Scaffold

This directory contains the first backend scaffold for Symgov:

- `symgov_backend/`
  - FastAPI ASGI app shell, route modules, service modules, and API schemas
  - SQLAlchemy metadata and ORM models
  - database URL helpers that default to `/data/.openclaw/workspace/symgov/.env.backend.database`
- `alembic/`
  - Alembic environment and revision scripts

Current scope:

- initial relational schema for the phase-1 Symgov backend
- PostgreSQL-first types including `jsonb`
- a FastAPI/Uvicorn ASGI server shell for growable versioned APIs
- a small bootstrap and health CLI in `manage_symgov.py`
- a SymGov-owned OpenClaw registration audit and repair path for the local agent workspaces

Current API package shape:

- `symgov_backend/app.py`
  - app factory and shared exception handling
- `symgov_backend/routes/`
  - versioned route modules grouped by API boundary
- `symgov_backend/services/`
  - reusable domain workflows behind the route layer
- `symgov_backend/schemas.py`
  - typed request and response models
- `symgov_backend/dependencies.py`
  - request-scoped dependency helpers
- `symgov_backend/settings.py`
  - API runtime settings and defaults

Typical commands once dependencies are available:

```bash
cd /data/.openclaw/workspace/symgov/backend
alembic upgrade head
alembic current
python manage_symgov.py seed-agent-definitions
python manage_symgov.py check-db
python manage_symgov.py check-storage
python manage_symgov.py check-openclaw
python manage_symgov.py reconcile-openclaw
python manage_symgov.py serve-api --host 0.0.0.0 --port 8010
```

OpenClaw resilience notes:

- The canonical SymGov-to-OpenClaw registration data now lives in:
  `/data/.openclaw/workspace/symgov/openclaw-agents.manifest.json`
- `manage_symgov.py check-openclaw` audits:
  - plugin safety profile
  - OpenClaw config registration
  - managed OpenClaw `bindings[]`
  - `agent.json` presence and contents
  - workspace state files
  - required SymGov runner and definition files
- `manage_symgov.py reconcile-openclaw` repairs the OpenClaw-side registration state from that manifest.
- This is intended as the first post-upgrade recovery path when OpenClaw updates leave SymGov agent registration or plugin state inconsistent.
- The current managed binding set is intentionally empty until explicit channel/account/peer targets are chosen for `Scott`, `Tracy`, or `Vlad`.
- OpenClaw bindings currently support deterministic match fields such as channel, account, and peer; they do not provide arbitrary keyword-routing rules.

Current VPS deployment notes:

- The public Symgov API root is:
  `https://apps.chrisbrighouse.com/api/v1`
- The public apps nginx host proxies `/api/...` to:
  `http://symgov-api:8010`
- The live compose-managed backend service is:
  `openclaw-hz0t-symgov-api-1`
- The live service uses the official image:
  `ghcr.io/openclaw/openclaw:2026.4.2`
  rather than the older Hostinger sidecar image, because the verified Symgov
  backend dependency set is aligned to Python `3.11`
- Operational reminder:
  - backend code changes under `/data/.openclaw/workspace/symgov/backend` do not affect the public API until the live `openclaw-hz0t-symgov-api-1` service is restarted or redeployed
  - if public submissions still show older intake behavior after a local code change, treat that as a deployment/runtime refresh issue first

Current external submission API:

- versioned route: `POST /api/v1/public/external-submissions`
- compatibility alias: `POST /api/external-submissions`
- accepts JSON with:
  - `pin`
  - `submitter_name`
  - `submitter_email`
  - `overall_description`
  - `files[]` where each file includes `name`, `note`, `content_type`, and `content_base64`
- validates the PIN, persists the external submitter identity and attachment metadata, writes uploaded files into the Scott runtime, runs `Scott` immediately, and continues into the downstream `Vlad`, `Tracy`, `Libby`, and `Daisy` paths when the routing and review outputs require them
- the current intake path now supports `.svg`, `.png`, `.jpg`, `.jpeg`, and `.json` uploads
- accepted raster intake can now reach `Vlad` for `raster_sheet_analysis`, including JPEG inputs normalized through Pillow in the live Python runtime
- one-symbol raster files now produce a `single_symbol_raster_candidate` artifact with filename-derived title, aliases, keywords, note-derived description hints, and attachment/object-key lineage; multi-symbol sheets still produce proposed child crops and `raster_split_review` follow-up
- versioned health route: `GET /api/v1/health`
- compatibility alias: `GET /api/health`

Current Workspace APIs:

- versioned agent queue route: `GET /api/v1/workspace/agent-queue-items`
- compatibility alias: `GET /api/workspace/agent-queue-items`
- review cases and Daisy reports remain the other live Workspace dashboard inputs

Planned API growth boundaries:

- `/api/v1/public`
  - external submission intake and future public clarification capture
- `/api/v1/published`
  - Standards browse, detail, pages, and packs
- `/api/v1/workspace`
  - queue, review, audit, and publication actions
- `/api/v1/admin`
  - health and operational inspection

Smoke-test flow for the current local agent slice:

```bash
python /data/.openclaw/workspaces/scott/run_scott_intake.py \
  --input /data/.openclaw/workspaces/scott/tasks/example-task.json \
  --output /tmp/scott-smoke-output.json \
  --runtime-root /data/.openclaw/workspaces/scott/runtime \
  --persist-db \
  --db-env-file /data/.openclaw/workspace/symgov/.env.backend.database

python /data/.openclaw/workspaces/scott/enqueue_scott_downstream.py \
  --intake-record /data/.openclaw/workspaces/scott/runtime/intake_records/ir-aqi-scott-0001-20260409T130244Z.json \
  --vlad-runtime-root /data/.openclaw/workspaces/vlad/runtime \
  --tracy-runtime-root /data/.openclaw/workspaces/tracy/runtime

python /data/.openclaw/workspaces/vlad/run_vlad_validation.py \
  --queue-item /data/.openclaw/workspaces/vlad/runtime/agent_queue_items/aqi-vlad-ir-aqi-scott-0001-20260409T130244Z-20260409T164854Z.json \
  --runtime-root /data/.openclaw/workspaces/vlad/runtime \
  --persist-db \
  --db-env-file /data/.openclaw/workspace/symgov/.env.backend.database

python /data/.openclaw/workspaces/tracy/run_tracy_provenance.py \
  --queue-item /data/.openclaw/workspaces/tracy/runtime/agent_queue_items/aqi-tracy-ir-aqi-scott-0001-20260409T130244Z-20260409T164854Z.json \
  --runtime-root /data/.openclaw/workspaces/tracy/runtime \
  --persist-db \
  --db-env-file /data/.openclaw/workspace/symgov/.env.backend.database

python manage_symgov.py check-db
python manage_symgov.py check-storage
```

Current runner bridge notes:

- `manage_symgov.py seed-agent-definitions` upserts baseline `agent_definitions` rows for `scott`, `vlad`, and `tracy`
- `manage_symgov.py check-db` reports basic connectivity plus counts for the current agent runtime tables
- `manage_symgov.py check-storage` probes the configured MinIO endpoint and live health URL
- `manage_symgov.py check-openclaw` audits whether local OpenClaw registration still matches the SymGov manifest
- `manage_symgov.py reconcile-openclaw` repairs missing or drifted OpenClaw registration state from the SymGov manifest
- the same audit/reconcile flow now also owns top-level OpenClaw `bindings[]` so future routing rules can survive upgrades
- `manage_symgov.py serve-api` now runs the FastAPI/Uvicorn server shell for Symgov APIs
- the current `Scott`, `Vlad`, and `Tracy` file-backed runners now support `--persist-db` to mirror queue execution into PostgreSQL while keeping the local JSON runtime records
- the current verified smoke path is `Scott` intake -> downstream enqueue -> `Vlad` validation + `Tracy` provenance, with successful PostgreSQL persistence and successful MinIO/database health checks
- the external submission API now uses that same live path for uploaded symbol files, starting with `Scott` intake and preserving submitter, original filename, candidate title, batch-summary, per-file-note, attachment, and object-key context in the normalized submission payload
- current host-level dependencies installed outside `/data/.openclaw` for upgrade resilience include Debian `ripgrep` at `/usr/bin/rg` and Debian `python3-pil` at `/usr/lib/python3/dist-packages/PIL`
- the current frontend submission route is expected to target this live API using a deploy-configured API root and a visible frontend build marker so VPS-served assets can be verified after deploys
