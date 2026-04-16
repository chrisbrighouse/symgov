# symgov frontend and backend workspace

## Current development convention

- Do frontend work inside openclaw-hz0t-openclaw-1 under /data/.openclaw/workspace/symgov.
- Use `/data/symgov` as the standalone GitHub-facing repository for commits and pushes that should contain only Symgov app and support files.
- Treat VPS nginx, compose, and public URL changes as a later deployment step.
- The public site may continue serving the older published bundle until the React/Vite dist/ output is intentionally published.

Glass-morphism Vite frontend plus FastAPI backend scaffold for the product split between:
- `Governance Workspace` for standards owners, methods leads, QA/admin users
- `Standards View` for engineers, contractors, reviewers, and other consumers of published standards

## Current implementation target

The current frontend is a Vite + React static SPA build with two primary working surfaces:

- `Governance Workspace` uses a queue-first review route for triage, compare-in-context, approval, and publication impact review.
- `Standards View` uses a browse/detail/clarification home route for published-only lookup, with focused routes for full symbol pages, guided lookup, and downloads.

Supporting routes still exist for focused tasks, but the product intent is now explicit:

- Workspace owns draft, review, compare, audit, and publish flow.
- Standards exposes latest approved published content only.
- Clarifications raised from Standards route back into Workspace review.

## Current frontend surface

- a glass-morphism app shell with shared navigation and build stamping
- a queue-first Governance Workspace review surface with visible impact and approval context
- a Standards home that keeps browse, latest approved detail, and clarification context together
- a live submission route that probes the backend and submits through the current public Symgov API
- route-safe SPA navigation using hash routes so static hosting remains simple
- accessible detail and compare SVG rendering for non-decorative symbol views
- explicit published page and pack context in the seeded UI data

## Frontend notes

- Standards and Workspace surface data is currently seeded in the frontend and shaped to match the intended API model
- The Standards submission route now calls the live Symgov backend instead of using demo-only local submission behavior
- Guided lookup is intentionally constrained to published approved records
- Workspace approval and Standards clarification loops are still frontend-seeded until the corresponding backend routes land
- Focused routes remain available for audit, per-symbol reading, downloads, and guided lookup

## Frontend deployment notes

- The current frontend source is a Vite app rooted in:
  - `frontend/index.html`
  - `frontend/src/`
  - `frontend/public/`
- The workspace root now acts as the published static target and receives:
  - `index.html`
  - `assets/`
  - `submit/index.html`
- Production assets are emitted into `dist/` with `npm run build`.
- Use `npm run build:publish` to build and sync the current `dist/` output into the published workspace root.
- Until that build output is intentionally published on the VPS, the public site may still reflect an older bundle than the local workspace source.
- Because this workspace lives under `/data/.openclaw/workspace/symgov`, it is publishable directly through the existing `applications-web` nginx mount and should appear under `/apps/workspace/symgov/`.
- The main app entry is:
  - `https://apps.chrisbrighouse.com/apps/workspace/symgov/`
- A direct static submission entrypoint is also available at:
  - `https://apps.chrisbrighouse.com/apps/workspace/symgov/submit/`
- The submission route now includes a frontend build stamp exposed through `meta[name="symgov-build"]` so the served asset version can be confirmed quickly after deploys.
- Frontend API targeting now supports:
  - `window.SYMGOV_API_ROOT`
  - `window.SYMGOV_API_BASE_URL`
  - `window.SYMGOV_CONFIG`
  - `meta[name="symgov-api-root"]`
  - `meta[name="symgov-api-base-url"]`
- The current published Symgov deployment sets `meta[name="symgov-api-root"]` to `https://apps.chrisbrighouse.com/api/v1`.
- If no explicit API config is provided, local file launches still fall back to `http://127.0.0.1:8010/api/v1`, localhost-served runs fall back to `${location.origin}/api/v1`, and non-local published hosts stay unconfigured until a real public API root is provided.
- The Standards submission route now probes `https://apps.chrisbrighouse.com/api/v1/health` and submits to `https://apps.chrisbrighouse.com/api/v1/public/external-submissions` on the live VPS deployment.
- The published submission gate must not expose the secret PIN in helper text or placeholder copy.
- Static frontend changes only become visible on the public site after the built files under `dist/` are published into the workspace root static target.
- When forcing a visible frontend refresh on the published site, bump the build marker and republish the generated asset filenames from `dist/assets/`.

## Agent implementation status

The product docs now also include the first agentization slice for Symgov:

- `symgov-agent-architecture.md` defines the shared agent runtime contract and the first concrete `Vlad` validation contract
- `Vlad` is the first live OpenClaw scaffold for Symgov, using `ollama/gemma4:e4b`
- the first runnable `Vlad` queue is intentionally local file-backed while the Symgov backend queue is still being introduced
- the local `Vlad` workspace writes queue items, run records, output artifacts, and validation reports in a queue-shaped contract that mirrors the intended backend model

`Scott` and `Tracy` now also exist as local Symgov scaffolds, and the current runners now support a first verified write-through path into the live Symgov database while retaining the local JSON runtime records.

Current intake/validation baseline:

- `Scott` now accepts `.png` submissions as supported intake alongside `.svg` and `.json`
- accepted and eligible PNG intake can now enqueue `Vlad`
- `Vlad` now has a Phase 1 deterministic PNG raster split path that:
  - estimates symbol count, candidate regions, sheet type, and `split_recommended`
  - emits `split_plan` and `derivative_manifest` artifacts
  - creates proposed child crop PNG files in a runtime `derivative_assets/` root
  - escalates multi-symbol and ambiguous sheets into `raster_split_review`
- the current Scott -> Vlad handoff now also carries intake and attachment references needed for Phase 1 split persistence

## OpenClaw resilience

To make SymGov more resilient to OpenClaw upgrades, the current workspace now keeps a SymGov-owned OpenClaw manifest at:

- `openclaw-agents.manifest.json`

That manifest is now the local source of truth for:

- the expected safe OpenClaw plugin profile for SymGov operations
- registered SymGov agent ids, names, workspaces, models, and tool profile
- managed OpenClaw `bindings[]` entries for deterministic channel/account/peer routing
- expected OpenClaw `agent.json` metadata paths
- required workspace files that prove each SymGov agent is still runnable

Use the backend CLI to audit or repair OpenClaw registration after upgrades:

```bash
cd /data/.openclaw/workspace/symgov/backend
python manage_symgov.py check-openclaw
python manage_symgov.py reconcile-openclaw
```

Current intent:

- SymGov-owned files remain the source of truth
- OpenClaw config and agent metadata are treated as rebuildable runtime state
- OpenClaw bindings are also treated as rebuildable runtime state
- after an OpenClaw upgrade, `reconcile-openclaw` should be the first repair step before doing manual edits
- the current managed SymGov binding set is intentionally empty until concrete channel/account targets are chosen
- OpenClaw bindings are deterministic channel/account/peer matches; they are not free-form keyword routing rules

## Run

Install dependencies and run the frontend locally:

```bash
npm install --include=dev
npm run dev
```

Generate production assets with:

```bash
npm run build
```

Build and sync the published static target with:

```bash
npm run build:publish
```

## Repository split

- `/data/.openclaw/workspace/symgov` remains the active local implementation workspace inside the broader OpenClaw environment.
- `/data/symgov` is the standalone GitHub-facing repository synced to `git@github.com:chrisbrighouse/symgov.git`.
- Keep `/data/symgov` limited to publishable Symgov source and support files only.
- Do not commit live `.env.backend.database`, live `.env.backend.storage`, dependency directories, virtualenvs, or generated build output to the standalone repo.

---

## symgov (symbol governance)

This repository is now referred to as **symgov** (symbol governance). See the architecture and design notes in:

- `symgov-governance-architecture.md` — backend architecture, data model, deployment, and runbook.
- `symgov-agent-architecture.md` — agent model, queue contracts, and the first `Vlad` validation runtime.
- `.env.backend.database.example` — database-only backend environment snippet for the `symgov_app` runtime path, with a commented `symgov_migrator` migration example.
- `.env.backend.database` — current VPS database credentials snippet for the local Symgov backend runtime.
- `.env.backend.storage` — current VPS object-storage snippet for the local MinIO deployment on `ai-stack`.
- `backend/` — current backend scaffold containing SQLAlchemy models, Alembic config, and the first live migration set.
- `backend/manage_symgov.py` — current backend bootstrap and inspection entrypoint for agent-definition seeding, DB/storage health checks, and the FastAPI server.
- `openclaw-agents.manifest.json` — current SymGov-owned OpenClaw registration manifest for `Scott`, `Tracy`, and `Vlad`.

## Current VPS backend support

For the first backend phase on this VPS:

- PostgreSQL runs in the separate `symgov-postgres` compose project.
- S3-compatible object storage runs in the separate `symgov-minio` compose project using MinIO.
- the current Symgov backend scaffold lives in:
  - `backend/symgov_backend/`
  - `backend/alembic/`
- the current runtime-facing env snippets live beside this README:
  - `.env.backend.database`
  - `.env.backend.storage`

## Current live backend status

- the first Alembic migration has been applied successfully to the live `symgov-postgres` database
- the runtime `symgov_app` role can read the created schema objects
- the migrator path uses the dedicated `symgov_migrator` role
- local MinIO is reachable on `ai-stack` and the configured app credentials can access the `symgov-dev` bucket
- baseline `agent_definitions` rows for `scott`, `vlad`, and `tracy` have been seeded into the live database
- `manage_symgov.py check-db` and `manage_symgov.py check-storage` now provide lightweight backend inspection commands
- `manage_symgov.py serve-api` now runs the FastAPI/Uvicorn API shell
- the current `Scott`, `Vlad`, and `Tracy` runners support `--persist-db` to mirror queue execution into PostgreSQL while keeping the local file-backed runtime contract

Current backend implementation notes:

- PostgreSQL remains relational-first, with `jsonb` used for flexible symbol payloads, agent outputs, evidence, and manifests
- the API server now uses a versioned FastAPI route structure under `/api/v1`, with compatibility aliases preserved for the first live `/api` endpoints
- `published_pages` are per-symbol published detail records
- Standards search, browse, navigation, and listing remain read-model and API concerns, not separate managed `published_pages`
- `change_requests` target explicit draft/base revisions
- external Standards submitters are modeled as lightweight identities and do not imply direct database access

Current API growth direction:

- `public` routes for submission intake and future public clarification flows
- `published` routes for Standards browse/detail/page/pack reads
- `workspace` routes for queue/review/audit/publication actions
- `admin` routes for health and operational inspection

Current frontend/backend handoff notes:

- the static submission route now calls the live backend external submission API instead of generating prototype-only local submission previews
- successful backend responses now surface real `intakeRecordId`, attachment object key, intake status, eligibility status, and downstream queue-item ids in the frontend

Current verified runtime baseline:

- `Scott` has been verified writing one queue item, run record, output artifact, and `intake_record` into PostgreSQL
- `Vlad` has been verified writing one queue item, run record, output artifact, and `validation_report` into PostgreSQL
- `Tracy` has been verified writing one queue item, run record, output artifact, and `provenance_assessment` into PostgreSQL
- the DB bridge currently maps legacy local string IDs into deterministic UUIDs so existing file-backed queue payloads can be mirrored into the live schema without immediate queue-format churn
- Vlad Phase 1 raster split persistence has now also been verified in a controlled live test for:
  - persisted `split_plan` and `derivative_manifest` artifacts
  - persisted derivative child `attachments`
  - persisted `raster_split_review` review cases
  - the temporary test rows were cleaned up after verification

The current MinIO bootstrap assets live outside this workspace in:

- `/docker/symgov-minio/docker-compose.yml`
- `/docker/symgov-minio/.env`
- `/docker/symgov-minio/setup-symgov-minio.sh`
