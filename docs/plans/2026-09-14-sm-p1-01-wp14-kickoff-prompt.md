# SM-P1-01 WP1.4 kickoff prompt

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This is the restart pack for the **frontend** of SM-P1-01: WP1.4, the semantic
review surface. The controlling plan —
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — is
now committed, its §4 decisions are all resolved, and WP1.0 through WP1.3 are
delivered and committed. The new session's job is to execute one work
package, not to plan.

**This pack differs from the last one in the way that matters most: the
working tree is clean.** The four backend packages are committed. There is no
uncommitted foundation to preserve this time.

---

## 1. Frozen baseline (captured 2026-09-14)

- Branch `main`, `HEAD` = `3f09fb0` ("docs: record the SM-P1-01 plan and WP1.0-WP1.3 delivery notes").
- **`main` is 4 commits ahead of `origin/main` and has not been pushed.** `git rev-list --left-right --count origin/main...main` → `0	4`. Pushing is a separate operation needing its own approval.

| Commit | Package |
|---|---|
| `3f09fb0` | docs: the plan and WP1.0–WP1.3 delivery notes |
| `b4ffbaa` | feat: the semantic and rights review API (WP1.2, WP1.3) |
| `b5b18af` | feat: cross-target semantic review queue queries (WP1.1) |
| `fb0f61c` | feat: structured classification on organisation promotion (WP1.0) |
| `b157e04` | (previous baseline) |

- Sole Alembic head: `20260911_0057`. No package in SM-P1-01 has added a migration and WP1.4 must not either.
- Production runs release `stage11-56677a9`. **Four commits and the whole semantic review API are undeployed.**
- **Backend regression baseline: 3944 passed, 3 skipped, 3 deselected** (portable partition, Postgres included, ~1400s). No work package may lower this.
- **Frontend baseline: 272 tests, 43 suites, 0 failures** via `npm run test:frontend` (~9s). `npm run build` succeeds: 83 modules, `dist/assets/index-*.js` 613.50 kB (there is a pre-existing >500 kB chunk-size warning; it is not yours).

### 1.1 The only working-tree items are not yours

`git status --porcelain` should show exactly:

```
 M .claude/settings.local.json
?? UI-Design/
```

Both predate this programme. **Leave both untouched.** If anything else
appears, stop and reconcile before editing.

### 1.2 Test invocations

Frontend: `npm run test:frontend` (wraps `node --test frontend/src/*.test.js`,
default 120s bound, raise with `SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS`).

Backend, if you touch it: `scripts/test-backend.sh` bounds the portable
partition at `timeout 300s`, which expires at ~53% on this host. That is a
harness limit, not a product regression. Use:

```
PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest tests \
  --ignore=tests/test_daisy_rights_review_coordination.py \
  --ignore=tests/test_dxf_phase1.py --ignore=tests/test_libby_duplicate_triage.py \
  --ignore=tests/test_libby_symbol_vision.py --ignore=tests/test_zip_phase2.py \
  -m "not external_workspace" -q
```

It takes ~23 minutes. Run it in the background and stamp the identity
(`git rev-parse HEAD` plus `sha256sum` of every changed path) into the same
output, so the result cannot later be attached to different bytes.

---

## 2. What WP1.4 is

From plan §2, with §4's decisions applied:

> A standalone semantic review surface at its own route (Q1), behind the flag.
> Queue list plus a detail panel showing, for one symbol revision:
> primary/qualifier/component assignments, classification assignments per
> scheme, external mappings, evidence JSON and status, each with its decision
> control. Keeps the existing visual language and responsive behaviour;
> accessible controls, visible focus states, semantic labels, keyboard-
> navigable queue. Human-readable symbol IDs and operator-readable timestamps
> prominent. Per Q6, `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE`
> render read-only: existing assignments are visible, no control creates one.
> Tests in the existing `frontend/src/*.test.js` pattern, run by
> `npm run test:frontend`.

**The §16.1 criteria this closes with WP1.2/WP1.3:** "The review workflow can
see proposed semantic/classification/source assertions with evidence and
status", and "No organisation-private symbol existence is revealed by public
semantic endpoints."

---

## 3. Required reading before writing anything

1. `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — the controlling plan, now committed. §2 (the sequence, with WP1.0–WP1.3 delivery notes), §4 (all six decisions), §6 (prohibited side effects).
2. `backend/symgov_backend/routes/semantic_review.py` — **read the module docstring in full.** It states the §14.2 tenant rule, why 404 rather than 403, why propose routes exist alongside decide routes, and the Q6 enforcement. WP1.4 must not re-decide any of it.
3. `backend/symgov_backend/semantic_review.py` — the capability functions. `mustRepropose` and `blockedReason` are computed server-side precisely so the UI does not infer them.
4. `frontend/src/App.jsx` — 7704 lines. The `SideRail` (`:759`), the `Routes` block (`:532-553`), and `RightsReviewPage` (`:4153`).
5. `CLAUDE.md` (repo root) — governs every session. Its UI section is directly on point.

---

## 4. Load this Hermes skill

`symgov-feature-implementation`
(`/root/.hermes/profiles/symgov/skills/symgov/symgov-feature-implementation/SKILL.md`)
— strict vertical-slice TDD, interruption-safe checkpoints,
evidence-separated verification. Its **Frontend State Contract** section is
the relevant part. Do **not** load `symgov-programme-planning` (the plan
exists) or `symgov-release-operations` (nothing here deploys).

---

## 5. Measured frontend facts — do not re-derive these

All confirmed against the live repository on 2026-09-14.

**There is already a `/rights` route, and it is not yours.** `App.jsx:546`
mounts `RightsReviewPage` behind `RequireAnyRole roles={['admin','reviewer']}`,
and `SideRail` renders a "Rights" rail item for the same roles. That surface is
the **legacy intake** rights lane (`provenance_rights_review`), a different,
intake-scoped domain from `rights_records`. WP1.3's API is not a replacement
for it and WP1.4 must not repoint it. Pick a distinct route.

**Routing.** Routes are declared inline in `App.jsx`'s `<Routes>` block
(`:532-553`), except the two admin ones, which `adminRoutes.js` returns as an
array of `createElement(Route, …)` from `adminRouteElements(auth, RequireAuth)`
and `App.jsx:541` splices in. `adminRoutes.js` is 37 lines and is the
precedent to follow for a new gated surface — it keeps the route out of the
7704-line file.

**Role gating.** `RequireAnyRole roles={['admin','reviewer']}` is the existing
wrapper and matches WP1.2/WP1.3's backend boundary exactly.

**Capability gating.** `adminJourneys.js` is the pattern: a pure predicate per
surface (`canAccessPlatformAdmin`), plus an `*Access` component that renders
`children` or a `denied(requiredRole)` empty state. `projectContext.js:49`
(`canMountOrganizationSymbolDrafts`) is the closest precedent for a
*feature-flag* gate — it checks session purpose, session mode, active
organization, organization/session agreement, and then
`user.capabilities.organizationSymbolsEnabled === true`.

**Your flag is already on `/auth/me`.** `capabilities.semanticReviewEnabled`,
added by WP1.2 (`routes/auth.py`). Unlike `symbolSetsEnabled` it is **not**
gated on organizations or on an organization-bound session, because semantic
review is platform governance — the router's own guard is the authority on
reachability. A personal-mode session is legitimate here and sees public
symbols only.

**Side rail.** `SideRail` (`App.jsx:759`) computes `canReview =
hasAnyRole(user, ['admin','reviewer'])` and renders `RailNavLink` items. New
items go in the primary `rail-nav` or a further `rail-nav-admin` group;
`RailNavLink` takes `{ to, label, icon, end }` and icons are keyed strings.

**API client.** `frontend/src/api.js` (1838 lines) exports flat
`async function` helpers that call `requestJson(path, options)`. `requestJson`
returns `{ ok, mode, status, message, payload }` — **it does not throw on a
non-2xx**, and it returns `mode: 'unconfigured'` with `ok: false` when
`appConfig.apiRoot` is unset. Several surfaces render a seeded/demo state off
that, which is why `RightsReviewPage` has a `mode: 'seeded'` branch.

**Tests.** `node --test` with `react-dom/server` for rendered markup; there is
no browser DOM harness. Components that must be rendered in tests are written
as `.js` with `React.createElement` (`OrganizationAdminPage.js`,
`PlatformAdminPage.js`), not `.jsx` — `.jsx` is not importable by the test
runner. 73 files in `frontend/src`, 28 of them `*.test.js`.

**Styling.** `frontend/src/styles.css` (84 kB built) holds the shared visual
language; `catalogDeveloper.css` is the only other stylesheet. Reuse existing
tokens and classes — `workspace-empty-state`, `eyebrow`, `primary-button`,
`form-message`, `rail-nav` — rather than introducing a new vocabulary.

---

## 6. The API WP1.4 consumes — 17 operations, all measured

All at `settings.api_prefix` (`/api/v1`), **v1-only, no `/api` twin**, all
behind `SYMGOV_SEMANTIC_REVIEW_ENABLED` (default off → every one answers
`404`, not `403`). All declare `401, 403, 404, 422`.

**Reads — `admin` or `reviewer`:**

| Operation | Response |
|---|---|
| `GET /semantic-review/queues/symbol-classifications` | `SymbolClassificationQueueResponse` |
| `GET /semantic-review/queues/symbol-semantic-assignments` | `SymbolSemanticAssignmentQueueResponse` |
| `GET /semantic-review/queues/concept-classifications` | `ConceptClassificationQueueResponse` |
| `GET /semantic-review/queues/concept-external-mappings` | `ConceptExternalMappingQueueResponse` |
| `GET /semantic-review/queues/rights-records` | `RightsRecordQueueResponse` |
| `GET /semantic-review/symbol-revisions/{symbol_revision_id}` | `SymbolRevisionSemanticStateResponse` |

Queue query parameters: `status` (default `proposed`), `method` — or
`determinationMethod` on the rights queue — `schemeCode` (not on
symbol-semantic-assignments or rights-records), `limit` (default 50, **max
200, rejected with 422 above it, not clamped**), `offset` (default 0).

Queue pages carry `{ items, limit, offset }` and **no `total`**. That is
deliberate: the underlying queries have no COUNT behind them, so a total
would be a number the API cannot compute. Do not render a total or a page
count; render "showing N" and a next/previous control driven by `offset`.

**Writes — `admin` or `reviewer`:**

| Operation | Request | Response | Status |
|---|---|---|---|
| `POST /semantic-review/symbol-revisions/{id}/semantic-assignments` | `SymbolSemanticAssignmentProposeRequest` | `SymbolRevisionSemanticStateResponse` | 201 |
| `POST /semantic-review/semantic-assignments/{id}/decision` | `SemanticReviewDecisionRequest` | `SymbolRevisionSemanticStateResponse` | 200 |
| `POST /semantic-review/symbol-revisions/{id}/classifications` | `SymbolClassificationProposeRequest` | `SymbolRevisionSemanticStateResponse` | 201 |
| `POST /semantic-review/symbol-classifications/{id}/decision` | `SemanticReviewDecisionRequest` | `SymbolRevisionSemanticStateResponse` | 200 |
| `POST /semantic-review/concepts/{id}/external-mappings` | `ConceptExternalMappingProposeRequest` | `ConceptExternalMappingListResponse` | 201 |
| `POST /semantic-review/external-mappings/{id}/decision` | `ExternalMappingDecisionRequest` | `ConceptExternalMappingListResponse` | 200 |
| `POST /semantic-review/rights-records` | `RightsRecordProposeRequest` | `RightsRecordReviewRowResponse` | 201 |
| `POST /semantic-review/rights-records/{id}/decision` | `RightsRecordDecisionRequest` | `RightsRecordReviewRowResponse` | 200 |

**Writes — `require_platform_admin` only (concept lifecycle, §17):**

| Operation | Request | Response | Status |
|---|---|---|---|
| `POST /semantic-review/concepts` | `SemanticConceptCreateRequest` | `SemanticConceptRevisionResponse` | 201 |
| `POST /semantic-review/concepts/{id}/revisions` | `SemanticConceptRevisionCreateRequest` | `SemanticConceptRevisionResponse` | 201 |
| `POST /semantic-review/concept-revisions/{id}/transition` | `SemanticConceptRevisionTransitionRequest` | `SemanticConceptRevisionResponse` | 200 |

**A write returns the affected entity's whole state, not a page.** A decision
or proposal on a symbol revision returns that revision's complete
`SymbolRevisionSemanticStateResponse`, so the detail panel can re-render from
the response without a second fetch. The concept-mapping writes return the
concept's whole mapping list, **including the row just decided** — it will not
be `proposed` any more, and it must still be shown.

### 6.1 Two response fields that drive the UI

`capabilities` on every row: `{ canVerify, canReject, canRetire,
mustRepropose, blockedReason }`. **Render controls from these, never from the
status.** `mustRepropose: true` with a `blockedReason` means the row can never
be verified — offer "Reject and re-propose", not a disabled approve button.
Two populations are permanently in this state and they are the two that
matter: every `legacy_backfill` classification (§12.3 — all 132 rows SM-P0-10
backfilled in production) and every `ai_assisted` rights determination (§8.4 —
which is every rights record production currently holds).

`symbol` on every symbol-targeted row: `{ governedSymbolId, catalogSymbolId,
canonicalName, slug, visibility }`. **`catalogSymbolId` is the label** —
`S-000001`, not the UUID (`CLAUDE.md`). It is `null` for an
organisation-private symbol that has never been published; fall back to
`canonicalName`, never to the UUID. `ownerOrganizationId` is deliberately not
in the response.

### 6.2 The error envelope

Non-2xx bodies are `{"error": ..., "detail": ...}`, and 422 additionally
carries `issues: [...]`. `error` is `not_found` for 404 and
`validation_error` for 422. Every governance refusal — §8.4's
`ai_assisted` bar, a missing approval reason, a missing licence reference, a
`legacy_backfill` verification, an `exact` mapping verified on
`string_similarity`, a proposal into a read-only scheme — arrives as a **422
with a human-readable `detail`**. Surface that `detail`; it is written to be
read by a reviewer.

---

## 7. Decisions already made — do not re-litigate

- **Q1: a standalone semantic review queue at its own route.** Not hung off `ReviewCase`; the 132 backfilled rows have none. WP1.5 covers the "in Workspace/organisation review" half with a read-only embed, so there is exactly one audit path for a decision.
- **Q2: authorization split.** Concept lifecycle is Platform Admin; everything else is `admin` or `reviewer`.
- **Q3: `SYMGOV_SEMANTIC_REVIEW_ENABLED`, default off**, surfaced as `capabilities.semanticReviewEnabled`.
- **Q6: `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` are read-only in v1.** Existing assignments render; no control creates one. The API enforces this too — a proposal into one returns 422 — so the UI and the server agree.
- **Q7: v1-only.** There is no `/api` twin to fall back to.
- **Q8: no step-up.** Do not build a PIN re-entry flow for any of these acts.

If implementation evidence genuinely contradicts one, raise it rather than
deciding unilaterally, and record the outcome in the plan's §4.

---

## 8. Deliberately out of scope — record, do not fix

- **The existing `/rights` surface** — legacy intake lane, different domain. Not yours to change.
- **Industry/Application and process-category vocabularies** — Chris is researching both; no scheme is seeded by any package here.
- **`classification_records.industry`** — logged as a defect 2026-09-11; four hard-coded values, three of them discipline names.
- **Catalogue facet over-matching** — `catalog_search.py:125` ORs an unconditional `payload_json` ILIKE into every facet, so `Equipment` and `Process` match all 84 published symbols. Pre-existing and unassigned.
- **§7.3 `SemanticConceptRelationship`** — no table, in no §15.1 row. Its own package, before SM-P1-04.
- **`publication_gate_evaluations` is write-only** — SM-P1-06's data source. WP1.4 may surface one revision's latest evaluation read-only; it must not add a dashboard.
- **M3 provisional concept candidates** — not in this package.
- **`ConceptTerm` and organisation-scoped concepts** — deferred by §17 decision, not rejected.
- **WP1.5** — the read-only embed into the existing review surfaces — and **WP1.6**, the acceptance pass. Separate packages.

---

## 9. Prohibited side effects

- No `npm run build:publish`, `npm run publish:static`, deployment, service restart, live migration, or **feature activation** without Chris's explicit approval for that specific operation. `SYMGOV_SEMANTIC_REVIEW_ENABLED` stays off. `npm run build` and `npm run test:frontend` are fine.
- **No push.** `main` is already 4 commits ahead of `origin/main`; that gap is deliberate and pushing needs its own approval.
- No commit without explicit approval.
- No migration — the head stays at `20260911_0057`.
- No change to WP1.1's tenant-scope decisions or WP1.2's 404-not-403 rule without raising it as a question first.
- Leave `.claude/settings.local.json` and `UI-Design/` untouched.

---

## 10. Test-artifact inventory

| Path | New/extend | Covers |
|---|---|---|
| `frontend/src/semanticReview.test.js` | new | queue rendering, capability-driven controls, the `mustRepropose` path, empty/loading/error/unconfigured states |
| `frontend/src/semanticReviewApi.test.js` | new | request shape per helper — method, URL, query string, body — against the real helper, not a mock |
| `frontend/src/semanticReviewMountedJourney.test.js` | new | the route actually mounts for `reviewer` and for `admin`, and is absent when the capability is false |

Names are a suggestion; the `*.test.js` suffix and the `frontend/src`
location are not — `scripts/test-frontend.sh` globs exactly
`frontend/src/*.test.js`.

The skill's own warning applies here with force: a passing source-shape or
export test is **not** evidence that a journey is reachable. Prove the
mounted route renders for each required role through the real application
router, and prove the wire contract at the API-helper boundary by inspecting
method, URL, headers and body — a mocked component call proves neither.

---

## Prompt

> Continue SM-P1-01 in `/docker/openclaw-hz0t/data/symgov`, executing WP1.4 (the semantic review frontend surface).
>
> Read `docs/plans/2026-09-14-sm-p1-01-wp14-kickoff-prompt.md` first — it is the restart pack. Unlike the last two packs the working tree is **clean**: WP1.0–WP1.3 are committed at `3f09fb0`, and `main` is 4 commits ahead of `origin/main` and deliberately unpushed. The only working-tree entries should be the modified `.claude/settings.local.json` and the untracked `UI-Design/`; both predate this programme and neither is yours. Then read the controlling plan `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` in full (§4's decisions are all resolved — do not re-litigate them), then the module docstring of `backend/symgov_backend/routes/semantic_review.py`, then `CLAUDE.md`.
>
> Load the Hermes skill `symgov-feature-implementation`. Do not load `symgov-programme-planning` or `symgov-release-operations`.
>
> Re-verify the git and Alembic baseline before assuming the pack is accurate. The baselines to hold are **3944 passed / 3 skipped / 3 deselected** on the backend portable partition and **272 passed / 0 failed** on `npm run test:frontend`, with `npm run build` succeeding.
>
> **Every decision is already made — there are none outstanding and none to ask me about.** The pack's §6 carries the complete measured API surface: 17 operations, v1-only, all behind a default-off flag, queue pages with `{items, limit, offset}` and deliberately **no total**, writes returning the affected entity's whole state, and per-row `capabilities` that the UI must render controls from rather than inferring them from status. Its §5 carries the measured frontend facts — including that a `/rights` route already exists for the **legacy intake** lane and is not yours to repoint. Do not re-derive either.
>
> Execute WP1.4 as vertical slices with strict TDD. Hold §9's prohibited side effects as invariants: the feature flag ships default-off and is never activated, no migration, no commit, **no push**, no deployment, no service restart, and no `build:publish` or `publish:static`. Stop at repo-side completion of WP1.4; WP1.5 and WP1.6 are separate packages.
