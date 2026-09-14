# Semantic Model & Classification — SM-P1-01 Semantic Review Implementation Plan

> **For Hermes / OpenClaw orchestration and Claude Code:** this is the one committed, controlling plan for SM-P1-01. Ephemeral `/tmp` manifests, restart prompts, and review verdicts produced by either orchestration layer are session-scoped evidence, not sources of truth — reconcile against this file and the current repository state (`git log`, `git status`, Alembic head) before resuming or dispatching a work package. If a `/tmp` artifact and this file disagree, this file wins; update this file rather than trusting a stale scratch manifest.

**Status:** ACTIVE — §4 resolved by Chris on 2026-09-12; all six decisions confirmed on the recommendation. The §2 sequence is final. Each work package still requires its own explicit go-ahead before code changes.

**Controlling product sources:**

- `docs/SymGov_Semantic_Model_Classification_Change_Specification_v0.1.docx` — §15.2 row `SM-P1-01` ("Semantic review UI — Review concept assignment, qualifiers, external mappings and evidence in Workspace/organisation review", effort M), §16.1 acceptance ("The review workflow can see proposed semantic/classification/source assertions with evidence and status"), §9.2 (publication gate dimensions), §12.3 (existing published symbols keep `legacy_backfill`/`proposed` status until reviewed), §14.2 (private/public boundary), §14.4 (audit and retention).
- §17 decision register, approved in full by Chris on 2026-09-09: **concept governance authority is platform-level initially**; organisation-scoped concepts deferred, not rejected. Multilingual `ConceptTerm` deferred. No graph database for P0/P1.
- `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md` — plan-format precedent (this file mirrors its structure).
- `docs/plans/2026-09-11-classification-industry-field-defect.md` — the Industry/Application and process-category vocabulary gaps. **Chris is researching both vocabularies separately; they are explicitly out of scope for SM-P1-01** and no scheme is seeded by any work package here.

**Authority note:** this file is documentation only. It does not itself authorize a commit, push, real/shared database migration, deployment, service restart, feature activation, live Hermes manifest/profile change, or destructive cleanup. Each work package still requires its own explicit go-ahead before code changes.

---

## 1. Repository baseline and current-state evidence

Baseline captured 2026-09-12, at the start of this planning session:

- Repository: `/docker/openclaw-hz0t/data/symgov`. Branch `main`, `HEAD` = `b157e04` ("refactor: split the backfill's rewrite count into label and column changes"). Local `main` and `origin/main` in sync (0 ahead, 0 behind). Working tree clean except the pre-existing `.claude/settings.local.json` diff and untracked `UI-Design/` — both leave untouched, as in every prior stage's baseline.
- Alembic sole head: `20260911_0057` — verified by walking `down_revision` across all 60 migration files. SM-P0-07 through -10 added no migration; the head is exactly where SM-P0-08 left it.
- Backend regression baseline re-run independently this session, not quoted from an earlier package note: **3696 passed, 3 skipped, 3 deselected** in 1103s, portable partition with Postgres tests included.
- Production runs `stage11-56677a9`; `b157e04` (a report-field rename) is the only main commit not deployed.

### 1.1 SM-P1-01 is a routes-and-UI package, not a domain-logic package

Every governance act the review UI needs already exists as a tested service function, with vocabularies frozen as `CheckConstraint`s:

| Act | Function | Vocabulary |
|---|---|---|
| Create concept + first revision | `semantic_concepts.create_semantic_concept` | kinds: `physical_equipment`/`function`/`property`/`state`/`action`/… |
| Add / move a concept revision | `add_semantic_concept_revision`, `transition_semantic_concept_revision` | `draft`→`review`→`approved`→`published`→`deprecated`/`withdrawn` |
| Propose / decide a symbol→concept assignment | `symbol_semantic_assignments.propose_symbol_semantic_assignment`, `transition_symbol_semantic_assignment` | roles `primary`/`qualifier`/`component`; states `proposed`/`verified`/`rejected`/`retired` |
| Propose / decide a classification assignment | `classification_assignments.propose_symbol_revision_classification`, `transition_symbol_revision_classification` | roles `primary`/`secondary`; same four states |
| Propose / decide an external mapping | `concept_external_references.propose_concept_external_reference`, `transition_concept_external_reference` | types `exact`/`close`/`broader`/`narrower`/`related` |
| Propose / decide a rights record | `rights_provenance.propose_rights_record`, `transition_rights_record` | `proposed`/`approved`/`rejected`/`retired` |

No new table, column or constraint is required by WP1.0–WP1.6. §4 Q1 resolved to option (a), so **this package adds no migration at all**. The Alembic head stays at `20260911_0057`. This is the same shape SM-P0-07 had.

### 1.2 Nothing in the semantic model is reachable over HTTP, and five governance acts have no production caller

Confirmed this session by import and call-site grep, not inference:

- `routes/` contains no file that imports any semantic-model module. The only `grep` hits for "classification"/"rights" in `routes/workspace.py` are the legacy intake review stages (`classification_review`, `provenance_rights_review`), which are a different, intake-scoped domain.
- `frontend/src` contains **zero** references to the semantic model; the "semantic" matches in `catalogRoutes.js` are about URL paths.
- Zero production importers: `semantic_concepts`, `concept_external_references`. So no `SemanticConcept` row is ever created, and every symbol→concept assignment and external mapping is structurally unreachable.
- No production caller: `transition_rights_record`, `record_asset_transformation`, `transition_symbol_semantic_assignment`, `transition_symbol_revision_classification`. Nothing can verify an assignment, approve a rights record, or record asset lineage.
- `publication_gate.enforce_publication_gate` is live on both promotion paths but fires only on `package_type='authoritative_library'`; `source_package_acquisition.register_source_package`, the only creator of one, is called from tests only. The gate is `not_in_scope` for 100% of production traffic.

`publication_gate.py:1-50` already measures all of this in its own module header. Read it before re-deriving any of it.

### 1.3 The review population is 132 backfilled rows that have no `ReviewCase`

SM-P0-10's backfill applied **132 assignments across 73 of 96 symbols** in production on 2026-09-11, all `proposed` / `legacy_backfill` / `primary`, zero verified, zero catalogue values changed. These belong to already-published symbols, most of which have no open `ReviewCase` at all — the backfill deliberately targeted the union of `governed_symbols.current_revision_id` and `published_pages.current_symbol_revision_id`.

Every existing human review lane (`classification_review`, `raster_split_review`, `provenance_rights_review`, listed in `routes/workspace.py:148`) is intake-scoped: it hangs off a `ReviewCase` whose `source_entity_id` is a queue item, intake record or promotion request. A `legacy_backfill` assignment has none of those. This is the structural reason §4 Q1 is a real question and not an implementation detail.

Note also `ck_symbol_revision_classifications_backfill_not_verified`: a `legacy_backfill` assignment can **never** be verified by design (§12.3) — only rejected, and re-proposed with a real method. The review UI must present that as the actual available action, not offer an "approve" button the database will refuse. This was verified against the live production database on 2026-09-11.

### 1.4 The organisation promotion path writes no structured classification — a P0 defect found during this session's review

`apply_classification_mapping` has exactly two production call sites, both in `publication_handoff.py` (`ensure_approved_symbol_revision:595`, `ensure_approved_child_symbol_revision:882`) — the Rupert/Daisy intake path. `organization_promotion_handoff.execute_organization_promotion_handoff` enforces the publication gate at `:248` but never calls `record_classification_mapping` or `record_legacy_classification_sync`.

So an organisation-private symbol promoted to the public catalogue today arrives with free-text `category`/`discipline` only (≤128 chars, no vocabulary check, set at `organization_symbol_drafts.py:137-154`) and **no** `symbol_revision_classifications` row — invisible to the M5 catalogue assignment read that is switched on in production, and absent from any review queue SM-P1-01 builds. This is wider than the draft-time gap recorded in the SM-P0-09/-10 notes: it is the publish moment, not draft time. See §4 Q5.

### 1.5 Three seeded classification schemes have no writer and no reader

`USE-CASE` (`classification_schemes.py:103`), `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` (migration `20260909_0052`) appear nowhere else in `backend/symgov_backend/`. Only `ENGINEERING-DISCIPLINE` and `SYMBOL-CATEGORY-FAMILY` are mapped into by `classification_mapping.plan_classification_mapping` and read by `catalog_search`. A review UI that lets a human assign into a scheme is the first thing that could give these three a writer — but whether it should is a product question, not a coding one. See §4 Q6.

### 1.6 §7.3 `SemanticConceptRelationship` has no table and is in no work package

The spec describes it as P0-shaped ("Start with the small vocabulary needed for SymGov navigation and composite-symbol semantics, and extend deliberately") but it appears in no §15.1 P0, P1 or P2 row. It is why `classification_mapping.py:421` can only record `parentEquipmentClass` as a `no_relationship_table` gap. This is a missing P0 table, not a deferral, and it is **out of scope for SM-P1-01** — recorded here so it is not lost. It should become its own package before SM-P1-04 (representation comparison), which will want broader/narrower navigation.

### 1.7 `publication_gate_evaluations` is write-only

Every publication — in scope or not — records all six §13.1 dimensions and the §13.2 traceability level. Nothing reads the table: no accessor, no route, no CLI. It is SM-P1-06's data source and is already accumulating. Out of scope here; WP1.5 may surface a single revision's latest evaluation read-only if Q4 allows.

### 1.8 There is no cross-target "open proposals" query anywhere

`list_symbol_semantic_assignments` and `list_symbol_revision_classifications` are both per-revision. `list_concept_external_references` is per-concept. `find_classified_targets` is per-node. A review **queue** needs "every open proposal, newest first, filterable by status/method/scheme" across symbols — which does not exist and is the one piece of genuinely new service code in this package. It is WP1.1, deliberately first, because getting the queue query wrong makes every later package's tests wrong.

---

## 2. Work-package sequence (final — §4 resolved 2026-09-12)

Each package is a vertical slice with its own tests and its own go-ahead. Migration-free unless stated.

**WP1.0 — Organisation promotion dual-write repair** *(in scope per Q5, sequenced first)* — **DELIVERED 2026-09-12**
Call `record_classification_mapping` and `record_legacy_classification_sync` from `organization_promotion_handoff.execute_organization_promotion_handoff`, reusing `publication_handoff`'s existing wrapped-whole-and-per-item error shape so a mapping failure never blocks a promotion (SM-P0-07's rule). Tests: an org promotion produces assignments; a mapping failure still promotes; the legacy columns are unchanged when the only match is `legacy_taxonomy` (the `NON_DISPLAY_MATCH_BASES` rule must hold on this path too). Sequenced first so the review queue is not built over a systematically incomplete population.

*Ordering deviation from this plan's draft, recorded rather than made silently.* The draft said "after the gate and before the visibility flip". Implementation split the two calls instead:

- `record_classification_mapping` runs **before** `enforce_publication_gate`. That is the intake path's own ordering — `ensure_approved_symbol_revision` proposes at review-decision time and `runtime.py:2508` evaluates the gate later at publication — and §9.2's evaluation *reads* `symbol_revision_classifications`. Proposing after the gate would have recorded a classification gap on every organisation promotion that the revision does not actually have, writing false §13.1 dimensions into `publication_gate_evaluations` — which §1.7 identifies as SM-P1-06's data source. No live gate outcome changes: both paths create `submission_sheet` packages, so the gate stays `not_in_scope`. `enforce_publication_gate` itself is unmodified, per §6.
- `record_legacy_classification_sync` runs **after** the gate passes, before the visibility flip, because it rewrites the durable `GovernedSymbol.category`/`.discipline` display columns and a refused promotion must leave the organisation's own symbol as it found it. A proposed assignment left by a refused promotion is harmless and is reused on resubmission (`_existing_assignment`); a rewritten display column would not be.

*Closing evidence.* Focused: 5/5 in `tests/test_wp73_promotion_publication_handoff_postgresql.py` (2 pre-existing + 3 new). Adjacent: 413 passed across the 20 test files referencing the changed modules. Full portable partition on the final bytes: **3699 passed, 3 skipped, 3 deselected** in 1109s — baseline 3696 plus exactly the three new tests, no regression. Identity stamped with the run: base `b157e04`, `organization_promotion_handoff.py` `16ed5798…`, `publication_handoff.py` `ac141e8a…`, the test file `1d1f9d5f…`. No migration; Alembic head unmoved at `20260911_0057`. Not committed.

`record_classification_mapping` gained optional `discipline`/`category` parameters: the organisation path has no `ReviewSymbolProperty` and no `IntakeRecord`, and its reviewed values live on `GovernedSymbol` itself. `symbol_properties` still wins where it exists, so the intake path is byte-for-byte unchanged. The import is function-local — `publication_handoff` already imports `organization_promotion_handoff` to dispatch, so a module-level import closes a cycle; same idiom as `auth.py:351`.

**WP1.1 — Cross-target review queue queries** — **DELIVERED 2026-09-12**
New service module `semantic_review.py`, pure reads over existing tables. Open-proposal queries for symbol semantic assignments, symbol revision classifications, concept classifications, concept external references and rights records, each filterable by status/method/scheme and each returning the human-readable symbol identity (catalogue symbol ID and canonical name — **not** UUIDs, per `CLAUDE.md`) alongside the row. Includes the `legacy_backfill`-cannot-be-verified fact as an explicit per-row capability flag rather than leaving the frontend to infer it. No routes. Tests: ordering, filters, pagination bounds, the capability flag, and that a private symbol's rows never appear in a platform-public projection (§14.2).

*Delivered shape.* Five queue functions — `list_open_symbol_revision_classifications`, `list_open_symbol_semantic_assignments`, `list_open_concept_classifications`, `list_open_concept_external_references`, `list_open_rights_records` — each newest-first with an id tiebreak so paging stays stable across a backfill's many same-timestamp rows, and each bounded by `MAX_QUEUE_LIMIT` rather than silently clamped.

Four capability functions, not one: each governed table has its own transition table and its own bars, and §7.5 names `imported` where §7.9 names `source_mapping`. Two carry a permanent bar a reviewer cannot lift, and they are the two that matter in production — `legacy_backfill` classifications (§12.3, all 132 backfilled rows) and `ai_assisted` rights determinations (§8.4, which is every rights record production holds, since `propose_intake_rights_record` is the only creator). Both surface as `must_repropose` with a reason rather than a disabled approve button. All four derive from the vocabularies the transition services enforce, so a UI cannot offer a decision the service refuses; a parametrized test asserts that agreement across every status × method pair.

Two findings the tests turned up, both fixed:

- **Tenant scope is not uniform across the five.** The two symbol-targeted queues carry §14.2's predicate; the two concept-targeted ones deliberately do not, because a concept→node or concept→external-release assertion names no symbol and has no private existence to leak (§17 made concept governance platform-level, and §14.2's sentence is specifically about "the assignment from a private symbol revision to a concept"). Rights records are mixed: symbol-subject records are scoped, package- and standard-subject records are platform-level and returned at every scope, since withholding them would hide the records §9.2's rights dimension needs approved.
- **A concept's `current_revision_id` is set only on publication** (`transition_semantic_concept_revision`), and manual concept creation — which lands in this package — starts every concept as `draft`. Keying the queue's display name on the current revision rendered a nameless row for exactly the concepts a reviewer was queued to act on. The name now falls back to the newest revision, pinned by its own test.

*Closing evidence.* 65 focused tests (`test_semantic_review_queries.py` 27, `test_semantic_review_queries_postgresql.py` 38). Full portable partition on the final bytes: **3764 passed, 3 skipped, 3 deselected** in 1099s — the post-WP1.0 baseline of 3699 plus exactly the 65 new tests, no regression. No migration; head unmoved at `20260911_0057`. Not committed.

**Running regression baseline: 3764 passed / 3 skipped / 3 deselected.** No later work package may lower this.

**WP1.2 — Semantic review API** — **DELIVERED 2026-09-13**
New `routes/semantic_review.py`, registered behind `SYMGOV_SEMANTIC_REVIEW_ENABLED` (Q3, default off) with the same `csrf, session_access` dependencies as every other authenticated router. Read endpoints for the WP1.1 queues and for one symbol revision's full semantic state; write endpoints for concept create / revision add / revision transition, and for assignment, classification and external-mapping decisions. Authorization per Q2: concept lifecycle behind `require_platform_admin`; assignment, classification and external-mapping decisions open to `admin` or `reviewer`. Pydantic schemas in `schemas.py` following the existing house shape. Tests: an exhaustive route-policy matrix (unauthenticated, every denied role, every allowed role), vocabulary rejection, and that every write is attributed to the authenticated actor — never a service user.

*Amended 2026-09-13 by decision Q7.* This line originally called for "legacy `/api` prefix parity", written before the route inventory was measured. It is **v1-only**: every router added since Stage 4 (`projects`, `symbol_sets`, `symbol_context`, `organization_symbols`, `symbol_demotion`) is mounted at `settings.api_prefix` with no `/api` twin, and the six legacy routers are all pre-Stage-4 surfaces kept for existing clients. WP1.4's frontend will be this API's first consumer, so a legacy mount would be a compatibility surface for zero callers and would double the policy matrix. Decision Q8 at the same time: **no step-up re-authentication** (`require_recent_step_up`) on either package — every act here is a reversible governed transition with a succession model and a full audit trail, unlike `symbol_demotion`, which is the one surface that uses step-up.

*Delivered shape.* Fifteen routes on one router, mounted once at
`settings.api_prefix` behind `semantic_review_enabled` (default off, exposed
on `/auth/me` as `semanticReviewEnabled`), with the flag guard answering 404
rather than 403 so a dormant feature does not advertise itself. Five queue
reads, one revision-detail read, three platform-level concept lifecycle
writes, and six `admin`/`reviewer` writes. No `require_recent_step_up`
anywhere (Q8); no legacy `/api` twin (Q7); no migration — head unmoved at
`20260911_0057`.

Three decisions the implementation had to take, none of which §4 covers, all
recorded rather than made silently:

- **Propose routes ship alongside decide routes.** §2's line named
  "decisions", but a decision route alone is unreachable for two of the three
  targets: §1.2 measured zero production importers for `semantic_concepts`
  and `concept_external_references`, so no assignment and no mapping exists
  to decide on. For classifications the converse holds — 132 rows exist and
  every one is `legacy_backfill`, which WP1.1 surfaces as `must_repropose`
  with the instruction to "reject it and propose afresh with a real method",
  an instruction no route could otherwise carry out. Proposing writes nothing
  the P0 services did not already own, and §8.4 still holds: every assertion
  starts `proposed` whatever its method or confidence.
- **Q6 is enforced at the API, not only in the UI.** A proposal into
  `USE-CASE`, `DOCUMENT-TYPE` or `REPRESENTATION-TYPE` is refused with the
  validation envelope; existing assignments in those schemes stay readable.
  The decision is written as a statement about controls, and a control the
  API still honours is not read-only.
- **Writes return the affected entity's whole state, not a queue page.** The
  queue shape carries `limit`/`offset`, which a write never applied;
  reporting `limit=len(items)` would document a page that was never taken.

Two §14.2 consequences, since this is the first read route over these tables
and `publication_gate`'s "the boundary is untouched here" no longer holds:
the caller's tenant scope comes from `active_organization_id`, so a
personal-mode session is public-only and **Platform Admin is deliberately not
a bypass** (widening WP1.1's predicate for a role would change the boundary
rather than apply it); and every route naming a single row re-resolves it
through the same predicate, answering **404 rather than 403** for a row
outside scope, because a caller can tell those two apart.

*Closing evidence.* 144 new tests — `test_semantic_review_routes.py` 128
(policy matrix over all 15 routes × unauthenticated/denied-role/reviewer/
platform-admin, the flag guard, v1-only mounting, and the OpenAPI contract
verified operation-by-operation through `create_app().openapi()`),
`test_semantic_review_routes_postgresql.py` 16 (tenant isolation on real
joined rows, the §12.3 backfill refusal, reject-then-repropose, concept
lifecycle to `published`, exact-mapping verification refused on
`string_similarity`, actor attribution). Full portable partition on the final
bytes: **3908 passed, 3 skipped, 3 deselected** in 1248s — the WP1.1 baseline
of 3764 plus exactly the 144 new tests, no regression. Identity stamped
before and after the run and re-verified unchanged. Not committed; the flag
was never activated.

The portable file stubs the five queue reads. WP1.1's concept display name is
a correlated subquery whose `ORDER BY` references the outer
`semantic_concepts` row, which SQLite will not resolve — the same reason
`test_semantic_review_queries.py` is deliberately DB-free. Authentication,
the role dependencies, the flag guard and the application factory are all
real there; every execution assertion lives in the PostgreSQL file.

**Running regression baseline: 3908 passed / 3 skipped / 3 deselected.**

**WP1.3 — Rights record review API** *(in scope per Q4)* — **DELIVERED 2026-09-13**
Propose and decide endpoints for `rights_records`, including the reviewer's own proposal (today only `publication_gate.propose_intake_rights_record` can create one, and it writes `ai_assisted`, which is unapprovable by constraint). Tests: the `approved_disposition_basis` policy, the `ON DELETE RESTRICT` approver key, and an end-to-end proof that an approved permissive record satisfies the gate's rights dimension.

*Delivered shape.* Two routes on the same router as WP1.2 — `POST
/semantic-review/rights-records` and `POST
/semantic-review/rights-records/{record_id}/decision` — behind the same
default-off flag, under the same `admin`/`reviewer` boundary, with no step-up
(Q8) and no legacy twin (Q7). Same router deliberately: a second one would
mean a second flag, a second policy matrix and a second copy of the tenant
rule, for two routes that share every one of them. No migration.

Tenant scope follows WP1.1's split rather than inventing a second rule. A
symbol-subject record is scoped to the caller and an out-of-scope one is
reported absent, not forbidden; a package- or standard-subject record names
no symbol and no tenant and is reachable at every scope, because withholding
it would hide exactly the records §9.2's rights dimension needs approved.

One implementation detail worth recording, because it would otherwise turn
every retirement into a 422: `transition_rights_record` refuses a
`decided_by_user_id` on anything but an approval or a rejection — retirement
is a succession, not a judgement about the work — so the route passes the
authenticated actor only where the transition is actually a decision.

*Closing evidence.* 36 new tests. `test_rights_review_routes.py` 22 (policy
matrix on both routes, flag guard, v1-only mounting, the 422 envelope, and a
step-up check that walks the resolved dependency tree rather than grepping
source, since the module docstring names the dependency in order to say it is
absent). `test_rights_review_routes_postgresql.py` 13 — §8.4's refusal of an
`ai_assisted` approval, §7.12's who/when/why triple landing on the row, the
licence requirement, the disposition/status permission rule, decision-time
correction of status and licence, retirement carrying no decider, succession
retiring the previously approved record, the `ON DELETE RESTRICT` approver
key, and the §14.2 boundary on both routes with a refused proposal proved to
write nothing. `test_publication_gate_postgresql.py` +1, and it is the one
that proves the package's point: a revision satisfying every other §9.2
dimension is **refused** with `rights_undecided` while holding only the
`ai_assisted` intake proposal production actually writes; approving that
proposal through the API is refused; a reviewer's own record proposed and
approved over HTTP then makes the same gate return `permitted`, with the
rights dimension `satisfied` and **not** waived. `_satisfy_all_dimensions`
gained an `include_rights` keyword for it; every existing caller is unchanged.

The `ON DELETE RESTRICT` test deletes a user who has never logged in.
Deleting one who has trips the unrelated append-only guard on
`auth_login_attempt_events` first, which would have made the test pass for
the wrong reason.

Full portable partition on the final bytes: **3944 passed, 3 skipped, 3
deselected** in 1398s — the post-WP1.2 baseline of 3908 plus exactly the 36
new tests, no regression. Identity stamped before and after and re-verified
unchanged. Head unmoved at `20260911_0057`. Not committed; the flag was never
activated.

**Running regression baseline: 3944 passed / 3 skipped / 3 deselected.**

**WP1.4 — Semantic review surface (frontend)** — **DELIVERED 2026-09-14**
A standalone semantic review surface at its own route (Q1), behind the flag. Queue list plus a detail panel showing, for one symbol revision: primary/qualifier/component assignments, classification assignments per scheme, external mappings, evidence JSON and status, each with its decision control. Keeps the existing visual language and responsive behaviour; accessible controls, visible focus states, semantic labels, keyboard-navigable queue. Human-readable symbol IDs and operator-readable timestamps prominent. Per Q6, `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` render read-only: existing assignments are visible, no control creates one. Tests in the existing `frontend/src/*.test.js` pattern, run by `npm run test:frontend`.

*Delivered shape.* A standalone surface at `/semantic-review` (Q1), reached
from a new "Semantics" rail item, behind two gates in series that mirror the
two the API applies: `RequireAnyRole roles={['admin','reviewer']}` for
WP1.2/WP1.3's boundary, and a `semanticReviewEnabled` capability gate for the
default-off flag (Q3). The route lives in `semanticReviewRoutes.js` rather
than inline, following `adminRoutes.js`, so the 7700-line `App.jsx` gained 16
lines. The capability predicate is deliberately **not**
`adminJourneys.hasActiveOrganizationContext`: decision Q3 exposes the flag as
platform governance rather than an organisation entitlement, so a
personal-mode session is legitimate and the router's own section 14.2 scope
predicate — not a second, stricter UI rule — decides what it sees. The flag
was never activated; nothing was committed, pushed or deployed, and the
Alembic head is unmoved at `20260911_0057`.

Five queues as a tablist with a roving tabindex, an offset pager that reports
"showing N" and never a total (the WP1.1 queries carry no COUNT), and a focus
pane whose contents follow the row kind: a symbol-targeted row opens the
revision's whole state, and a concept-targeted one is reviewed in place.
Decision controls render from each row's `capabilities` and never from its
status — a mutation that ignored `canVerify` fails three tests, which is the
evidence that the rule is enforced rather than merely intended.

Four findings, all measured against the delivered API rather than inferred,
none of which section 4 covers:

- **A `legacy_backfill` classification cannot actually be re-proposed from
  any surface, and that is an API gap, not a UI omission.** `mustRepropose`
  instructs "reject it and propose afresh with a real method", but
  `POST /semantic-review/symbol-revisions/{id}/classifications` requires a
  `classificationNodeId`, and **no read in the 17-operation surface returns
  one**: `ClassificationReviewRow` carries `scheme_code`, `node_code` and
  `node_label` but no node id, `SymbolRevisionSemanticStateResponse` carries
  the same fields, and `/catalog/taxonomy` returns the free-text legacy
  facets, not `classification_nodes`. So WP1.4 renders the reject control and
  the blocked reason and says plainly that re-proposal is unavailable here.
  Closing the loop needs either `classificationNodeId` on the row or a
  node-lookup read — a WP1.2 amendment, and a decision for Chris, not one
  WP1.4 may take. The equivalent rights path has no such gap: the row names
  its own subject, so the reviewer's own record **is** proposable, which is
  the half section 8.4 actually blocks in production.
- **The 422 envelope's readable sentence is in `issues[].msg`, not
  `detail`.** `app.py`'s `validation_exception_handler` sets `detail` to the
  constant "Request validation failed." and puts the service's own sentence in
  `issues`. The helpers therefore prefer `formatValidationIssues(...)` and
  fall back to `detail`, which is what makes a section 12.3 or section 8.4
  refusal legible to a reviewer. Surfacing `detail` alone would have shown
  every governance refusal as a malformed request.
- **Concept classifications have no decision route.** WP1.2 ships five queue
  reads but four decision writes; `ConceptClassificationAssignment` has none.
  That queue is therefore rendered read-only with the reason stated, rather
  than given a control the API cannot honour.
- **External mappings are not in the revision detail, by the schema's own
  decision.** This plan's WP1.4 sentence lists them in the detail panel;
  `SymbolRevisionSemanticStateResponse` deliberately excludes them because a
  mapping hangs off a concept and choosing which of an assigned concept's
  mappings are "this revision's" would assert a relationship the model does
  not hold. They are reviewed in their own queue instead. Recorded as a
  deviation from this plan's wording, resolved on the API's reasoning.

Two smaller choices, recorded rather than made silently: the reviewer's rights
proposal form offers `manual` and `licence_document` only — the API still
accepts `ai_assisted`, but offering the one method section 8.4 can never
approve would be offering a self-defeating act on the surface whose purpose is
to clear that bar; and a successful proposal refreshes the queue through an
explicit token, because the page effect depends on the filter *values* and
re-cloning the filter object would not have refetched (caught by a test that
fails when the token is removed).

*Closing evidence.* 53 new frontend tests across four files —
`semanticReviewApi.test.js` 14 (method, URL, query string and body against the
real helpers, the v1 path, the 422-issues preference and the dormant-flag
404), `semanticReviewJourney.test.js` 8 (the capability predicate including
the personal-mode case and the denied state), `semanticReview.test.js` 25
(queue rendering, capability-driven controls, the two `mustRepropose`
populations, Q6's read-only marker, offset paging with no total, the tablist
keyboard contract, empty/loading/error states and the write-response
re-render), and `semanticReviewMountedJourney.test.js` 6 (the route mounted
through the real application router for `reviewer`, for `admin` and for a
personal-mode session, absent behind the flag, denied outside the role
boundary, and the legacy `/rights` lane left in place). `npm run test:frontend`:
**325 passed, 0 failed, 51 suites** — the 272 baseline plus exactly the 53 new
tests, no regression. `npm run build` succeeds: 86 modules (83 plus the three
new source modules), `dist/assets/index-*.js` 639.29 kB against a 613.50 kB
baseline; the >500 kB chunk-size warning is pre-existing. The backend was not
touched, so the **3944 passed / 3 skipped / 3 deselected** portable baseline
stands unchanged and was not re-run.

**WP1.2/WP1.4 amendment — making section 12.3's remedy reachable** — **DELIVERED 2026-09-14**, approved by Chris the same day

WP1.4 found that the instruction `mustRepropose` carries could be read but not
followed. `propose_symbol_revision_classification` takes a
`classificationNodeId`, and **no read on the delivered surface returned one**:
`ClassificationReviewRow` and `SymbolRevisionSemanticStateResponse` both carry
`schemeCode`/`nodeCode`/`nodeLabel`, which are display values, and
`/catalog/taxonomy` returns the free-text legacy facets rather than
`classification_nodes`. WP1.2's own
`test_a_reviewer_rejects_a_backfilled_row_then_proposes_afresh` passed only
because the *fixture* held the identifier; no client could take the second
step. So every one of the 132 production rows could be rejected and never
replaced.

Chris chose the two-step shape over an atomic `repropose` route on 2026-09-14.
The reason is the partial unique index
`uq_symbol_revision_classifications_active_node`, unique on
(`symbol_revision_id`, `classification_node_id`) while the status is
`proposed` or `verified`: re-proposing the **same** node must follow the
rejection and can never precede it, while a **different** node may be proposed
first. The gap between the two calls is recoverable — rejecting frees the node,
so the replacement can be proposed at any later moment and nothing is
destroyed — which is what made a third write route, with its own request
model, policy matrix and tenant rule, not worth adding to a delivered API.

*Delivered shape.* Two additions and one fix, no migration; head unmoved at
`20260911_0057`.

- `classificationNodeId` on `ClassificationReviewRow` and on
  `SymbolClassificationReviewRowResponse`, rendered by both the queue and the
  revision detail. It sits *alongside* the display fields and never replaces
  them (`CLAUDE.md`). Deliberately **not** added to
  `ConceptClassificationReviewRow`: that queue has a read and no decision or
  propose route, so a node identifier there would serve nothing.
- `GET /semantic-review/classification-schemes`, the sixteenth route on the
  same router, behind the same default-off flag and the same
  `admin`/`reviewer` boundary. It returns only
  `REVIEWER_ASSIGNABLE_SCHEME_CODES` — decision Q6 keeps the other three
  read-only and the propose route already refuses them, so offering them as
  choices would invite a refusal the caller could be spared. No pagination
  (the two schemes hold 11 and 20 nodes) and no tenant predicate (schemes and
  nodes are seeded platform reference data naming no symbol, so section 14.2
  has nothing to say about them). Only `active` nodes are offered.
- **A defect the amendment made reachable, and fixed with it.** A duplicate
  live node escaped `propose_symbol_classification` as a raw
  `IntegrityError` — an unhandled 500, not a refusal. It was unreachable
  before only because no client could name a node at all. It now wears the
  same 422 envelope as every other refusal on this router, matched on the
  index name so a genuine storage fault still surfaces as one.

*A sibling defect, measured and deliberately not fixed here.*
`propose_external_mapping` has the same shape:
`uq_concept_external_references_active_mapping` would escape as a 500 on a
duplicate active mapping. It is **not** reachable from any UI — WP1.4 ships
decision controls for external mappings but no propose control — so fixing it
would have widened an amendment beyond what its own change makes reachable.
Recorded here for scheduling rather than silently carried.

*Frontend.* The revision detail gained a "Propose a classification" form:
scheme and node pickers fed by the new read, `primary`/`secondary` role, and
the method fixed at `manual` rather than offered as a choice — a reviewer
picking a node is making a manual determination, and `legacy_backfill` is the
very thing being replaced. Nodes the revision already holds live are shown as
taken rather than offered and refused, mirroring the index predicate. The
queue's *filter* still offers `legacy_backfill`, because that is how a
reviewer finds the 132 rows.

*Closing evidence.* Backend: 258 passed across the six semantic and rights
test files, including four new PostgreSQL tests — the queue row naming the
node the assignment actually holds, the assignable-scheme read excluding all
three Q6 schemes, the full re-proposal journey run on **nothing but values the
API returned** (the test the old fixture-fed one could not be), and the
same-node collision refused as a 422 rather than a 500. Full portable
partition on the final bytes: **3958 passed, 3 skipped, 3 deselected** in
1476s -- the WP1.3 baseline of 3944 plus exactly the 14 new tests (two route
contract tests, eight parametrized policy-matrix instances the new route adds
to the existing table, and four PostgreSQL tests), no regression. Identity
stamped before and after the run and re-verified unchanged. Frontend: **335
passed, 0 failed, 52 suites**, up from WP1.4's 325 by exactly the ten new
tests. `npm run build` succeeds, 86 modules, 642.66 kB.

**WP1.5 — Read-only semantic panel in the existing review surfaces** — **RE-SCOPED 2026-09-14 by decisions Q9 and Q10**

*The sentence this package was written with.* "Embed a read-only 'Engineering
meaning' summary into the existing review case detail and the organisation
symbol review page, so an SME reviewing an intake sees the proposed semantic
state without leaving the lane. Read-only deliberately: the decision controls
stay in one place (WP1.4) so there is one audit path."

*Why it could not be built as written.* Measured against the tables before
any code was planned: **every governed semantic assertion in the system is
written by an approval handoff that runs after the review the panel would sit
in.** `SymbolRevisionClassificationAssignment` and `RightsRecord` rows are
created at exactly two call sites — `publication_handoff.py:604`/`:891`
(intake approval) and `organization_promotion_handoff.py:266` (promotion
approval, WP1.0's repair) — and both run inside
`execute_publication_handoff`, on the decision itself. So:

- **The intake review case detail has no symbol revision to ask about at
  all.** `/workspace/review-cases` lists only open cases (`closed_at IS
  NULL`) of `source_entity_type` `validation_report` or
  `provenance_assessment` (`workspace.py:2457`). Neither has a
  `symbol_revisions` row: `ensure_approved_symbol_revision`
  (`publication_handoff.py:479`) *creates* the revision from the
  `HumanReviewDecision`. There is no identifier to pass to
  `GET /semantic-review/symbol-revisions/{id}`.
- **The organisation symbol review page has a real revision id and nothing
  hanging off it.** `OrganizationSymbolDraftResponse.currentRevision.id`
  (`schemas.py:1383`) is a genuine `symbol_revisions` row, and
  `_visible_revision` would resolve it for an organisation-mode session. But
  no path writes assignments to an organisation draft revision before
  promotion, so the panel would resolve and render nothing.

Built literally, the panel would show "no semantic assertions yet" in exactly
the population it was designed for. Recorded as a finding, not worked around.

*Re-scoped shape (decision Q9).* The panel forecasts rather than reports: on
the intake review case detail it shows **what approving this case will
assert, and which section 9.3 fields will fall into a gap**. This is the one
thing that is genuinely non-empty before the decision, it is computable from
code that already exists and is already tested, and it sits beside the raw
`ClassificationRecord` fields the detail pane already renders as Discipline /
Format / Industry / Symbol family.

The forecast **must be a composition of the existing mapping path, never a
restatement of its rules** — a second copy of the precedence would drift from
the one that actually writes, and a panel that forecasts something other than
what approval does is worse than no panel:

```
context           = load_review_context(session, review_case)              # publication_handoff.py:229
classification    = context["classification_record"]
symbol_properties = load_review_symbol_properties(session, review_case=...)  # publication_handoff.py:196
fields            = classification_fields_from_record(                      # classification_mapping.py:462
                        classification,
                        discipline=symbol_properties.discipline if symbol_properties else None,
                        category=symbol_properties.category  if symbol_properties else None)
plan              = plan_classification_mapping(fields)                     # classification_mapping.py:332 (pure)
resolved          = resolve_node(session, scheme_code=..., candidates=...)   # classification_mapping.py:534
```

That `discipline`/`category` precedence is `record_classification_mapping`'s
own (`publication_handoff.py:364-367`): the human-reviewed
`ReviewSymbolProperty` wins over the classification record. The Reviews
surface lets an SME edit those properties in place, so a preview that skipped
the override would forecast the wrong node the moment a reviewer corrected a
discipline. Resolve the candidates rather than listing raw codes: a candidate
that resolves to no active node is a gap the SME would otherwise not discover
until after approval.

*Delivery shape.* One new read on the **existing** `semantic_review` router —
`GET /semantic-review/review-cases/{review_case_id}/classification-preview` —
behind the same default-off `SYMGOV_SEMANTIC_REVIEW_ENABLED` flag, the same
`admin`/`reviewer` boundary and the same 422 envelope as its sixteen
siblings, so WP1.5 changes nothing in production until the flag is activated
under its own approval. No migration; the head stays at `20260911_0057`.

**No tenant predicate, and the reason is measured rather than assumed.**
Neither `IntakeRecord` (`models/schema.py:2053`) nor `ClassificationRecord`
(`:2121`) carries an organisation, and the intake lane feeds the public
catalog. Section 14.2 is about organisation-private *symbol existence*; a
review case naming no symbol and no organisation gives it nothing to
protect. This is the same reasoning that left
`GET /semantic-review/classification-schemes` unscoped, and it is the
argument — not the convenience — that must hold at review.

*Frontend.* The forecast panel embeds in `ReviewsPage`'s focus pane
(`App.jsx:5069`), in the `copy-block` idiom, beside the existing
`review-support-facts`. Label it plainly as a forecast of the approval, never
as recorded state — `CLAUDE.md` forbids presenting an illustrative value as a
production one. `/reviews` is already gated
`RequireAnyRole roles={['admin','reviewer']}`, an exact match for the
router's `require_any_role({"admin", "reviewer"})`, so the lane needs no
second role rule — only the capability gate for the flag.

*The organisation symbol review page (decision Q10).* Its governed-state
panel stays in scope but **renders only for a session that satisfies the
API's own boundary and the flag, and is absent otherwise** — no failed
request, no empty frame, no promise the API will not keep. The page is gated
on an *organisation capability* (`canReviewOrganizationSymbols` →
`symbol_reviewer`, or organisation `baseRole === 'admin'`,
`projectContext.js:80`), which is a different axis from the platform role the
router requires; an organisation reviewer without platform `admin`/`reviewer`
would otherwise meet a 403. This mirrors `semanticReviewJourney.js`, which
already reproduces the API boundary rather than inventing a stricter or
looser one. Widening the router to accept the organisation capability was
considered and **rejected**: it would re-open decision Q2, which §4 records as
settled.

*Read-only throughout.* The decision controls stay in WP1.4 so there is one
audit path, exactly as the original sentence intended.

**WP1.5 — DELIVERED 2026-09-14**

*Delivered shape.* One new read — `GET
/semantic-review/review-cases/{review_case_id}/classification-preview`, the
seventeenth route in this router's semantic policy matrix and its nineteenth
route overall (WP1.3's two rights writes share the router) — behind the same
default-off flag, the same `admin`/`reviewer` boundary and the same 422
envelope as its siblings. No migration; head unmoved at `20260911_0057`. The flag was never
activated, so production is unchanged.

The forecast is a composition and not a copy, and two extractions are what
make that literally true rather than merely intended:

- **`publication_handoff.classification_fields_for_review`** — the reviewed
  precedence (`ReviewSymbolProperty` wins over the classification record),
  lifted out of `record_classification_mapping` and now called by both the
  writer and the forecast. A DB-free test fails if the writer stops calling
  it, and a PostgreSQL test fails if the override is dropped.
- **`classification_mapping.preview_classification_mapping`** — plans with
  `plan_classification_mapping` (the same pure function) and resolves with
  `resolve_node` and `_standard_version_for` (the same lookups), writing
  nothing. The two gap constructors `apply_classification_mapping` used
  inline — `_no_node_match_gap` and `_no_source_package_gap` — are now shared
  module functions, so the gap an SME is shown before the decision is the
  same row, word for word, that the approval records.

Two things the §4 text does not cover, recorded rather than decided silently:

- **The route takes an optional `splitItemId`, and it is not optional
  politeness.** The Reviews queue lists raster-split children as their own
  rows, and approving one runs `ensure_approved_child_symbol_revision`, which
  uses the *child's* classification record and the child's own
  `ReviewSymbolProperty`. `load_child_classification_record` deliberately
  refuses to inherit the sheet's record, whose family, process category and
  equipment class are the placeholders `mixed_symbol_set`/`review_required`/
  `mixed_equipment`. A case-level forecast for a split child would therefore
  have forecast precisely what the approval will not write — the failure Q9
  calls worse than no panel. The parameter composes the same two functions
  the child path composes; it introduces no rule of its own.
- **A measured discrepancy in the split path, not fixed here.** The approval
  handoff passes `load_child_classification_record` the child's position among
  the *approved* children (`approved_child_decisions`), which is unknowable
  before a decision and, when a reviewer approves only some children, is not
  the manifest position either. The forecast passes the manifest position
  (`ReviewSplitItem.payload_json["package_symbol_sequence"] - 1`, which is
  what `ClassificationRecord.symbol_region_index` means), falling back — as
  the writer does — to the child key, which is exact. The two agree whenever
  every child is approved. Where they would not, the *handoff* is the one
  matching a child against another child's region index; that is a
  pre-existing defect in `ensure_approved_child_symbol_revision`'s `index`
  argument, reachable without this package, and fixing it would widen WP1.5
  into the approval path it is only meant to forecast. Recorded here for
  scheduling, alongside the `propose_external_mapping` sibling defect the
  2026-09-14 amendment recorded.

*Frontend.* `ReviewClassificationForecast` (a new module, the `createElement`
idiom WP1.4 established) embedded in `ReviewsPage`'s focus pane in the
`copy-block` idiom, immediately after `review-support-facts` — beside the raw
Discipline / Format / Industry / Symbol family values it is a statement
about. Every heading says "will", the panel opens by saying it is a forecast
and not recorded state, and the closed backend vocabularies
(`MAPPING_GAP_REASONS`, `MATCH_BASES`) are rendered as sentences rather than
machine values. It is absent unless `canAccessSemanticReview` holds — the same
predicate the WP1.4 surface uses, so the UI reproduces the API's boundary
rather than inventing one. On the organisation symbol review page (Q10) the
governed-state panel reads `activeDraft.currentRevision.id` through the
existing `fetchSemanticReviewSymbolRevision` and is likewise absent for a
session the router would refuse; `auth` now reaches
`OrganizationSymbolReviewQueuePanel` for that one purpose. Neither panel adds
a decision control.

*Closing evidence.* Backend: 30 new tests. Nine DB-free in
`test_classification_mapping.py` (53 → 62) — three pinning the shared
precedence and that the writer still calls it, six pinning that the forecast
accounts for every section 9.3 field, reports the writer's own gap
vocabulary, plans with the writer's own planner and writes nothing. Four in
`test_classification_mapping_postgresql.py`, which are the ones that matter:
over one seeded review case the forecast names exactly the assignments the
approval proposes (node for node, role for role, match basis for match
basis), exactly the gaps it records, follows the reviewed property rather
than the record it overrode, and leaves every table unchanged. Eleven in
`test_semantic_review_routes.py` — three explicit (absent case reported
absent, the unscoped-read argument stated in the route itself, and the
contract naming the response `willAssert`/`willGap`/`willLink` rather than
`assignments`/`gaps`) plus the eight parametrized policy-matrix instances the
seventeenth route adds. Six in `test_semantic_review_routes_postgresql.py`,
including the split-child forecast against the sheet's, the foreign
split-item 404, the personal-mode read and a write-nothing check on the route
itself. Full portable partition on the final bytes: **3988 passed, 3 skipped,
3 deselected** in 1448s — the WP1.4-amendment baseline of 3958 plus exactly
the 30 new tests, no regression. Identity stamped before and after the run
and re-verified unchanged. Head unmoved at `20260911_0057`; `backend/alembic`
is untouched. Frontend:
**362 passed, 0 failed, 56 suites** via `npm run test:frontend`, up from 335
by exactly the 27 new tests — 3 API-helper, 17 in the new
`reviewSemanticPreview.test.js` (13 unit plus 4 mounted through the real
application router at `/reviews`) and 7 in `organizationSymbolDrafts.test.js`
for Q10. `npm run build` succeeds: 87 modules (86 plus the one new source
module), `dist/assets/index-*.js` 651.20 kB against 642.66 kB; the >500 kB
chunk-size warning is pre-existing. Not committed; nothing pushed, deployed or
activated.

**WP1.6 — Route-policy matrix, regression and acceptance**
Full portable regression, frontend tests, `npm run build`, a tenant-isolation matrix proving no organisation-private symbol existence leaks through any new endpoint (§14.2, §16.1), and an acceptance pass against the §16.1 criteria this package claims to close.

---

## 3. Test-artifact inventory

| Path | New/extend | Covers |
|---|---|---|
| `tests/test_semantic_review_queries.py` | new | WP1.1 non-Postgres |
| `tests/test_semantic_review_queries_postgresql.py` | new | WP1.1 constraints, isolation |
| `tests/test_semantic_review_routes.py` | new | WP1.2 route-policy matrix |
| `tests/test_semantic_review_routes_postgresql.py` | new | WP1.2 end-to-end governance acts |
| `tests/test_rights_review_routes*.py` | new | WP1.3 |
| `tests/test_classification_mapping_postgresql.py` | extend | WP1.0 org promotion path |
| `frontend/src/semanticReview.test.js` | new | WP1.4 |
| `tests/test_publication_gate_postgresql.py` | extend | WP1.3 gate satisfaction |
| `tests/test_semantic_review_routes.py` | extend | WP1.5 preview route policy, flag-off 404 |
| `tests/test_classification_mapping.py` | extend | WP1.5 forecast == what approval writes |
| `frontend/src/reviewSemanticPreview.test.js` | new | WP1.5 intake panel |
| `frontend/src/organizationSymbolDrafts.test.js` | extend | WP1.5 Q10 hide-when-ungated |

Pin every new `*_postgresql.py` fixture at the current head (`20260911_0057`) — the ORM is one global object that always reflects head, and a fixture pinned to an older revision breaks the moment a later migration extends a table its models touch.

## 4. Decisions log — RESOLVED

Q1–Q6 were put to Chris on 2026-09-12 and answered in the same session, each
confirmed on the recommendation as written below. Q7 and Q8 followed on
2026-09-13 with the WP1.2 route inventory (recorded inline in §2's WP1.2
entry). Q9 and Q10 followed on 2026-09-14, when WP1.5's targets were measured
against the tables and found empty at decision time. No work package is
blocked on a decision; the §2 sequence is final.

**Q1. Where does semantic review live? → (a) a standalone semantic review queue.**
Its own route, driven by queries over the semantic tables, independent of `ReviewCase`.
The 132 backfilled rows have no `ReviewCase` and cannot be given one without inventing an
intake that never happened (§1.3); option (b) would additionally need a migration and a
`ReviewCase` vocabulary extension. WP1.5 covers the "in Workspace/organisation review"
half of §15.2's sentence with a read-only embed, so there is exactly one audit path for a
governance decision.

**Q2. Who may perform each governance act? → split.**
Concept lifecycle (create, add revision, transition) requires `require_platform_admin`,
matching §17's "concept governance is platform-level initially". Symbol semantic
assignment, classification assignment and external-mapping decisions are open to `admin`
or `reviewer`, matching the existing Reviews surface. Rights-record decisions follow the
same `admin`/`reviewer` boundary (WP1.3).

**Q3. Feature flag? → yes, `SYMGOV_SEMANTIC_REVIEW_ENABLED`, default off.**
Same boolean-parse pattern as the eight existing I-20 flags in `settings.py`, exposed on
`/auth/me` as `semanticReviewEnabled`. Evaluated at import, so activation needs a process
restart — which is a separate operation requiring its own approval and is **not** part of
any work package here.

**Q4. Does SM-P1-01 include rights approval (WP1.3)? → yes.**
Without it the publication gate's rights dimension stays permanently unsatisfiable, which
keeps SM-P2-01/-02 authoritative ingestion behind an all-waiver or all-refusal gate. It is
the smaller half of the same unblocking job and shares the whole route/authz scaffold.

**Q5. Is WP1.0 (the organisation promotion dual-write repair, §1.4) in this package? → yes, sequenced first.**
It is a P0 defect against §12.1 phase M4 rather than new P1 scope, and building the review
queue over a population missing every organisation-promoted symbol wastes the queue's
first real use.

**Q6. May the UI assign into `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE`? → read-only in v1.**
Existing assignments in those three schemes are displayed; no control creates one. Giving
three unused schemes their first writer through a review UI is a product decision about
what SymGov asks reviewers to record, and `CLAUDE.md` forbids inventing workflow states.
Revisit separately once the vocabularies are settled.

**Q9. WP1.5's two target surfaces are both empty at decision time. What does the panel show? → the mapping forecast.** *(2026-09-14)*
Every governed semantic assertion is written by the approval handoff that runs
*after* the review the panel sits in, so a governed-state panel in the intake
lane would render "nothing yet" in every case it was designed for. The panel
instead shows what approving the case **will** assert and which section 9.3
fields will fall into a gap, composed from `load_review_context`,
`load_review_symbol_properties`, `classification_fields_from_record`,
`plan_classification_mapping` and `resolve_node` — all existing and tested,
and `plan_classification_mapping` is pure by its own docstring. Cost: one new
read on the existing router. The three alternatives were building it as
specified and accepting a permanently empty panel, delivering only on the
organisation page, and deferring to WP1.6. Chris chose the forecast. It must
be labelled as a forecast of the approval, never as recorded state.

**Q10. The organisation symbol review page's gate does not match the API's. What does the panel do there? → hide it.** *(2026-09-14)*
That page is gated on the organisation capability `symbol_reviewer` (or
organisation `baseRole === 'admin'`); the router requires the *platform* role
`admin` or `reviewer`. A reviewer holding only the former would meet a 403.
The panel is therefore absent unless the session satisfies the router's own
boundary and the flag — not rendered as an error and not rendered as an empty
frame. Widening the router to accept the organisation capability was rejected
as a re-opening of Q2.

**Settled, do not re-litigate:** concept governance is platform-level (§17); Industry/Application and process-category vocabularies are Chris's separate research and no scheme is seeded here; `ConceptTerm` and organisation-scoped concepts stay deferred; M3 provisional concept candidates are **not** in this package — manual concept creation lands first, so there is something to review before a generator fills the queue.

## 5. Regression standard

Every work package closes on: the focused tests named in §3; the portable backend partition (currently 3696 passed / 3 skipped / 3 deselected — no package may lower this); `npm run test:frontend` and `npm run build` for any package touching `frontend/`. Run the portable partition with an explicit timeout above 300s — `scripts/test-backend.sh`'s five-minute bound now expires at ~53% on this host, which is a test-harness observation, not a product regression.

## 6. Prohibited side effects

- No `npm run build:publish`, `npm run publish:static`, deployment, service restart, live migration, or feature activation without Chris's explicit approval for that specific operation.
- No push without explicit approval.
- No change to the legacy `GovernedSymbol.category`/`.discipline` derivation rules — SM-P0-09's `NON_DISPLAY_MATCH_BASES` deny-list stays exactly as it is; a review UI adds a reviewer's own choice as a *new* candidate, it does not re-open the coarsening question.
- No seeding of an Industry/Application or process-category scheme.
- No change to `automation_policy.evaluate_publication_automation_gate` or `provenance_assessments` — the two publication gates coexist and stay strangers.
- Leave `.claude/settings.local.json` and `UI-Design/` untouched.
