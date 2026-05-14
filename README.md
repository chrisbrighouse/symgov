# symgov frontend and backend workspace

## Current development convention

- Do frontend work inside openclaw-hz0t-openclaw-1 under /data/.openclaw/workspace/symgov.
- Use `/data/symgov` as the standalone GitHub-facing repository for commits and pushes that should contain only Symgov app and support files.
- Treat VPS nginx, compose, and public URL changes as a later deployment step.
- The public site may continue serving the older published bundle until the React/Vite dist/ output is intentionally published.

Glass-morphism Vite frontend plus FastAPI backend scaffold for the product split between:
- `Governance Workspace` for standards owners, methods leads, QA/admin users
- `Standards View` for engineers, contractors, reviewers, and other consumers of published standards

Current architecture/design references:

- `symgov-governance-architecture.md`
- `symgov-agent-architecture.md`
- `symgov-review-decision-orchestration.md`

## Current implementation target

The current frontend is a Vite + React static SPA build with four primary working surfaces:

- `Submissions` gathers external source symbols and sends them into the live intake path.
- `Workspace` is the admin/operator processing view for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed activity.
- `Reviews` is the SME-facing review interface for Daisy-coordinated review cases.
- `Standards View` uses a browse/detail/clarification home route for published-only lookup, with focused routes for full symbol pages, guided lookup, and downloads.

Supporting routes still exist for focused tasks, but the product intent is now explicit:

- Workspace owns processing visibility, queue health, run status, artifacts, and exception visibility.
- Reviews owns human review ergonomics and draft SME decisions prepared by Daisy.
- Standards exposes latest approved published content only.
- Clarifications raised from Standards route back into governance review.

## Current frontend surface

- a glass-morphism app shell with a full-width light top banner, simple engineering-symbol logo mark, and version/date stamping
- primary banner navigation for `Submissions`, `Reviews`, and `Standards`, with the cog icon linking to the internal Workspace view
- an admin Workspace processing dashboard headed `ADMIN WORKSPACE` / `Activity Monitors`, with eight equal-height compact vertical lanes for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed; live queue/review cards use a `HH:MM DDMMMYY` top label rendered in the `Europe/London` timezone so GMT/BST changes are automatic, the second visible card line uses a short package display name, and status sits on its own line under the activity string
- an SME Reviews workbench headed `Daisy-coordinated Reviews`, with queue navigation, visual source evidence, reviewer-editable symbol properties, classification/source context, visible case actions, comments, latest decision state, Daisy coordination, and per-child review actions
- a Standards home that keeps browse, latest approved detail, and clarification context together; published tables use `ID` for symbol identifiers and the `Name` column uses the published payload name when present
- a live submission route that probes the backend and submits through the current public Symgov API, showing `Submission accepted` on successful submit instead of rendering the raw backend JSON
- route-safe SPA navigation using hash routes so static hosting remains simple
- accessible detail and compare SVG rendering for non-decorative symbol views
- explicit published page and pack context in the seeded UI data

## Frontend notes

- Standards and Reviews retain seeded fallback data for local/static development where live APIs are still pending or unavailable.
- The Workspace monitor now polls live agent queue items, review cases, and Daisy coordination reports every five seconds while the Workspace route is mounted and the browser tab is visible, using `GET /api/v1/workspace/agent-queue-items` plus the existing review and Daisy endpoints.
- Workspace polling uses no-store timestamped requests, stops when the tab is hidden, and refreshes immediately when the tab becomes visible again.
- Workspace monitor cards use operator-readable times as the first visible row, never queue UUIDs where a timestamp exists. Libby, Daisy, Human Review, Rupert, classification, review, and publication cards use `createdAt` or review `openedAt` rendered as London local time.
- Workspace monitor cards use backend-provided `displayName` as the second visible row when available. Submitted sheets/packages receive global uppercase 4-character hex display IDs starting at `0001`; extracted split symbols display as `{packageId}-{sequence}` with an unpadded per-package sequence such as `0001-1`, `0001-13`, or `0001-999`. Single-symbol submissions display as the package ID only, such as `0001`.
- Workspace queue panels now stretch evenly to the bottom of the visible monitor area. The summary counters above the lanes and duplicate footer counts inside lanes have been removed, while the live refresh/status text has moved to a full-width row above the lanes. The Scott lane shows completed items by default.
- Full filenames, proposed symbol names, queue IDs, and longer review details remain available to search/detail/tooltips rather than being used as the compact card title.
- The Workspace monitor retains seeded `processingActivity` fallback when no API root is configured or the live queue endpoint is unavailable.
- The Standards submission route now calls the live Symgov backend instead of using demo-only local submission behavior
- Guided lookup is intentionally constrained to published approved records
- Review decisions now have source-level backend support through durable `human_review_decisions` and `review_case_actions` records plus `POST /api/v1/workspace/review-cases/{id}/decisions`. Review-case source previews are exposed through `sourcePreviewUrl` and `GET /api/v1/workspace/review-cases/{review_case_id}/source/preview`, and these routes are available through the live public API.
- Review-decision routing now preserves the Daisy-managed review loop:
  - `approve` is the only path to Rupert publication staging
  - every non-approval outcome routes to Libby with the full SME response, case comment, decision note, and child-symbol decisions
  - Libby handles metadata, classification, source, evidence, duplicate, deletion, rejection, and deferral follow-up before sending the item back to Daisy for review
  - physical symbol graphic changes route Libby -> Vlad -> Libby, then back to Daisy for re-review
- Reviews now has a live Daisy coordination read path:
  - `GET /api/v1/workspace/daisy/reports`
  - the Reviews decision rail now renders Daisy coordination output for the active review case when present
  - if no Daisy report exists yet for a case, the UI shows an explicit empty state instead of silently omitting coordination status
- Reviews now includes SME filters for stage, reviewer, priority, and action type, and the decision rail can record approve, reject, request changes, request more evidence, rename/classify, duplicate, delete, and defer outcomes when the live decision endpoint is available.
- Reviews now exposes reviewer-editable symbol properties alongside the source graphic. The symbol record identifier is labelled `ID`; `Name` is limited to 50 characters and allows letters, numbers, spaces, `-`, `/`, and `$`; `Description` is limited to 256 characters and allows any characters; `Category` and `Discipline` follow the existing classification values. These values are seeded from agent output when available, can be changed by reviewers, and are preferred by the publication handoff.
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
- The current frontend build/version display is `v0.1.5 · 2026-04-29.01`; the visible version pill intentionally omits the word `Build`.
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
- The Phase 3 Reviews frontend has been built and published to the static root used by `https://apps.chrisbrighouse.com/apps/workspace/symgov/`.
- The live public API currently exposes the Workspace queue, review-case, Daisy report, and review-decision routes used by the static frontend.

## Agent implementation status

The product docs now also include the first agentization slice for Symgov:

- `symgov-agent-architecture.md` defines the shared agent runtime contract and the first concrete `Vlad` validation contract
- `Vlad` is the first live OpenClaw scaffold for Symgov, using `ollama/gemma4:e4b`
- the first runnable `Vlad` queue is intentionally local file-backed while the Symgov backend queue is still being introduced
- the local `Vlad` workspace writes queue items, run records, output artifacts, and validation reports in a queue-shaped contract that mirrors the intended backend model

`Scott` and `Tracy` now also exist as local Symgov scaffolds, and the current runners now support a first verified write-through path into the live Symgov database while retaining the local JSON runtime records.

`Daisy` now also exists as the first Symgov review-coordination scaffold:

- Daisy can be created automatically from persisted `review_cases` emitted by `Vlad` or `Tracy`
- Daisy writes local `review_coordination_reports` artifacts under `/data/.openclaw/workspaces/daisy/runtime`
- the backend exposes those reports through `/api/v1/workspace/daisy/reports`
- the Reviews UI now shows Daisy coordination status, reviewer assignment proposals, stage-transition proposals, contributor evidence requests, visual source evidence, latest recorded decision state, and a reviewer decision panel for the active case
- the current implemented post-review loop is Daisy review -> Libby follow-up for every non-approval outcome -> optional Vlad graphic change -> Libby consolidation -> Daisy re-review; only explicit approval routes to Rupert
- queue status tracks each agent's completed work rather than the full downstream governance state: Vlad marks Libby-routed `symbol_graphic_change_request` items completed after returning the changed image result to Libby, and Daisy marks Libby follow-up/human-review escalation work completed once the human review request has been created/escalated
- durable post-review decisions and follow-on action records have a first backend implementation:
  - migration `20260426_0004_human_review_decisions.py`
  - ORM models `HumanReviewDecision` and `ReviewCaseAction`
  - `POST /api/v1/workspace/review-cases/{id}/decisions`
  - latest decision summary on `GET /api/v1/workspace/review-cases`
- the implementation design for post-review decisions, action codes, and publish-readiness flow lives in:
  - `symgov-review-decision-orchestration.md`

`Ed` now also exists as the Symgov visual experience and feedback scaffold:

- Ed is registered through the SymGov-owned OpenClaw manifest and backend agent seed list
- Ed writes local UX feedback artifacts under `/data/.openclaw/workspaces/ed/runtime`
- Ed can summarize provided feedback or collect recent messages from the existing SymGov Ops Telegram group when invoked
- Ed remains non-authoritative and does not approve, classify, publish, or change governance records

Current intake/validation baseline:

- `Scott` now accepts `.png`, `.jpg`, and `.jpeg` submissions alongside `.svg` and `.json`
- accepted and eligible raster intake can now enqueue `Vlad`
- `Vlad` now has a Phase 1 deterministic raster analysis path for PNG inputs and JPEG inputs normalized through Pillow:
  - estimates symbol count, candidate regions, sheet type, and `split_recommended`
  - emits `split_plan` artifacts for all analyzed raster inputs
  - emits `single_symbol_raster_candidate` artifacts for one-symbol files, preserving filename-derived title, aliases, keywords, description hints, attachment/object-key metadata, and candidate region
  - emits `derivative_manifest` artifacts and creates proposed child crop PNG files in a runtime `derivative_assets/` root for multi-symbol sheets
  - escalates multi-symbol and ambiguous sheets into `raster_split_review`
- the current Scott -> Vlad/Tracy handoff now carries original filename, candidate title, file note, batch summary, intake references, and attachment/object-key metadata so filename clues can flow into Libby classification
- the `symbols2.png` submission batch `subext-20260416T182301Z` has now been replayed successfully after repairing Vlad runtime ownership on `runtime/derivative_assets`
- that replay verified the current Vlad environment can see both `tesseract` and Pillow, generate OCR label candidates for the sheet, persist derivative child crops to storage, and create a live `raster_split_review` case for Workspace review
- current host-level dependencies installed outside `/data/.openclaw` for upgrade resilience include Debian `ripgrep` at `/usr/bin/rg` and Debian `python3-pil` at `/usr/lib/python3/dist-packages/PIL`
- the remaining downstream status for that submission is provenance follow-up:
  - Tracy's queue item was still `queued` at the end of the 2026-04-17 repair pass
- current live routing is now effectively:
  - `Scott` intake
  - `Vlad` technical validation when applicable
  - `Tracy` provenance and rights review
  - `Libby` classification and source-reference enrichment
  - `Daisy` coordination when downstream review follow-up exists

## Libby review follow-up direction

`Libby` is now the classification, research, and non-approval review follow-up owner.

Agreed Libby boundary:

- owns engineering discipline, formats, industry, symbol family, process category, parent equipment class, standards source, library provenance class, keywords, and aliases
- may repair or improve missing source references raised by `Tracy`
- may browse externally when needed to answer unresolved classification questions
- may create new taxonomy terms and use them immediately
- may supersede earlier classifications, but prior records should remain durable and be marked obsolete for auditability
- does not replace `Tracy` rights judgment or `Vlad` technical validation
- owns every non-approval response from Daisy-organized human review
- sends physical symbol graphic changes to `Vlad`, then checks Vlad's result and combines it with other updates before Daisy re-review
- never sends symbols to Rupert directly
- may prepare audited metadata/source/classification/disposition instructions, but durable write/delete mutations must go through Symgov-controlled backend helpers
- accepts both single-case queue items and multi-item `payload_json.items` / `payload_json.cases` batches

Agreed Libby workflow:

- `Libby` should run after `Tracy` and before the first Daisy review
- after Daisy review, any non-approval outcome returns to Libby
- classification should exist at both file level and symbol level
- the main actionable review unit should be the symbol, not only the source file
- each symbol-level classification and review case must retain lineage back to the originating file, attachment/object key, and batch
- Daisy-managed human review should start only after Libby has completed its classification pass
- items may cycle between Daisy review and Libby follow-up several times before final approval
- multi-item queue work produces a parent `libby_batch_report` and per-item downstream Daisy or Vlad queue items
- Libby's OpenClaw Telegram route is bound for `telegram:7643191699`; first chat commands should remain read-only, such as queue status and case explanation

Agreed Libby persistence direction:

- add versioned `classification_records`
- each new current classification may supersede an earlier one
- superseded classifications should be marked obsolete rather than deleted
- classification outputs should include:
  - source lineage
  - aliases
  - keywords
  - source classification
  - supporting references/evidence
  - a `libby_approved` readiness flag

Agreed initial source-classification vocabulary:

- `unknown`
- `contributor_asserted`
- `standards_derived`
- `catalog_derived`
- `internet_inferred`
- `human_confirmed`

Agreed target routing after Libby implementation:

- `Scott` intake
- `Vlad` technical validation and raster split where needed
- `Tracy` provenance and rights review
- `Libby` classification and source-repair pass
- `Daisy` review coordination for human follow-up
- raster split reviews are processed at child-symbol level; decided children leave the parent split workbench while undecided children remain open
- approved split children route individually to `Rupert`; every non-approval child outcome routes individually to `Libby`
- non-approval non-split review outcomes return to `Libby`
- graphic-change requests route `Libby` -> `Vlad` -> `Libby`
- `Libby` sends revised items back to `Daisy` for re-review
- only approved review decisions route to `Rupert`

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
- raster split child review persistence now uses `review_split_items`, materialized from Vlad `derivative_manifest` children, so each proposed child can carry its own lifecycle, open/processed status, latest review action, reviewer note/details, downstream agent, and downstream queue item
- `GET /api/v1/workspace/review-cases` projects open `review_split_items` with `awaiting_decision` or `returned_for_review` as first-class human-review items with `reviewItemType: "split_item"`, parent review-case lineage, a one-child review payload, and the child preview as the primary review visual
- split-review processing is handled through `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions`; it processes only non-pending child decisions, routes approved children to Rupert and non-approval children to Libby, and closes the parent split case only when no child items remain open
- reviewer-editable symbol properties now persist in `review_symbol_properties` via Alembic revision `20260512_0006`; review-case responses include `symbolProperties`, reviewers update them through the Workspace API, and publication staging prefers the reviewed `name`, `description`, `category`, and `discipline` values
- clean test resets should use `/data/.codex/skills/clean-symgov/scripts/clean_symgov.py --apply`; this clears operational/review/publication/source-package records and generated runtime artifacts while preserving source code and agent definitions. Because `source_packages` are cleared by that explicit reset, the next submitted sheet/package display ID starts again at `0001`.

The current MinIO bootstrap assets live outside this workspace in:

- `/docker/symgov-minio/docker-compose.yml`
- `/docker/symgov-minio/.env`
- `/docker/symgov-minio/setup-symgov-minio.sh`
