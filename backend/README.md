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
python manage_symgov.py seed-scott-source-discovery
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
  - manifest-defined model profiles expanded into concrete per-agent OpenClaw model ids
  - managed OpenClaw `bindings[]`
  - `agent.json` presence and contents
  - workspace state files
  - required SymGov runner and definition files
- `manage_symgov.py reconcile-openclaw` repairs the OpenClaw-side registration state from that manifest.
- Per-agent LLM model access is configured through manifest `model_profiles` plus each agent's `model_profile`. Reconciliation resolves the profile to OpenClaw's concrete `agents.list[].model` and per-agent `agent.json` `model` field.
- This is intended as the first post-upgrade recovery path when OpenClaw updates leave SymGov agent registration or plugin state inconsistent.
- The current managed binding set is intentionally empty so `telegram:7643191699` remains handled by Alfi/main. Do not bind Telegram directly to `Libby` unless the orchestrator model is intentionally changed.
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
  - testability in the VPS/containerized deployment only begins after the frontend build has been published and the live service has been refreshed; a local code edit alone is not enough

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
- Scott source-discovery memory is exposed through `GET /api/v1/workspace/scott/source-sites`; the route returns URL, status, title, domain, descriptive metadata, `includeNextRun`, candidate-only source prompts, formats, evidence, relevance score, timestamps, and last queue item, with offset/limit lazy loading plus server-side filters and sorting for each grid column. Candidate source prompts are edited through `PATCH /api/v1/workspace/scott/source-sites/{source_site_id}/prompt`; `Next run` inclusion is edited through `PATCH /api/v1/workspace/scott/source-sites/{source_site_id}/include-next-run`. The frontend retries these PATCH calls with a wrapped `{request: ...}` body when the deployed API expects FastAPI's wrapped body shape.
- Hannah published-symbol curation is started through `POST /api/v1/workspace/hannah/curation-searches`; the backend creates a Hannah queue item with a two-minute default run window, writes a runtime queue JSON record, and launches the Hannah runner with DB persistence.
- Hannah published-symbol curation can be stopped through `POST /api/v1/workspace/hannah/curation-searches/{queue_item_id}/stop`; the backend marks the queue item `cancelled`, stamps `completed_at`, records stop metadata in the queue payload, updates the runtime queue JSON, and sends `SIGTERM` to the stored process group when available.
- Hannah photo candidates are exposed through `GET /api/v1/workspace/hannah/photo-candidates`; the route supports lazy loading plus sort/filter parameters and returns symbol context, candidate/source URLs, source domain, rights status, license, score, curation status, timestamps, and preview URLs for attached public supplemental photos.
- Whitney market demand sensing is started through `POST /api/v1/workspace/whitney/demand-scans`; the request accepts `durationSeconds` from 30 to 300 and optional `focus` text up to 120 characters. The backend creates a Whitney `market_demand_scan` queue item with the two-minute default run window, writes a runtime queue JSON record, and launches the Whitney runner with DB persistence. If a Whitney scan is already `queued`, `running`, or `sensing`, the endpoint returns the active queue item instead of launching a duplicate.
- Whitney market demand sensing can be stopped through `POST /api/v1/workspace/whitney/demand-scans/{queue_item_id}/stop`; the backend marks the queue item `cancelled`, stamps `completed_at`, records stop metadata in the queue payload, updates the runtime queue JSON, and sends `SIGTERM` to the stored process group when available.
- Whitney demand signals are exposed through `GET /api/v1/workspace/whitney/demand-signals`; the route supports lazy loading plus sort/filter parameters and returns signal type, market segment, discipline, category, symbol/page context, demand score, confidence, recommendation, source, status, evidence, timestamps, and queue lineage.
- Workspace agent-queue, review-case, and review-child responses can include `displayName`, `packageDisplayId`, and `packageSymbolSequence` for compact monitor-card naming. The visible convention is `0001` for a submitted sheet or single-symbol package and `0001-1`, `0001-2`, ... for extracted split symbols.
- Workspace agent-queue responses can include `toolSummary` for Vlad queue items. The field is derived from the latest `agent_runs.tool_trace_json` plus queue payload hints and currently supports compact labels such as `Tess`, `Nano`, `DXF to SVG`, `Format conversion`, `Raster split`, and `Raster candidate`.
- Rupert Workspace agent-queue responses expose `publishedSymbolId`, `publishedPageCode`, `publishedPackCode`, and `publishedStandardsPath` after the queued symbol revision has a public published page; those queue rows report `status='published'` so the Workspace can show `PUBLISHED` and link to Standards View.
- review-case responses can include `symbolProperties` for reviewer-editable per-symbol `Name`, `Description`, `Category`, and `Discipline` values
- reviewer-editable symbol properties are updated through `PATCH /api/v1/workspace/review-cases/{id}/symbol-properties`; the API enforces the current review rules for name length/characters and description length
- reviewer-entered `Category` and `Discipline` values are remembered in `review_symbol_property_options`, normalized to capitalized mixed case, deduplicated by canonical key plus conservative fuzzy matching, and exposed through `GET /api/v1/workspace/review-symbol-property-options` for Reviews picklists
- reviewer symbol properties now also include read-only `Format`, seeded from Libby classification when available and otherwise inferred from validation/intake metadata, source filename, or object key
- review decisions are recorded through `POST /api/v1/workspace/review-cases/{id}/decisions`
- review-decision routing now uses:
  - `approve` -> Rupert publication handoff
  - every non-approval decision -> Libby review follow-up handoff
  - Libby -> Vlad -> Libby for physical symbol graphic changes
  - Libby -> Daisy for first review or re-review when human review is required
  - Libby -> Rupert when a single-symbol item is classified, Category/Discipline have been added where ascertainable, no upstream block remains, and Libby records that human review is not required
- published Standards rows prefer `symbol_revisions.payload_json.name` when building the `Name` column, with legacy `canonical_name` retained only as fallback
- published Standards symbol list/detail payloads can include `supplementalPhotos` for Hannah-attached public equipment-photo references. The public preview route is `GET /api/v1/published/symbols/{symbol_id}/supplemental-photos/{photo_id}/preview`, and Standards UI keeps these real-world photos separate from the schematic symbol preview.

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

- `manage_symgov.py seed-agent-definitions` upserts baseline `agent_definitions` rows for `scott`, `vlad`, `tracy`, `daisy`, `libby`, `rupert`, `ed`, `hannah`, and `whitney`
- `manage_symgov.py seed-scott-source-discovery` upserts Scott's durable source-discovery memory rows, prioritising the recommended standards/source backbone: IEC 60617, ISO 14617, ISA-5.1, ASME Y14.5 / ISO 1101, ProjectMaterials, Vista Projects, NECA 100, QElectroTech, readable GD&T references, and rights-gated CAD-library candidates; ignored domains such as `linecad.com`, `svghmi.pro`, and `autodesk.com` remain ignored
- Scott source-search defaults now seed toward `ProjectMaterials P&ID symbols ISA-5.1 ISO 14617 IEC 60617 NECA 100 QElectroTech GD&T`, while ignored domains and checked `include_next_run` rows are passed into the next run payload
- Scott treats downloadable CAD/manufacturer libraries as reference/intake sources only until rights, reuse terms, provenance, and standards alignment have been checked. ProjectMaterials is treated as the immediate practical P&ID seed source, but its candidates must be mapped back to ISA-5.1 / ISO 14617 rather than treated as authoritative.
- Scott source-site browsing now supports two operator edits from the Workspace Sources grid: candidate-only source prompts and `Next run` checkboxes. Checked rows are inspected before the normal web search. If a checked row has a prompt, Scott keeps that prompt available during the checked-source pass and can inspect same-domain URLs mentioned in it, but durable evidence records only prompt availability rather than the prompt text.
- Hannah curation uses Alembic revision `20260519_0010_hannah_curation.py` for `hannah_symbol_curation_states` and `hannah_photo_candidates`. The runner searches eligible public published symbols through Wikimedia Commons metadata, records all scored candidates, uploads only low-risk licensed image files when storage is configured, and caps attached public supplemental photos at two per symbol.
- Hannah persistence writes `hannah_curation_report` artifacts through the runtime bridge and records audit events for metadata updates and supplemental-photo attachment.
- Whitney market intelligence uses Alembic revision `20260522_0011_whitney_market_intelligence.py` for `whitney_market_intelligence_reports` and `whitney_demand_signals`. The runner currently reads internal Symgov telemetry only, writes `market_intelligence_reports` runtime records, and persists `whitney_market_intelligence_report` artifacts through the runtime bridge. Demand signals are upserted by `(source_type, source_ref, signal_type)` so repeated scans refresh the durable signal instead of duplicating it.
- `manage_symgov.py check-db` reports basic connectivity plus counts for the current agent runtime tables
- `manage_symgov.py check-storage` probes the configured MinIO endpoint and live health URL
- `manage_symgov.py check-openclaw` audits whether local OpenClaw registration still matches the SymGov manifest
- `manage_symgov.py reconcile-openclaw` repairs missing or drifted OpenClaw registration state from the SymGov manifest
- the same audit/reconcile flow now also owns top-level OpenClaw `bindings[]` so future routing rules can survive upgrades
- `manage_symgov.py serve-api` now runs the FastAPI/Uvicorn server shell for Symgov APIs
- the current `Scott`, `Vlad`, and `Tracy` file-backed runners now support `--persist-db` to mirror queue execution into PostgreSQL while keeping the local JSON runtime records
- Libby is the required classification and publication-readiness triage step for submitted single symbols and split-sheet child symbols; it supports classification work, `review_decision_follow_up` queue items for non-approval review outcomes, and `vlad_graphic_update_completed` queue items for Vlad returns
- Vlad now supports `symbol_graphic_change_request` queue items and returns graphic-change results to Libby before Daisy re-review; those graphic-change queue items are marked `completed` after Vlad returns the result to Libby
- Daisy marks Libby follow-up/human-review escalation work `completed` once the required human review request has been created/escalated, and the backend queue bridge mirrors related Daisy queue-item completions into PostgreSQL
- intake persistence now creates a `source_packages` row for each submitted sheet/file and assigns the next global uppercase 4-character hex `package_code`, starting at `0001` after an explicit clean reset. The code is copied into intake `normalized_submission_json.source_package_code` and `workspace_display_name` for downstream Workspace display.
- raster split child review state now persists in `review_split_items` via Alembic revision `20260503_0005`; split items are materialized from Vlad `derivative_manifest` children, exposed by `GET /api/v1/workspace/review-cases` as first-class `split_item` human-review records while open, and processed through `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions`
- materialized split items store `package_display_id`, `package_symbol_sequence`, and `workspace_display_name` in `review_split_items.payload_json`, allowing Workspace cards and review responses to show short names like `0001-3` while retaining full filenames and proposed symbol IDs in detail payloads
- split processing currently routes approved children to Rupert and every non-approval child to Libby, while pending or returned children stay open as individual split-item review records; target intake routing sends all extracted child symbols through Libby classification before Daisy or Rupert
- reviewer-editable symbol properties now persist in `review_symbol_properties` via Alembic revision `20260512_0006`; review responses seed those values from classification/agent data when available, reviewers can update them, and publication staging prefers the reviewed values
- reviewer-entered Category and Discipline picklist memory now persists in `review_symbol_property_options` via Alembic revision `20260515_0007`; the runtime API records options as reviewers save properties and returns them for the Reviews autocomplete picklists
- reviewer symbol property Format now persists in `review_symbol_properties.format` via Alembic revision `20260515_0008`; review responses backfill blank values from known source-format metadata so Reviews does not show an empty Format property for known inputs such as PNG, JPEG, SVG, SVF, or DXF
- the current verified smoke path is `Scott` intake -> downstream enqueue -> `Vlad` validation + `Tracy` provenance, with successful PostgreSQL persistence and successful MinIO/database health checks
- the external submission API now uses that same live path for uploaded symbol files, starting with `Scott` intake and preserving submitter, original filename, candidate title, batch-summary, per-file-note, attachment, and object-key context in the normalized submission payload
- current host-level dependencies installed outside `/data/.openclaw` for upgrade resilience include Debian `ripgrep` at `/usr/bin/rg` and Debian `python3-pil` at `/usr/lib/python3/dist-packages/PIL`
- the current frontend submission route is expected to target this live API using a deploy-configured API root and a visible frontend build marker so VPS-served assets can be verified after deploys
