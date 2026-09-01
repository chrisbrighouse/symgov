# Symbol Set Management — Product Stage 5 Implementation Plan

> **For Hermes / OpenClaw orchestration and Claude Code:** this is the one committed, controlling plan for Product Stage 5. Ephemeral `/tmp` manifests, restart prompts, and review verdicts produced by either orchestration layer are session-scoped evidence, not sources of truth — reconcile against this file and the current repository state (`git log`, `git status`, Alembic head) before resuming or dispatching a Stage 5 work package. If a `/tmp` artifact and this file disagree, this file wins; update this file rather than trusting a stale scratch manifest.

**Status:** IMPLEMENTATION IN PROGRESS — WP5.1 is complete and committed. WP5.2 (a, b, c), WP5.3 (draft/revision/intake/asset service and API), and WP5.4 (organization review lifecycle) are complete, tested against the disposable Postgres container, and awaiting review/commit. The `organization-symbols` API is mounted behind the `organizations_enabled`/`organization_symbols_enabled` feature flags (both default off). WP5.5–WP5.6 are planned but not started.

**Goal:** establish organization-owned private symbol drafts, an organization review lifecycle, and a single authoritative public-visibility floor that every existing public reader — HTTP routes and background agent readers alike — enforces, before any private row can exist.

**Architecture:** one additive PostgreSQL migration plus matching SQLAlchemy models and review tables (delivered in WP5.1), then a staged migration of every public reader onto one authoritative projection (`active_public_symbol_projections`), then organization-private draft/review services and APIs, then the necessary frontend journeys, then a whole-stage audit. No private row, activation, or demotion is authorized by this plan.

**Tech stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Alembic/PostgreSQL, React 19, React Router, pytest, Docker-backed disposable PostgreSQL for migration tests.

**Controlling product sources:**

- `docs/Symbol Set Management Spec v0.3.md`
- `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md`
- Programme plan: `docs/2026-08-10-symbol-set-management-implementation-plan.md`, §11 ("Stage 5 — organization-owned symbols, organization review, and visibility foundation") remains controlling except where this repository-grounded plan makes implementation mechanics explicit.
- `docs/plans/2026-08-22-symbol-set-management-stage4-implementation-plan.md` — Stage 4 baseline this stage builds on.

**Authority note:** this file is documentation only. It does not itself authorize a commit, push, real/shared database migration, deployment, service restart, feature activation, publication, withdrawal, or destructive cleanup. Each work package still requires its own explicit go-ahead before code changes; disposable local tests (e.g. the Docker-backed PostgreSQL harness already used by `tests/test_organization_symbol_postgresql.py`) are fine to run without separate authorization.

---

## 1. Repository baseline and current-state evidence

Baseline captured on 2026-09-01:

- Repository: `/docker/openclaw-hz0t/data/symgov`
- Branch: `main`, `HEAD` = `e464ef8778eda8bd4f6436db64931ca32754524d`
- Alembic sole head: `20260829_0033`
- Tracked tree: clean

Verified seams:

- `backend/symgov_backend/models/schema.py:705-731` — `GovernedSymbol` carries `owner_organization_id`, checked `visibility` (`organization_private | public`, default `public`), and `organization_wide` (default `false`, checked `not organization_wide or owner_organization_id is not null`).
- `backend/symgov_backend/models/schema.py:851-925` — `OrganizationSymbolReviewSubmission` and `OrganizationSymbolReviewDecision` exist with immutable actor/timestamp/rationale fields.
- `backend/alembic/versions/20260829_0033_organization_symbol_visibility.py` — defines `active_public_symbol_projections`, the append-preserving history guards (including `TRUNCATE`), and the four review-binding validator triggers, each schema-qualified with a pinned `search_path` (`pg_catalog, public, pg_temp`) so a caller-created temp table cannot shadow the authoritative tables during deferred validation. An advisory-lock trigger (`serialize_organization_symbol_review_binding`) serializes concurrent writes to the same governed symbol's review bindings.
- `backend/symgov_backend/public_symbol_eligibility.py:7-21` — `PUBLIC_SYMBOL_ELIGIBILITY_SQL` currently checks `pk.status='published' AND pk.audience='public' AND sr.lifecycle_state='published'` and does **not** check `visibility='public'`. Consumed by `symbol_set_service.py` (3 call sites).
- `backend/symgov_backend/published_catalog.py:6-41` — `PUBLISHED_SYMBOLS_SQL` is the same shape, also missing a `visibility` check. Consumed by `catalog_search.py` (1 site), `routes/published.py` (4 sites), `routes/catalog.py` (3 sites) — 8 call sites total across 3 files, all importing the one shared constant.
- `scripts/run_hannah_curation.py:255-272` and `scripts/run_whitney_market_intelligence.py:75-110` — each restates its own independent copy of the same predicate (`pk.status='published' AND pk.audience='public' AND sr.lifecycle_state='published'`) directly against `governed_symbols`/`published_pages`, with no shared import and no `visibility` check. These are background agent readers (Hannah curation, Whitney market intelligence), not HTTP routes, and are easy to miss in a route-only audit.
- No `presigned`/`signed_url`/raw storage URL pattern was found in `backend/symgov_backend`; asset preview/download appears to go through authorized app routes (`routes/published.py:729`, `:775`) rather than durable public object URLs — this still needs confirming for every asset path as part of WP5.2c, not assumed.

## 2. Work-package sequence

1. **WP5.1 — schema/ORM and authoritative public projection foundation.** ✅ Complete, committed at `e464ef8`.
2. **WP5.2 — visibility-floor migration.** Split into three sub-packages (below) because the reader surface has three distinct risk profiles: request-authorized HTTP routes, unauthenticated background agent readers, and asset delivery.
3. **WP5.3 — organization-private draft/revision/intake/asset service and API.**
4. **WP5.4 — organization-review lifecycle** (session-derived reviewer, revision-specific approve/reject/request-changes, stale-decision conflict, organization-wide eligibility).
5. **WP5.5 — necessary frontend draft/review journeys** (Workspace + Reviews only; Standards View stays published-only per the product split).
6. **WP5.6 — whole-stage visibility-floor audit**, then Contract Review and Security Review on identical hashes. Activation, the first private row, and operational rollout remain separately authorized beyond this plan.

Each package is serialized. WP5.3+ may not begin — and no private row may exist — until WP5.2 is complete and proven on every reader in this plan.

### WP5.2a — public HTTP route migration

Scope: `public_symbol_eligibility.py`, `published_catalog.py`, `catalog_search.py`, `routes/published.py`, `routes/catalog.py`, `symbol_set_service.py`.

- Add `visibility = 'public'` (or an equivalent join against `active_public_symbol_projections`) to `PUBLIC_SYMBOL_ELIGIBILITY_SQL` and `PUBLISHED_SYMBOLS_SQL`. Prefer joining the view over restating predicates a second time, per the programme plan's explicit instruction that readers "join this view rather than restating weaker raw-table predicates."
- Because both predicates are each defined once and imported everywhere, this is a small, high-leverage change — but every one of the 8 `PUBLISHED_SYMBOLS_SQL` call sites and 3 `current_public_symbols` call sites needs a regression proving a matching `organization_private` row is excluded, not just that public rows still return.
- Acceptance: a disposable-PostgreSQL regression per route family (list/detail/search/facet/count/page/package/download/asset/Favorites/aliases) proving two-organization non-disclosure, plus the existing published/catalog test suites passing unmodified in behavior for legacy public rows.

### WP5.2b — background agent reader migration

Scope: `scripts/run_hannah_curation.py`, `scripts/run_whitney_market_intelligence.py`, and an inventory pass over `backend/symgov_backend/runtime.py` and `backend/symgov_backend/workspace.py` for any other direct `governed_symbols`/`published_pages` reads that don't go through the shared predicates above.

- Replace each independently-restated predicate with a join against `active_public_symbol_projections` (or the corrected shared SQL from WP5.2a), not a locally patched copy — the whole point of this sub-package is to stop these readers drifting from the HTTP-route predicate a second time.
- Acceptance: a regression proving Hannah's curation-candidate query and Whitney's coverage/clarification queries exclude an `organization_private` symbol that otherwise matches every other eligibility condition.
- This sub-package is the one most likely to be forgotten in a route-only audit — flag it explicitly in any WP5.2 handoff or review card.

### WP5.2c — asset delivery verification

Scope: confirm every preview/download/thumbnail path (`routes/published.py:729,775`, and the equivalent in `routes/catalog.py`/`routes/workspace.py`) serves bytes through an authorization-aware app route rather than a durable public object URL, per the programme plan's explicit gate ("demotion remains disabled until objects are private or only short-lived, revocable authorization-derived URLs are issued").

- This is a verification/inventory task first; only add short-lived signed URLs if a durable public URL is actually found.
- Acceptance: a written inventory of every asset delivery path and how each one authorizes access, checked into this plan's WP5.6 audit evidence.

## 3. WP5.3 — organization-private draft/asset service and API

Per programme plan §11: active Organization Admin or member with `contributor` capability creates an owner-bound private draft in the active organization only; draft/submitted metadata and assets are visible only to the creator, active Organization Admins, and active appointed Organization Reviewers. Deterministic intake/asset validation reuses Stage 4's existing patterns; no LLM decides persistence authority.

Lower relative risk: the capability plumbing (`OrganizationMembership`, `OrganizationMemberCapability`, `contributor`/`symbol_reviewer`) already exists from earlier stages. Mainly new deterministic intake/validation code plus authorization checks.

## 4. WP5.4 — organization review lifecycle

Session-derived appointed reviewer; revision-specific approve/reject/request-changes; stale-decision conflict; no publication side effect; fresh revision required after mutation; organization-wide eligibility only for an organization-approved current revision.

**Complexity note:** WP5.1 needed a security correction (advisory-lock serialization, `SEC-WP51-002`/`SEC-WP51-003`) to close a concurrency race in a comparatively simple binding-validation trigger. WP5.4's approve/reject/request-changes state machine has more transitions and the same deferred-trigger validation pattern; budget for a concurrency-focused test pass as a first-class part of this package rather than a post-hoc correction.

## 5. WP5.5 — frontend draft/review journeys

Per `CLAUDE.md`: preserve the product split. Draft/review UI belongs in Workspace (operator/processing visibility) and Reviews (SME review ergonomics); Standards View stays published-only. No Product Stage 6+ Catalog UI expansion in this package.

## 6. WP5.6 — whole-stage audit

Re-verify every reader inventoried in WP5.2 (including WP5.2b's background readers) against the frozen final hashes, then obtain Contract Review and Security Review on identical bytes. Activation, the first private row, and operational rollout are separately authorized beyond this plan — this package proves readiness, it does not turn anything on.

## 7. Global prohibited side effects (applies to every package above)

No commit, staging, push, shared/real migration, deployment, service/gateway restart, feature activation, first private row, private-data import/backfill, publication, withdrawal, demotion, external messaging, credential change, or unrelated edit, unless a specific step above and the human authorizing it says otherwise.
