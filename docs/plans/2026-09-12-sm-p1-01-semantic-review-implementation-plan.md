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

**WP1.4 — Semantic review surface (frontend)**
A standalone semantic review surface at its own route (Q1), behind the flag. Queue list plus a detail panel showing, for one symbol revision: primary/qualifier/component assignments, classification assignments per scheme, external mappings, evidence JSON and status, each with its decision control. Keeps the existing visual language and responsive behaviour; accessible controls, visible focus states, semantic labels, keyboard-navigable queue. Human-readable symbol IDs and operator-readable timestamps prominent. Per Q6, `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` render read-only: existing assignments are visible, no control creates one. Tests in the existing `frontend/src/*.test.js` pattern, run by `npm run test:frontend`.

**WP1.5 — Read-only semantic panel in the existing review surfaces**
Embed a read-only "Engineering meaning" summary into the existing review case detail and the organisation symbol review page, so an SME reviewing an intake sees the proposed semantic state without leaving the lane. Read-only deliberately: the decision controls stay in one place (WP1.4) so there is one audit path.

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

Pin every new `*_postgresql.py` fixture at the current head (`20260911_0057`) — the ORM is one global object that always reflects head, and a fixture pinned to an older revision breaks the moment a later migration extends a table its models touch.

## 4. Decisions log — RESOLVED 2026-09-12

All six were put to Chris on 2026-09-12 and answered in the same session. Each was
confirmed on the recommendation as written below. No work package is blocked on a
decision; the §2 sequence is final.

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
