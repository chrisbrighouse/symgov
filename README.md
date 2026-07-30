# Symgov — engineering symbol governance and reference

Symgov governs engineering-symbol intake, evidence, review, publication, and approved-reference consumption. It is deliberately not a general drawing-management or document-management product.

This README describes repository source at commit `182430932ae315f472b9e3611d54ad4f08cee038`. Source and tests do not prove that an external production deployment is active, current, or configured identically; use separately captured deployment evidence for those claims.

## Development convention

- Run repository commands from the checkout root; do not depend on one host-specific checkout path.
- Keep runtime secrets, virtual environments, dependency trees, and generated build output out of Git.
- Treat nginx, compose, public URLs, service restarts, migrations, and static publication as separate operational steps requiring their own verification.

Glass-morphism Vite frontend plus FastAPI backend scaffold for the product split between:
- `Governance Workspace` for standards owners, methods leads, QA/admin users
- `Standards View` for engineers, contractors, reviewers, and other consumers of published standards

Current architecture/design references:

- `symgov-governance-architecture.md`
- `symgov-agent-architecture.md`
- `symgov-review-decision-orchestration.md`
- `docs/btx-submission-workflow.md` — supported Bluebeam BTX intake, conversion, storage, and review contract

## Current implementation target

The current frontend is a Vite + React static SPA build with four primary working surfaces:

- `Submissions` gathers external source symbols and sends them into the configured intake API.
- `Workspace` is the admin/operator processing view for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, Hannah, Whitney, and Ed activity.
- `Reviews` is the SME-facing review interface for Daisy-coordinated review cases.
- `Standards View` uses a published-only browse/grid/detail surface, with focused routes for full symbol pages, guided lookup, clarifications, and downloads.

Supporting routes still exist for focused tasks, but the product intent is now explicit:

- Workspace owns processing visibility, queue health, run status, artifacts, and exception visibility.
- Reviews owns human review ergonomics and draft SME decisions prepared by Daisy.
- Standards exposes latest approved published content only.
- Clarifications raised from Standards route back into governance review.

## Current frontend surface

- a glass-morphism app shell with a full-width light top banner, simple engineering-symbol logo mark, and version/date stamping
- primary banner navigation for `Submissions`, `Reviews`, and `Standards`, with the cog icon linking to the internal Workspace view
- an admin Workspace with a persistent `ADMIN WORKSPACE` bar containing the `Agents`, `Sources`, `Curation`, and `Intelligence` tabs. `Agents` shows an `Activity Monitors` header with chevron navigation, queue search, and refresh status controls. Its 12 compact lanes are Scott, Vlad, Tracy, Libby, Daisy, Rights, Review, Rupert, Ed, Hannah, Reggie, and Whitney, arranged across monitor screens. Queue/review cards use a `HH:MM DDMMMYY` top label rendered in the `Europe/London` timezone so GMT/BST changes are automatic; the second visible line uses a short package display name, Vlad cards can show a compact `Process` line, Rupert cards can link to the published Standards record after durable publication succeeds, and status sits on its own line under the activity string
- an SME Reviews workbench headed `Daisy-coordinated Reviews`, with queue navigation, visual source evidence, reviewer-editable symbol properties, classification/source context, visible case actions, comments, latest decision state, Daisy coordination, and per-child review actions
- a Standards home with left-side facets, a central approved-symbol grid, and an in-grid right detail panel for the selected row; published tables use `ID` for symbol identifiers and the `Name` column uses the published payload name when present
- account-required app access with session login, forced default-PIN change on first login, and role-gated routes (`admin`, `integrator`, `submitter`, `reviewer`)
- a submission route that probes the configured backend and submits as the logged-in user identity, showing `Submission accepted` on success instead of rendering raw backend JSON
- an admin-only Workspace `Sources` tab with a `Sources` content header and a permanently visible source-memory grid showing URL first, status/title prominent on the left, a `Next run` checkbox with checked/unchecked filtering, candidate-only Scott prompt editing, sortable columns, simple per-column filters, infinite scroll, and an internal horizontal scrollbar so wide source metadata never pushes the Workspace tabs off-screen
- an admin-only Workspace `Curation` tab for Hannah with a two-minute published-symbol photo search, countdown, Stop control, and scored photo-candidate table. Hannah searches one eligible published symbol at a time, records candidate source evidence, attaches at most two low-risk supplemental photos per symbol, and exposes accepted photos immediately on Standards records.
- an admin-only Workspace `Intelligence` tab for Whitney with a two-minute internal demand-sensing scan, countdown, Stop control, optional focus input, and scored demand-signal table. Whitney reads Symgov telemetry only in this slice and produces operator prioritization recommendations without mutating published Standards.
- route-safe SPA navigation using hash routes so static hosting remains simple
- accessible detail and compare SVG rendering for non-decorative symbol views
- explicit published page and pack context in the seeded UI data

## Frontend notes

- Standards and Reviews retain seeded fallback data for local/static development where live APIs are still pending or unavailable.
- The Workspace monitor polls configured API queue items, review cases, and Daisy coordination reports every five seconds while the Workspace route is mounted and the browser tab is visible, using `GET /api/v1/workspace/agent-queue-items` plus the review and Daisy endpoints.
- Workspace polling uses no-store timestamped requests, stops when the tab is hidden, and refreshes immediately when the tab becomes visible again.
- Workspace monitor cards use operator-readable times as the first visible row, never queue UUIDs where a timestamp exists. Libby, Daisy, Human Review, Rupert, classification, review, and publication cards use `createdAt` or review `openedAt` rendered as London local time.
- Workspace monitor cards use backend-provided `displayName` as the second visible row when available. Submitted sheets/packages receive global uppercase 4-character hex display IDs starting at `0001`; extracted split symbols display as `{packageId}-{sequence}` with an unpadded per-package sequence such as `0001-1`, `0001-13`, or `0001-999`. Single-symbol submissions display as the package ID only, such as `0001`.
- Workspace queue panels now stretch evenly to the bottom of the visible monitor area. The summary counters above the lanes and duplicate footer counts inside lanes have been removed, while the live refresh/status text has moved to a full-width single-line row above the lanes. The Scott lane shows completed items by default.
- Vlad Workspace cards can show a `Process` line above the status indicator, sourced from backend `toolSummary` values derived from Vlad run traces and payload hints. Current labels include `Tess`, `Nano`, `DXF to SVG`, `Format conversion`, `Raster split`, and `Raster candidate`; the list is intentionally extensible as Vlad's processing-tool repertoire grows.
- Rupert Workspace cards show `PUBLISHED` only after the queued symbol revision has a public published page. Those cards expose the Standards page target on the card and navigate to Standards View using the published symbol slug; Standards View accepts the linked `symbol` query parameter and opens the matching record.
- The Workspace `Sources` tab reads `GET /api/v1/workspace/scott/source-sites`, supports server-side sorting/filtering, candidate-only prompt saves, and `Next run` checkbox saves, and loads additional rows on scroll instead of showing paging controls. Scott's seeded source memory now prioritises IEC 60617, ISO 14617, ISA-5.1, ASME Y14.5 / ISO 1101, ProjectMaterials, Vista Projects, NECA 100, QElectroTech, and readable GD&T references. ProjectMaterials is the first practical P&ID seed source, but candidates must be mapped back to ISA-5.1 / ISO 14617; downloadable CAD/manufacturer libraries remain reference/intake-only until rights, reuse terms, provenance, and standards alignment are checked. The source table is intentionally wider than the visible pane, but it must remain contained inside the Sources grid shell with horizontal overflow handled by the table frame rather than the page. Checked source rows are inspected first on Scott's next source-discovery run; prompts attached to checked rows are available to Scott for that checked-source pass, including same-domain URLs mentioned in the prompt, but prompt text is not written verbatim into durable evidence.
- Full filenames, proposed symbol names, queue IDs, and longer review details remain available to search/detail/tooltips rather than being used as the compact card title.
- The Workspace monitor retains seeded `processingActivity` fallback when no API root is configured or the live queue endpoint is unavailable.
- The Standards submission route calls the configured Symgov backend instead of using demo-only local submission behavior
- Guided lookup is intentionally constrained to published approved records
- Review decisions have source-level backend support through durable `human_review_decisions` and `review_case_actions` records plus `POST /api/v1/workspace/review-cases/{id}/decisions`. Review-case source previews are exposed through `sourcePreviewUrl` and `GET /api/v1/workspace/review-cases/{review_case_id}/source/preview`.
- Review, rights, split-child, and symbol-property mutations derive the human actor from the authenticated session and reject legacy client identity fields. Human-approved publication resolves the approver from the durable review decision; publication records and governance audits retain that human snapshot while identifying Rupert's service account separately as the executor.
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
- Reviews now exposes reviewer-editable symbol properties alongside the source graphic. The symbol record identifier is labelled `ID`; `Name` is limited to 50 characters and allows letters, numbers, spaces, `-`, `/`, and `$`; `Description` is limited to 256 characters and allows any characters; `Format` is a read-only file-format badge under the description, seeded from classification, validation/intake metadata, filename, or object-key extension; `Category` and `Discipline` are free-text fields with explicit saved-value selectors. Blank property fields fall back to Libby suggestions when available. Category/discipline entries are remembered in the database with normalized display values, and reviewed name/description/category/discipline values are preferred by the publication handoff.
- Focused routes remain available for audit, per-symbol reading, downloads, and guided lookup

## Frontend build and deployment boundary

- The current frontend source is a Vite app rooted in:
  - `frontend/index.html`
  - `frontend/src/`
  - `frontend/public/`
- The workspace root now acts as the published static target and receives:
  - `index.html`
  - `assets/`
  - `submit/index.html`
- Build assets are emitted into `dist/` with `npm run build`.
- Use `npm run build:publish` to build and sync the current `dist/` output into the published workspace root.
- The submission route now includes a frontend build stamp exposed through `meta[name="symgov-build"]` so the served asset version can be confirmed quickly after deploys.
- Frontend API targeting now supports:
  - `window.SYMGOV_API_ROOT`
  - `window.SYMGOV_API_BASE_URL`
  - `window.SYMGOV_CONFIG`
  - `meta[name="symgov-api-root"]`
  - `meta[name="symgov-api-base-url"]`
- If no explicit API config is provided, local file launches still fall back to `http://127.0.0.1:8010/api/v1`, localhost-served runs fall back to `${location.origin}/api/v1`, and non-local published hosts stay unconfigured until a real public API root is provided.
- The submission API request no longer carries a shared submission PIN or editable submitter identity; backend intake now stamps submitter name/email from the authenticated session user.
- Admin user management is available at `/workspace/users` for active Plus `admin` users. New accounts start on the non-expiring Free tier. Administrators can upgrade users to Plus for calendar-month durations, extend or shorten the expiry, assign privileged roles only while Plus is active, cancel Plus, reset PINs, activate/deactivate accounts, and soft-delete users. The list is server-paginated and searchable for growth beyond the initial user set.
- Plus expiry dates are exclusive: a subscription starting 3 January for three months expires on 3 April. End-of-month starts clamp to the last valid day of shorter months. Expiry or cancellation returns the account to Free and permanently removes Admin, Integrator, Submitter, and Reviewer roles.
- `chris.brighouse@hotmail.co.uk` is the protected perpetual Plus owner. Backend rules prevent cancelling the subscription, removing Admin, deactivating, or deleting that account. Active Plus users see a Plus badge beside their signed-in name.
- Signed-in users can click their name to open `/profile`, view their identity and current subscription, activate one to five whole years of Plus at £50/year, or immediately downgrade an ordinary Plus subscription to Free. The initial self-service confirmation does not take payment, and self-service activation never grants roles; administrators remain responsible for role assignment.
- Successful self-service changes write an immutable audit origin and queue customer plus administrator email notifications transactionally. Delivery can use SMTP or Pat's AgentMail inbox; the selected transport retries queued messages without rolling back a valid subscription change, and credentials remain in protected runtime configuration.
- Static frontend changes become visible only after the intended build is published to the intended static target. Verify the served build marker and API behavior against that target rather than inferring deployment from this checkout.

## Agent implementation status

The product docs now also include the first agentization slice for Symgov:

- `symgov-agent-architecture.md` defines the shared agent runtime contract and the first concrete `Vlad` validation contract
- `Vlad` was the first OpenClaw scaffold; current model assignments are defined by `model_profiles` in `openclaw-agents.manifest.json`, not by this README
- the first runnable `Vlad` queue is intentionally local file-backed while the Symgov backend queue is still being introduced
- the local `Vlad` workspace writes queue items, run records, output artifacts, and validation reports in a queue-shaped contract that mirrors the intended backend model

`Scott` and `Tracy` also have runner contracts, and the runners support PostgreSQL write-through while retaining local JSON runtime records. This is a source capability, not a claim about a live database.

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

`Hannah` now also exists as the Symgov catalogue quality and long-term curation scaffold:

- Hannah owns the catalogue-curation scope for published Standards records
- Hannah is registered through the SymGov-owned OpenClaw manifest and backend agent seed list
- Hannah writes local curation reports under `/data/.openclaw/workspaces/hannah/runtime`
- Hannah works only on public published Standards symbols that have reasonable Name, Title, Category, and Discipline values
- Hannah records scored external photo candidates, attaches only low-risk supplemental photos, and keeps the public schematic preview distinct from real-world equipment photos. Candidate images should be photographic representations of real examples of the equipment represented by the symbol, not text documents, manuals, description pages, or diagram-only references.
- Hannah searches can be stopped from the Workspace Curation tab; the stop path marks the active queue item `cancelled`, records stop metadata, updates the runtime queue JSON, and terminates the detached runner process group when available

`Whitney` now also exists as the Symgov market intelligence and demand sensing scaffold:

- Whitney owns internal demand sensing for market intelligence, catalogue demand, and operator prioritization
- Whitney is registered through the SymGov-owned OpenClaw manifest and backend agent seed list
- Whitney writes local queue records, run logs, run records, output artifacts, and market intelligence reports under `/data/.openclaw/workspaces/whitney/runtime`
- Whitney's first slice uses internal telemetry only: published Standards coverage, clarification volume, intake patterns, and open review pressure
- Whitney records durable demand signals and market intelligence reports through `whitney_demand_signals` and `whitney_market_intelligence_reports`
- Whitney demand scans use `market_demand_scan` queue items, start in `sensing`, complete as `signals_recorded`, and can be cancelled from the Workspace Intelligence tab
- Whitney scan requests accept a 30-300 second run window plus an optional focus string; the current frontend uses the two-minute default
- Whitney does not publish, classify, approve, validate, or mutate public Standards content directly

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
- source-backed target routing is:
  - `Scott` intake
  - `Vlad` technical validation and split processing for symbol sheets or other technical work
  - `Tracy` provenance and rights review in parallel with Vlad where applicable
  - `Libby` classification and source-reference enrichment for every single-symbol candidate, including submitted single symbols and children extracted from sheets
  - `Libby` decides whether the classified symbol needs human review
  - `Daisy` coordinates only the human reviews Libby or another upstream control requires
  - `Rupert` receives publication-ready symbols either after Daisy-coordinated human approval or from a Libby no-human-review-required handoff with auditable classification, provenance, and validation evidence

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
- may send publication-ready symbols directly to Rupert only when Libby has classified the symbol, recorded Category and Discipline where ascertainable, found no unresolved validation/provenance/classification block, and explicitly marks human review as not required
- may prepare audited metadata/source/classification/disposition instructions, but durable write/delete mutations must go through Symgov-controlled backend helpers
- accepts both single-case queue items and multi-item `payload_json.items` / `payload_json.cases` batches

Agreed Libby workflow:

- `Libby` should run after `Tracy` and after Vlad split/validation output is available, before any Daisy review decision is made
- after Daisy review, any non-approval outcome returns to Libby
- classification should exist at both file level and symbol level
- the main actionable review unit should be the symbol, not only the source file
- each symbol-level classification and review case must retain lineage back to the originating file, attachment/object key, and batch
- Daisy-managed human review should start only when Libby or another upstream control explicitly requires human review after Libby has completed its classification pass
- items may cycle between Daisy review and Libby follow-up several times before final approval
- multi-item queue work produces a parent `libby_batch_report` and per-item downstream Daisy or Vlad queue items
- The intended policy is Alfi/main as Telegram-facing orchestrator, but the current manifest directly binds the Telegram account to Libby. F0.6 must resolve that contradiction before reconciliation or runtime claims.

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
- `Libby` routes clean, no-human-review-required symbols to `Rupert`
- `Daisy` review coordination for human follow-up only when human review is required
- raster split reviews are processed at child-symbol level; decided children leave the parent split workbench while undecided children remain open
- approved split children route individually to `Rupert`; every non-approval child outcome routes individually to `Libby`
- non-approval non-split review outcomes return to `Libby`
- graphic-change requests route `Libby` -> `Vlad` -> `Libby`
- `Libby` sends revised items back to `Daisy` for re-review when human review remains required, or to `Rupert` when the item is now publication-ready without human review
- `Rupert` accepts either explicit Daisy/human approval handoffs or Libby no-human-review-required publication handoffs; Rupert still blocks publication when validation, provenance, classification, policy, approval, or release evidence is incomplete

## OpenClaw compatibility and resilience

Repository-owned worker execution is selected independently through `SYMGOV_AGENT_RUNTIME`: `direct` (the default) or `hermes`. OpenClaw remains a compatibility/registration boundary for agent workspaces, bindings, model profiles, and recovery after OpenClaw upgrades. The SymGov-owned manifest is:

- `openclaw-agents.manifest.json`

That manifest is the local source of truth for OpenClaw compatibility data:

- the expected safe OpenClaw plugin profile for SymGov operations
- registered SymGov agent ids, names, workspaces, model profiles, resolved model ids, and tool profile
- managed OpenClaw `bindings[]` entries for deterministic channel/account/peer routing; the audited manifest currently contains one direct Libby binding that conflicts with the intended policy
- expected OpenClaw `agent.json` metadata paths
- required workspace files that prove each SymGov agent is still runnable

Per-agent LLM model access is configured through top-level `model_profiles` in the manifest. Each agent references a `model_profile`, and `reconcile-openclaw` expands that profile into the concrete OpenClaw `agents.list[].model` and per-agent `agent.json` model field. This keeps model policy in one SymGov-owned place while leaving provider credentials and runtime access in OpenClaw's normal configuration.

Use the backend CLI to audit or repair OpenClaw registration after upgrades:

```bash
cd /path/to/symgov/backend
python manage_symgov.py check-openclaw
python manage_symgov.py reconcile-openclaw
```

Current intent:

- SymGov-owned files remain the source of truth for OpenClaw registration and workspace compatibility; `SYMGOV_AGENT_RUNTIME` separately selects repository-worker execution
- OpenClaw config and agent metadata are treated as rebuildable runtime state
- OpenClaw bindings are also treated as rebuildable runtime state
- after an OpenClaw upgrade, `reconcile-openclaw` should be the first repair step before doing manual edits
- the current manifest binding set is not empty; resolve F0.6 before treating reconciliation as a safe repair action
- OpenClaw bindings are deterministic channel/account/peer matches; they are not free-form keyword routing rules

## Repeatable quality gates

The default backend gate is the portable repository suite. It installs the runtime
requirements plus the bounded test-only dependencies in a clean, isolated `uv`
environment and excludes tests that import separately managed agent workspaces.
From the repository root, run:

```bash
./scripts/test-backend.sh
```

The portable gate has a five-minute outer timeout by default. Override it for a
diagnostic run with `SYMGOV_BACKEND_TIMEOUT_SECONDS`; do not raise it merely to hide
a hang. The separately bounded external-workspace partition and the complete
portable-plus-external sequence are:

```bash
./scripts/test-backend.sh --external
./scripts/test-backend.sh --full
```

External files are run in separate pytest processes, with a two-minute timeout per
process, because some load code from `/data/symgov` or `/data/.openclaw/workspaces`.
This isolation also prevents their import-time `sys.modules` rewrites from corrupting
the portable suite. These tests require the Libby, Scott, Daisy, Ed and downstream
agent workspaces to exist. They also verify the retired Vlad code copy remains absent
at `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`; failures are not skipped
by the full command.

Other partitions are explicit. These examples are also from the repository root:

```bash
./scripts/test-langfuse-poc.sh
./scripts/test-frontend.sh
./scripts/build-frontend-isolated.sh
./scripts/test-verification-scripts.sh
```

From another current directory, invoke the same wrappers through an absolute repository
path instead of a cwd-relative `./scripts` path, for example:

```bash
REPO_ROOT=/path/to/symgov
"$REPO_ROOT/scripts/test-backend.sh" --full
"$REPO_ROOT/scripts/test-langfuse-poc.sh"
"$REPO_ROOT/scripts/test-frontend.sh"
"$REPO_ROOT/scripts/build-frontend-isolated.sh"
"$REPO_ROOT/scripts/test-verification-scripts.sh"
```

The Langfuse POC, frontend Node tests and isolated build each have a finite outer
timeout of two minutes by default. Diagnostic overrides are
`SYMGOV_LANGFUSE_TEST_TIMEOUT_SECONDS`,
`SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS` and
`SYMGOV_FRONTEND_BUILD_TIMEOUT_SECONDS`; each must be a positive integer.

The shell-level contract probes cover invalid timeout values, timeout exit-status
propagation, relative and in-repository build outputs, `..` and symlink resolution,
and valid external output paths without running a real frontend build.

The isolated build writes to `/tmp/symgov-build` by default and refuses an output
directory inside the repository after canonical path and symlink resolution. A
custom `SYMGOV_BUILD_OUT_DIR` must be absolute. The isolated build accepts no CLI
arguments; output customization is available only through `SYMGOV_BUILD_OUT_DIR`.
`npm run test:frontend` and `npm run build:isolated` are thin conveniences for the
frontend commands.

Use the quality gates according to the changed surface:

| Change | Required checks |
|---|---|
| Backend domain/service | Focused pytest target, then portable backend |
| Backend route/auth | Focused route matrix, then portable backend |
| Frontend | Relevant Node target, all frontend Node tests, isolated build |
| Agent/external workspace | Focused portable test plus external partition |
| Langfuse POC | Langfuse POC partition |
| Migration | Focused tests, portable backend, Alembic single-head and disposable upgrade checks |

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

## Repository and runtime boundary

- This repository contains publishable Symgov source and support files.
- Runtime checkouts may live elsewhere; commands in current docs should use the active checkout root unless a path is explicitly part of an external-runner contract.
- Do not commit live `.env.backend.database`, live `.env.backend.storage`, dependency directories, virtual environments, or generated build output.

---

## symgov (symbol governance)

This repository is now referred to as **symgov** (symbol governance). See the architecture and design notes in:

- `symgov-governance-architecture.md` — backend architecture, data model, deployment, and runbook.
- `symgov-agent-architecture.md` — agent model, queue contracts, and the first `Vlad` validation runtime.
- `.env.backend.database.example` — database-only backend environment snippet for the `symgov_app` runtime path, with a commented `symgov_migrator` migration example.
- `.env.backend.storage.example` — object-storage configuration example.
- `backend/` — backend source containing SQLAlchemy models, Alembic config, and migrations.
- `backend/manage_symgov.py` — current backend bootstrap and inspection entrypoint for agent-definition seeding, DB/storage health checks, and the FastAPI server.
- `openclaw-agents.manifest.json` — Symgov-owned OpenClaw registration manifest. At this snapshot it contains a direct Telegram-to-Libby binding, which conflicts with the documented Alfi-first orchestration intent and remains an explicit F0.6 backlog item; do not describe the binding as empty.

No database migration state, object-store reachability, seeded runtime rows, container image, public route, or external service health is asserted by this repository document. Verify those against the intended environment.

Current backend implementation notes:

- PostgreSQL remains relational-first, with `jsonb` used for flexible symbol payloads, agent outputs, evidence, and manifests
- the API server uses a versioned FastAPI route structure under `/api/v1`, with selected compatibility aliases under `/api`
- `published_pages` are per-symbol published detail records
- Standards search, browse, navigation, and listing remain read-model and API concerns, not separate managed `published_pages`
- `change_requests` target explicit draft/base revisions
- external Standards submitters are modeled as lightweight identities and do not imply direct database access

Current API growth direction:

- `public` routes for submission intake and future public clarification flows
- `published` routes for Standards browse/detail/page/pack reads
- `workspace` routes for queue/review/audit/publication actions
- `admin` routes for health and operational inspection

Current source-backed frontend/backend handoff notes:

- the static submission route calls the configured backend external-submission API instead of generating prototype-only local previews
- successful backend responses surface `intakeRecordId`, attachment object key, intake status, eligibility status, and downstream queue-item IDs
- the DB bridge maps legacy local string IDs into deterministic UUIDs so file-backed queue payloads can be mirrored into the schema without immediate queue-format churn
- raster split child review persistence now uses `review_split_items`, materialized from Vlad `derivative_manifest` children, so each proposed child can carry its own lifecycle, open/processed status, latest review action, reviewer note/details, downstream agent, and downstream queue item
- `GET /api/v1/workspace/review-cases` projects open `review_split_items` with `awaiting_decision` or `returned_for_review` as first-class human-review items with `reviewItemType: "split_item"`, parent review-case lineage, a one-child review payload, and the child preview as the primary review visual
- split-review processing is handled through `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions`; it processes only non-pending child decisions, routes approved children to Rupert and non-approval children to Libby, and closes the parent split case only when no child items remain open
- reviewer-editable symbol properties now persist in `review_symbol_properties` via Alembic revision `20260512_0006`, with `format` added by revision `20260515_0008`; review-case responses include `symbolProperties`, reviewers update them through the Workspace API, and publication staging prefers the reviewed `name`, `description`, `category`, and `discipline` values
- reusable reviewer-entered `Category` and `Discipline` values persist in `review_symbol_property_options` via Alembic revision `20260515_0007`; saved values are normalized to capitalized mixed case, deduplicated by canonical key plus conservative fuzzy matching, fetched with no-store requests, and shown as explicit saved-value selectors in Reviews
- destructive environment-reset tooling is operationally managed outside this repository and is intentionally not documented here as a portable repository command
