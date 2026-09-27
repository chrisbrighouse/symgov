# Ed application-guru Stage 0 baseline and implementation kickoff

Date: 2026-09-27
Status: Stage 0 complete for the first bounded implementation package; no commit, push, migration, deployment, restart or production activation authorised

## Controlling documents

- Product specification: `docs/plans/2026-09-27-ed-application-guru-spec.md`
  - SHA-256: `76b64204fb1c844fdc6b3a481736470d6368f037b728ce2ecc49bc9bc13fbb01`
- Implementation plan: `docs/plans/2026-09-27-ed-application-guru-implementation-plan.md`
  - SHA-256: `b17334e1f10c88aa904c2ce569d1da08b515d52c53f12779b0f08d1ded06bf44`

The seven resolved decisions in specification section 10 are controlling. They must not be reopened by an implementation worker unless live repository evidence proves a genuine contradiction.

## Repository baseline

- Repository: `/docker/openclaw-hz0t/data/symgov`
- Branch: `main`
- Baseline commit: `c9cb088`
- Upstream relation at kickoff: `main...origin/main`
- Pre-existing unrelated dirty path: `.claude/settings.local.json`
- New planning paths introduced for Ed:
  - `docs/plans/2026-09-27-ed-application-guru-spec.md`
  - `docs/plans/2026-09-27-ed-application-guru-implementation-plan.md`
  - `docs/plans/2026-09-27-ed-stage0-baseline.md`

The `.claude/settings.local.json` modification is outside Ed scope and must remain byte-preserved. Do not reset, clean, stash, stage or rewrite it.

## Live repository inventory

### Existing Ed surfaces

- `backend/symgov_backend/catalog_integration_ed.py` implements the existing deterministic, stateless Catalog Integration Ed.
- `POST /api/v1/catalog/developer/ed` is protected by the Catalog developer route boundary and Catalog API-key context.
- `tests/test_catalog_integration_ed.py` proves topic answers, citations, no conversation memory, credential rejection, body bounds and support fallback.
- The Catalog Integration Ed contract must remain unchanged by the first Ed guru package.

### Existing generic LLM boundary

- `backend/symgov_backend/routes/llm.py` exposes authenticated `POST /api/v1/llm/chat` and admin settings/test/usage routes.
- `backend/symgov_backend/services/llm.py` currently normalizes application settings to `openrouter`, supports feature-specific model identifiers and reads server-side credentials.
- `backend/symgov_backend/services/llm_router.py` already provides a broader provider-capable routing layer and privacy-conscious usage telemetry.
- `tests/test_admin_llm_management_routes.py`, `tests/test_llm_router.py`, usage-ledger tests and telemetry tests cover the current boundary.
- The generic `/llm/chat` route is not the Ed guru API and must not be exposed directly as the new product surface.

### Authentication and frontend

- `backend/symgov_backend/app.py` mounts route families under `/api/v1` with central session, CSRF and role dependencies.
- `frontend/src/App.jsx` has authenticated routes and an admin-only `/workspace/llm` page, but no general Ed guru route.
- The new Ed route is intended for every authenticated user, subject to a separately enforced selected-organization pilot gate during activation.

## Privacy, retention and support policy for phase 1

- The visible conversation transcript is browser-only and is not persisted as retrievable server conversation history.
- Existing LLM telemetry must continue to exclude raw prompt text, raw response text and credentials.
- Server telemetry may record bounded request identity, topic/tool names, provider/model, latency, token/cost data, citation IDs, knowledge version, result category and denial/failure category.
- Full prompt or answer content must not be stored by default.
- Feedback persistence is deferred until its exact fields and retention period are separately specified; the first implementation package adds no feedback table or route.
- Unresolved questions direct users to the existing authenticated `/support` route.

## First implementation package

Implement only the Stage 1 knowledge-foundation slice:

1. versioned internal knowledge-manifest models/schema;
2. closed topic and visibility vocabularies;
3. atomic claim/source metadata including approval state, source version, extraction time and supersession links;
4. deterministic validation for duplicate identifiers, missing or disallowed source paths, invalid visibility/approval values, broken supersession references and missing required topic coverage;
5. a small representative manifest fixture and focused tests;
6. a deterministic validation report that marks requested topics covered or incomplete.

### Explicit exclusions

- no database migration or ORM table;
- no HTTP route;
- no provider call or model setting change;
- no frontend route or component;
- no embeddings/vector database;
- no production corpus build;
- no edits to Catalog Integration Ed;
- no edits to existing generic LLM routes;
- no commit, push, deployment, restart, shared/live migration or production activation.

### Expected path envelope

The implementation worker may add a narrowly named backend knowledge module, its fixture/data directory if needed, and one focused test module. If another path is genuinely required, the worker must stop and report it rather than widening scope silently.

### Verification gate

- observe a focused failing test before production code;
- make the focused knowledge-manifest/validator suite green;
- run the nearest existing non-PostgreSQL Ed/LLM regression tests;
- run `git diff --check` for the exact changed paths;
- report exact changed paths and commands;
- stop for controller verification before any review, commit or next package.
