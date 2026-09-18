# Symgov — state of play and account-migration handover

**Written 2026-09-14, at `615e617`, for the move to a new Claude account in a
different Idox engineering organisation.**

This document exists because a large part of what an assistant knows about
Symgov currently lives **outside this repository** — in a Claude memory
directory, a Hermes profile, untracked working trees, and machine-local git
branches. None of that travels with an account, and some of it does not
travel with a machine either. §6 is the list of what must be physically
moved; §7 is what must be re-authorised in the new organisation.

Everything here was verified against the repository and the running
deployment on 2026-09-14. Where something is unknown, it says so rather than
guessing.

---

## 1. How to use this in the new account

Read this file first, then `CLAUDE.md` (repo root), then the controlling plan
for whatever work is being resumed. This document is a **snapshot**, not a
living contract: if it disagrees with the repository or with a plan document,
the repository wins and this file should be corrected.

It deliberately does **not** duplicate:

- `CLAUDE.md` — the standing rules for working in this repo.
- `docs/README.md` — the documentation map and classification scheme.
- `docs/plans/` — 86 plan, kickoff and decision documents. The programme
  history is there, not here.

What it *does* carry is the knowledge that was only ever held in assistant
memory: open defects nobody has scheduled, decisions that are settled and
should not be re-litigated, and engineering traps that cost real time.

---

## 2. System state, verified 2026-09-14

| Fact | Value |
|---|---|
| Branch / HEAD | `main` at `615e617`, level with `origin/main` |
| Production release | `stage11-615e617`, deployed 2026-09-14 |
| Alembic head | `20260911_0057` — sole head; the live database is at it |
| Backend suite | 4064 passed / 3 skipped / 3 deselected (~24 min, Postgres included) |
| Frontend suite | 362 passed / 0 failed / 56 suites |
| `npm run build` | 87 modules, `dist/assets/index-*.js` 651.22 kB (the >500 kB chunk warning is pre-existing) |

**Deployment topology** is documented in the `deploy-release` skill
(`.claude/skills/deploy-release/SKILL.md`, tracked in git — it travels).
Summary: release worktrees under `/data/symgov-releases/stage11-<sha>/`,
compose at `/docker/symgov-hermes/docker-compose.yml` which hardcodes the
release path in **four** places, containers `symgov-hermes-api`,
`applications-web`, `symgov-postgres`, `symgov-minio`.

### 2.1 What is live versus dormant

The 2026-09-14 deployment shipped eleven commits — the whole of SM-P1-01 plus
`b157e04`, none of which had been deployed since 2026-09-11.

**Live and observable:** WP1.0's organisation-promotion repair. It is *not*
flag-gated, so promoting an organisation-private symbol to the public
catalogue now writes `symbol_revision_classifications` rows and syncs the
legacy `GovernedSymbol.category`/`.discipline` display columns. Before this
release it wrote neither.

**Deployed but dormant:** the entire semantic review API and both UI
surfaces. `SYMGOV_SEMANTIC_REVIEW_ENABLED` is **unset** in
`/docker/symgov-hermes/docker-compose.yml`, so the flag is false and
`/api/v1/semantic-review/*` answers **404** — absent rather than forbidden, so
the surface does not advertise itself. Verified three ways on the running
container.

To activate: add `SYMGOV_SEMANTIC_REVIEW_ENABLED=true` to the compose
`environment` block and `docker compose -f … up -d symgov-api`. The flag is
read at **import**, so it needs that restart; it needs no rebuild and no
migration. **This has never been switched on in production**, and the 132
backfilled classification rows would be the surface's first real population.

---

## 3. Programme status

**SM-P0-01 … SM-P0-10 — complete and deployed.** The semantic model data
layer: concepts, revisions, semantic assignments, classification schemes and
assignments, source packages, rights records, asset transformations, the
publication gate, and the classification backfill.

**SM-P1-01 — complete in the repository, not in effect.** WP1.0 through WP1.6,
delivered 2026-09-12 to 2026-09-14. Controlling plan:
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md`, whose
**§7 is the acceptance pass** against the specification's §16.1 criteria and
is the most useful single thing to read about what this package does and does
not close.

The distinction matters and should be repeated to anyone picking this up:
*delivered* means the repository contains it and its tests pass. Because the
flag has never been activated, **no reviewer has ever used this surface.**

**Not started:** SM-P1-02 onward, and SM-P2-01/-02 authoritative ingestion.
Note the standing advice in §5 below — do not plan an ingestion connector
before the review surface is actually in use, or the first ingest is
all-waiver or all-blocked.

---

## 4. Open defects and unfinished business

None of these is assigned to a work package. All were measured, not
suspected.

### 4.1 Carried defects in SM-P1-01 (recorded, deliberately not fixed)

1. **`propose_external_mapping` lets a duplicate active mapping escape as a
   500** rather than a 409 or 422. Recorded by the 2026-09-14 amendment. Not
   reachable from any UI, so low urgency.
   **Fixed 2026-09-17.** Root cause: the insert reached the database only at
   `session.commit()`, outside every exception handler, so
   `uq_concept_external_references_active_mapping` raised a bare
   `IntegrityError`. The route now flushes inside the `try` and matches that
   index by name, exactly as `propose_symbol_classification` already did for
   `uq_symbol_revision_classifications_active_node`. **422, not 409**, by that
   precedent and because 409 is not in the router's declared
   `_ERROR_RESPONSES`; a reviewer naming an already-live mapping is making a
   refusable request, not provoking a fault. The sibling index
   `uq_concept_external_references_verified_exact` is deliberately not matched
   — proposing always writes `proposed`, so it cannot be violated here.
   Regression cover:
   `test_proposing_a_live_mapping_again_is_refused_rather_than_faulting`,
   verified RED (bare `IntegrityError`) before the fix and GREEN after.
2. **`ensure_approved_child_symbol_revision` passes the wrong child index.**
   It gives `load_child_classification_record` the child's position among the
   *approved* children, so approving only some children of a split sheet can
   match a child against another child's `symbol_region_index`. This one is
   in the live approval path and **is** reachable. Recorded by WP1.5.
   **Fixed 2026-09-16.** The approval path now resolves the child's manifest
   ordinal through `publication_handoff.split_item_region_index`, which WP1.5's
   forecast already used and which is now shared by both so they cannot drift.
   Regression cover:
   `test_approving_only_the_second_child_reaches_the_second_child_record`.

### 4.2 Pre-existing defects found during the semantic-model work

3. **Catalogue facet over-matching.** `catalog_search.catalog_symbol_filters`
   unconditionally ORs a `payload_json` substring match into every facet, so
   filtering the public catalogue by category `Equipment` or discipline
   `Process` returns **all 84** published symbols — the facets exclude
   nothing. Measured 2026-09-11 against production. The likely fix (prefer a
   governed assignment where the revision has one, fall back to text
   otherwise) is a behaviour change to the legacy path and needs its own
   decision, not a quiet tightening. **Do not treat facet counts for those two
   values as meaningful until this is fixed.**
   **Fixed 2026-09-17.** Root cause: `CAST(payload_json AS TEXT)` renders the
   JSON *keys* beside the values, so `Equipment` matched the key
   `parent_equipment_class` and `Process` matched `process_category` in every
   payload. The five payload-matching facets now name the field they mean;
   `use_case`, which is derived by `use_cases_for_formats` and stored nowhere,
   resolves back to the formats that present it. Chris chose the semantics on
   2026-09-16. Facet counts for those two values are meaningful again, but no
   production measurement has been taken since the fix. The free-text `q`
   filter carried the identical flaw and was fixed the same way on
   2026-09-17: it reads the named fields the catalogue shows, including the
   `aliases`, `keywords` and `search_terms` arrays. `source_file` was
   deliberately dropped from the searchable set — the only recall that change
   gives up, and still open if operators search by contributor filename.
   `has_preview` still reads the document on purpose: it matches a key name.
4. **`classification_records.industry` is effectively dead.** One writer
   (`scripts/run_libby_classification.py`), no editor, four hard-coded values
   — three of which are discipline names duplicating the
   `ENGINEERING-DISCIPLINE` scheme, and the fourth
   (`general_industry`) is already listed in `automation_policy.py`'s
   `PLACEHOLDER_DISCIPLINES` as an absence of a value. The vision LLM never
   writes it and reviewers cannot edit it, so a wrong value written at intake
   stays wrong forever. Logged at Chris's instruction in
   `docs/plans/2026-09-11-classification-industry-field-defect.md`. ~~**Open and
   unscoped:** whether `industry` is an axis at all, or should be retired in
   favour of the scheme it duplicates.~~
   **Axis question answered 2026-09-17 (Chris): `industry` *is* an axis and
   stays; it is not retired in favour of `ENGINEERING-DISCIPLINE`.** ICS
   licensing is also closed the same day (ODC-By v1.0 via ISO Open Data), so
   the Industry/Application scheme is unblocked and ICS is its vocabulary.
   **The defect itself is still open**: the four hard-coded branches, the
   absent scheme-selecting writer and the absent reviewer correction path are
   all unchanged and unscoped. Decisions recorded in the defect doc's
   "Decisions taken 2026-09-17" section.

**Both §4.2 item 4 and all of §4.3 now have written implementation plans
(2026-09-17):**
`docs/plans/2026-09-17-sm-p1-02-industry-axis-implementation-plan.md` and
`docs/plans/2026-09-17-sm-p1-03-structural-gaps-implementation-plan.md`.
Each carried the decisions still needed from Chris (D1–D4 and D5–D8).
**All eight were answered by Chris on 2026-09-17**, each as recommended, with
D3 additionally instructing that `ISO-ICS-7` be activated as
`chris.brighouse@hotmail.co.uk` — an act that cannot be performed today, for
four measured reasons recorded under "D3, as answered" in the industry plan
(ICS is not deployed to production; no code path activates a scheme; the only
actor column on `ClassificationScheme` records who created it, so a status
change cannot be attributed; that account's production role is unverified). Writing a plan closes nothing; every item
below stays open except item 9.

**SM-P1-02 WP2.1 delivered 2026-09-18.** `ISO-ICS-7` is now in
`REVIEWER_ASSIGNABLE_SCHEME_CODES`, bounded by decision D4's depth rule
(`REVIEWER_ASSIGNABLE_MAX_DEPTH`), enforced on the propose route as well as
the picker. Two decisions were taken on the day and are recorded as D5 and D6
in §7 of the industry plan: the options response stays a single unpaginated
read, and a `draft` node accepts no assignment. **Item 4 is not closed** —
`industry` still has no writer that can select from a scheme (WP2.2, which
turns on D1) and the four heuristic branches in `run_libby_classification.py`
are untouched (WP2.4). What WP2.1 closed is the narrower absence: there was no
governed vocabulary a reviewer could assign from at all.

Two findings from it worth carrying beyond the plan:

- **`CLOSED_NODE_STATUSES` was doing two jobs.** It gated both "may this node
  take a child" and "may this node take an assignment". Adding `draft` to it
  as literally instructed would have refused the ICS import, which builds all
  1381 nodes as draft before activation. Split into a second constant,
  `UNASSIGNABLE_NODE_STATUSES`. Any future status rule should ask which of the
  two questions it is answering.
- **The draft hole was reachable for every scheme, and for concepts.** Concept
  classifications have no propose route at all, so a route-level guard could
  never have covered them. This is the same shape as the 2026-09-17 pre-load
  ruling and was fixed the same way.

**SM-P1-01 was activated in production on 2026-09-18.**
`SYMGOV_SEMANTIC_REVIEW_ENABLED: "1"` on the `symgov-api` service, container
recreated; the surface moved 404 -> 401. No migration ran and production is
still at `20260911_0057`. Rollback is removing the one line and recreating.

It was activated **knowingly on `stage11-615e617`**, which predates four
fixes that are therefore not live: `c26ce1b` (a duplicate live external
mapping 500s instead of 422 -- reachable by a single reviewer, the likeliest
bite), `3e1776b` (split child resolved by the wrong index), `261193a` (WP3.1's
decide path) and `09b2b13` (the silent-overwrite race). Chris weighed these
against a release that would run migrations `20260915_0058`/`_0059`/`_0060`
against production, and chose activation. **The next release closes all four
at once**, and is worth cutting before the surface has a second reviewer.

Two facts about this box that cost time on 2026-09-18 and are worth keeping:

- **The served frontend is a release worktree, not the repository.** nginx
  mounts `/data/symgov-releases/stage11-615e617/dist`; the repo root's own
  `assets/` is stale published output and says nothing about production. The
  API listens on **8010**.
- **The PostgreSQL test fixtures leak a docker volume per module per run.**
  1,971 orphaned anonymous volumes had filled the 193 GB root filesystem to
  100% (11 MB free), which does not present as a disk error: docker simply
  cannot start the disposable Postgres, and the sweep reports failures and
  setup errors in unrelated `_postgresql` modules. Cleared on 2026-09-18 to
  48%. **It will refill** -- `_database()` in
  `test_organization_symbol_postgresql.py` does not remove its volume on
  teardown, and that is the actual defect.

### 4.3 Structural gaps

5. **Three of five seeded classification schemes have no writer and no
   reader** — `USE-CASE`, `DOCUMENT-TYPE`, `REPRESENTATION-TYPE`. Decision Q6
   of SM-P1-01 deliberately kept them read-only in v1.
6. ~~**The specification's §7.3 `SemanticConceptRelationship` has no table and
   is in no work package.**~~
   **Closed 2026-09-17 (SM-P1-03 WP3.2), on Chris's D6.** Migration
   `20260917_0060` creates `semantic_concept_relationships` with §7.3's whole
   relationship vocabulary, and `concept_relationships.py` carries the
   propose/transition pair and the directed reads. Three things are worth
   carrying forward. A row is **one directed assertion**: §7.3 ships both
   directions of each pair, so nothing mints `narrower(B, A)` from
   `broader(A, B)` or refuses it as a duplicate, and a test pins that absence —
   inferring one from the other would put an unreviewed assertion in the record
   (P-07). `method` and `confidence` are carried although §7.3's field list
   omits them, so §8.4's auto-verification policy has a column to inspect;
   `legacy_backfill` is deliberately **not** in the vocabulary, because §12.1
   phase M2 backfills classifications and no relationship backfill exists to
   write it. And **unlike SM-P0-02, -03 and -04 there is no succession rule** —
   nothing in §7.3 makes two verified relationships mutually exclusive, so
   verifying one retires nothing.

   `parentEquipmentClass` still records a gap, but a true one: its reason moved
   from `no_relationship_table` to `no_concept_target`, because the table now
   exists and what is missing is a concept to point at. The CFIHOS equipment
   classes that would supply one wait on SM-P2-02, which waits on the connector,
   which waits on SM-P1-01 being in use. `no_relationship_table` has left
   `MAPPING_GAP_REASONS` entirely.

   **P0 completeness:** this was the entity that made "all ten P0 packages are
   complete" untrue. With the table in, the claim stands again.
7. **`publication_gate_evaluations` is write-only.** Every publication records
   all six §13.1 dimensions and nothing reads the table. It is SM-P1-06's data
   source and is already accumulating.
8. **The publication gate's scope is unreachable in production.** It fires
   only on `package_type='authoritative_library'`, and `register_source_package`
   — the only creator of one — is called from tests only. So the gate is
   `not_in_scope` for 100% of production traffic.
9. ~~**`ConceptClassificationAssignment` has a queue read and no decision
   route.** WP1.4 renders that queue read-only for this reason.~~
   **Closed 2026-09-17 (SM-P1-03 WP3.1), on Chris's D8.**
   `POST /semantic-review/concept-classifications/{assignment_id}/decision`,
   modelled on `decide_symbol_classification`, with the frontend's read-only
   notice replaced by `capabilities`-driven `DecisionControls`. The response
   is the concept's whole classification state rather than the row named,
   because verifying a `primary` retires the primary verified before it and a
   single-row response would hide that succession. Unscoped by decision and
   registered as such in the tenant matrix: a concept-to-node assertion names
   no symbol (§17, and WP1.1's identical call for the queue it decides on).

### 4.4 Unknown, and worth establishing

10. **Nobody has written down what database role the deployment actually runs
    as.** The narrow `symgov_app` role was measured to have *no* privilege on
    `governed_symbols`, `symbol_revisions`, `source_packages` or any
    semantic-model table, yet the live intake path writes them on every
    submission — so `symgov_app` is demonstrably not the role production uses
    for the core tables. Three production failures on 2026-09-07 came out of
    this same blind spot. **Consequence for new work:** a new semantic-model
    table should carry **no** `GRANT`; that matches SM-P0-04 through -08, and
    adding one would be the anomaly.

11. **24 further locking reads have never been checked for a caller that
    preloads.** Closing the unlocked-pre-load race (2026-09-17) fixed five
    `session.get(..., with_for_update=True)` calls that needed
    `populate_existing=True`: the four reached by a decision route in
    `routes/semantic_review.py`, plus `concept_relationships.py`, which has no
    route yet. A repository-wide sweep at that moment found **29** locking reads
    in total, so **24 remain unrefreshed** across `promotion_requests.py`,
    `symbol_demotion.py`, `symbol_set_service.py`, `catalog_symbol_ids.py`,
    `semantic_concepts.py`, `organization_promotion_handoff.py`,
    `publication_gate.py`, `standard_sources.py`, `ics_taxonomy.py`,
    `source_package_acquisition.py` and `agent_queue_worker.py`.

    **This is not 24 defects.** The bug needs a *caller that loads the row
    before the service locks it*; a service whose callers all enter cold is
    unaffected. Which of the 24 have such a caller was **not measured** — the
    semantic-review four were measured because their routes were already known
    to preload. Establishing that, module by module, is the work; the guard in
    `tests/test_governed_transition_locking_postgresql.py` deliberately covers
    only the five, and says so.

    `promotion_requests.py` and `symbol_demotion.py` are the two worth looking
    at first: both are live production paths that move a governed symbol between
    visibility states.

    **A second shape of the same trap exists and was checked:**
    `select(...).with_for_update()` returns identity-mapped instances just as
    unrefreshed as `Session.get` does, and the two succession helpers
    (`_retire_superseded_primary`, `_retire_superseded_exact_mapping`) use it.
    Both were measured clean — they select rows *other* than the one the caller
    named (`model.id != assignment.id`), and no route loads those rows before
    the transition — so neither was changed. The guard test does not cover this
    shape, which is why it is written down here.

---

## 5. Settled decisions — do not re-litigate

Approved by Chris on the dates shown. Re-opening any of these has cost
sessions before.

- **2026-09-09, the specification's §17 register, approved in full.** Concept
  governance is **platform-level initially** (organisation-scoped concepts
  deferred, not rejected). Multilingual `ConceptTerm` deferred; aliases stay
  JSONB in P0. **No graph database** for P0/P1 — PostgreSQL only; RDF/JSON-LD
  is P2.
- **Rights persistence: no durable model existed, so SM-P0-06 created one.**
  Verified across all 80 tables: `provenance_assessments` is intake-scoped and
  `hannah_photo_candidates.rights_status` is candidate-scoped; neither binds
  rights to a governed symbol or revision. **Do not re-litigate this as
  "extend the existing model".**
- **2026-09-11, no Industry/Application scheme is seeded.** ICS
  (International Classification for Standards, ISO edition 7) is the intended
  source *when the scheme is eventually created*. ~~**Chris is investigating ICS
  licensing separately and this is to be picked up in a later discussion — the
  scheme must not be created until that returns.**~~ Both alternatives (the ISO
  14617 application-area list; Chris supplying nodes himself) were offered and
  declined. Seeding "from the distinct values already in the column" is closed,
  not merely unattractive — see defect 4 above.
- **2026-09-17, ICS licensing is closed and the scheme is unblocked.** ICS
  edition 7 is published as the `iso_ics` dataset on ISO Open Data under
  **ODC-By v1.0** — a different channel from the paywalled page that refused
  automated fetches — so embedding node labels in the UI and API is permitted
  and the vendored `ICS.csv` on public `main` is correctly redistributed. In
  the same decision, **`industry` is confirmed as a real axis**: it is not
  retired in favour of `ENGINEERING-DISCIPLINE`, and ICS becomes its governed
  vocabulary. **Gate carried forward:** ODC-By requires the attribution notice
  to travel with the data, and nothing under `frontend/src` currently carries
  it. **Corrected 2026-09-17:** `frontend/src/SupportDataSources.jsx` has
  carried the attribution, the codes-only clarification and the ODC-By link on
  the Support route since `e6cd860`, so the general notice is present; it
  reads the vendored `data/ics-source.json` rather than the stored import row.
  What is still owed is the notice travelling **with** the labels, before any
  ICS label reaches the UI or a public API response. No ICS label is conveyed
  today — `ISO-ICS-7` is a draft scheme excluded from the SME options route —
  so this is a precondition on activation, not a live breach.
- **The process-category vocabulary is likewise Chris's separate research**
  and out of scope for everything delivered so far.
- **SM-P1-01's decisions Q1–Q10 are all resolved** and recorded in §4 of its
  plan. In particular: the review surface is standalone (Q1); concept
  lifecycle is platform-admin, symbol/classification/mapping decisions are
  `admin` or `reviewer` (Q2); the flag ships default-off (Q3); the three
  unused schemes stay read-only (Q6); no route takes step-up re-authentication
  (Q8); the classification preview is a forecast and must never be presented
  as recorded state (Q9); the organisation panel is absent rather than
  erroring for a session the router would refuse (Q10).
- **CFIHOS may help with `parentEquipmentClass` later** — its 832 equipment
  classes and 875 tag classes are the likely vocabulary once SM-P2-02 imports
  them.

---

## 6. What lives OUTSIDE this repository

**This is the section that matters for the move.** Everything below is real,
current and would be lost or silently degraded if the machine or account
changed without action.

### 6.1 Assistant memory — 25 files, not in git

`/root/.claude/projects/-docker-openclaw-hz0t-data-symgov/memory/`

Twenty-five files (132 KB on disk, index included) of settled decisions,
delivery records, defect measurements and engineering traps, accumulated
2026-09-06 to 2026-09-14, with an index at `MEMORY.md`. **The durable content has been distilled into §3–§5 and §8 of
this document**, but the per-package delivery records and their reasoning are
not reproduced here. Copy the directory wholesale if the new account should
inherit the full history.

### 6.2 Hermes skill profile — not in git

`/root/.hermes/profiles/symgov/skills/symgov/` holds six skills that every
stage from 4 onward was delivered with:

| Skill | Role |
|---|---|
| `symgov-programme-planning` | produced every stage implementation-plan doc |
| `symgov-feature-implementation` | the delivery skill — worktree discipline, TDD, evidence separation |
| `symgov-product-planning` | smaller external proposals → testable backlogs |
| `symgov-release-operations` | integrate / verify / push / migrate / deploy / smoke-test |
| `symgov-uncommitted-worktree-integration` | exactly the skill for §6.4 below |
| `api-catalog-authentication` | Catalog API auth specifics |

The in-repo `deploy-release` skill (`.claude/skills/deploy-release/SKILL.md`)
**is** tracked and travels with git; these six do not.

### 6.3 `UI-Design/` — 488 KB, untracked and *not* gitignored

The Idox design system from Dave Gibson: `design-tokens.css`,
`component-examples.html`, `idox-design-system.md`, a dist zip, plus his
written instructions and a UI prompt document. `CLAUDE.md` says to leave it
untouched, and every session has. It is **not** in `.gitignore` — it is simply
uncommitted, so it exists only on this machine.

### 6.4 Uncommitted work in a side worktree — the largest single risk

`/docker/openclaw-hz0t/data/symgov-stage6-fixes`, branch
`fix/stage6-review-remediation-20260902`.

The branch tip (`bcf581c`) has **zero commits not already in `main`** — so the
branch itself carries nothing. The value is entirely in the **uncommitted
working tree**: ~1022 insertions across 14 tracked files, plus four untracked
new modules including `backend/symgov_backend/symbol_identity.py` and
`symbol_eligibility.py`. Triaged 2026-09-06 as *not* abandoned exploration but
substantially working, mostly-additive unshipped code. Three pieces were
judged worth porting on their own merits:

- category/discipline/format search filters on `GET /builder-search`
  (`main` takes only `q`);
- human-readable `displayId` surfacing — directly relevant to `CLAUDE.md`'s
  rule that human-readable symbol IDs stay prominent, which `main` does not
  satisfy on these responses today;
- an optimistic-concurrency (ETag/428) guard on
  `symbol_set_service.replace_items`, guarding a real lost-update race `main`
  does not.

**There is no second copy of this anywhere.** It is not committed, not
pushed, and not on any remote.

### 6.5 Local-only git branches — no remote counterpart

These exist on this machine and nowhere else:

```
docs/current-state-refresh-20260730             510844d
fix/stage6-review-remediation-20260902          bcf581c
hermes/langfuse-items-4-5                       19dd58b
integration/stage11-wp11.1-worktree-integration 3c790be
merge/llm-consumption-into-main                 47f25bc
```

### 6.6 Production configuration and secrets

`/docker/symgov-hermes/.env` — mode 0600, seven telemetry variables that exist
**only** there. Deploying with them empty silently blanks the Langfuse
credentials, which is why the deploy procedure requires
`docker compose config | grep -c '\${'` to return **0**. Do not delete this
file; do not commit it.

Also outside git: `/data/symgov-backups/` (pg_dump custom-format dumps) and
`/data/symgov-releases/` (release worktrees, including the rollback target).

---

## 7. Account-bound items needing re-authorisation in the new organisation

These are tied to the Claude account/organisation rather than the machine, and
will need setting up again:

- **MCP connectors** — Atlassian Rovo (Jira/Confluence), Aha!, Microsoft 365.
  All are OAuth-bound to the current organisation.
- **Any published Artifacts** created under the old account remain owned by
  it; they cannot be updated from the new account and would need republishing.
- **`.claude/settings.local.json`** is tracked but carries a local
  modification that has been deliberately left uncommitted through every
  session in this programme. Review it before assuming it should travel.
- **Scheduled tasks / cron agents**, if any were registered under the old
  account.

---

## 8. Engineering traps — read before writing tests or migrations

Each of these fails in a way that looks like a product defect but is not.

**Portable (SQLite) route tests — four fixture traps:**

1. `app.routes` exposes **no paths** — this FastAPI version keeps lazy
   `_IncludedRouter` objects, so a mount assertion against `route.path`
   passes *vacuously*. Use `create_app().openapi()["paths"]`.
2. **I-20 feature flags cannot be re-read at runtime.** They are dataclass
   field defaults evaluated once at import; `monkeypatch.setenv` plus
   re-instantiation will not flip one.
3. **Platform Admin is not a role assignment.** It needs an active
   `platform_admin` `PlatformRoleAssignment` **plus** an `admin` base role in
   the `symgov` organisation itself, that org must be in
   `organization_pilot_codes`, its code must be lowercase `symgov` with
   `is_protected=True` at INSERT, and the membership and assignment must land
   in one transaction.
4. **Global roles need a `plus` subscription.** `upsert_user(…, roles=[…])` on
   a fresh user silently stores nothing. Create, upgrade
   `UserSubscription.tier` to `plus`, then `upsert_user` again with the roles.

Also: `governed_symbols.visibility` is `organization_private`, not
`organization`.

**A trap added 2026-09-14:** a *bare* Platform Administrator holds no global
`admin`/`reviewer` role, so every semantic-review route answers **403** before
the tenant predicate is reached. Any tenant-isolation probe using a platform
admin must also grant the global role, or it is testing the authorisation
boundary while believing it tested tenant isolation.

**The SQLite boundary.** The five `semantic_review.list_open_*` queue queries
**cannot execute on SQLite at all** — `_CONCEPT_DISPLAY_NAME` is a correlated
subquery whose `ORDER BY` references the outer `semantic_concepts` row, and
SQLite reports `no such column: semantic_concepts.current_revision_id` even
though the column exists. This is a capability limit, not a fixture gap.
**Never try to make these run in the portable partition** — stub them and put
the assertion in the `_postgresql.py` file.

**PostgreSQL's 63-character identifier limit** bites migrations two ways: an
explicit name to `create_foreign_key`/`create_index` raises `IdentifierError`
and fails the upgrade outright, while a name to `sa.CheckConstraint(name=…)`
is **silently truncated** with a 4-char hash suffix. Pass a **bare**
constraint name (`name="status"`, not `name="ck_semantic_concepts_status"`)
and let `NAMING_CONVENTION` add the prefix. The ORM/DB drift this caused was
fully repaired by `20260909_0050` and `c373972`; three `_postgresql.py` suites
assert it stays repaired.

**Test invocation.** `scripts/test-backend.sh` bounds the portable partition
at `timeout 300s`, which expires at ~53% on this host — a harness limit, not a
product regression. Run the partition directly with a longer bound; it takes
~24 minutes.

---

## 9. Standing advice

- **Check whether a code path can create a `verified` row before designing
  anything that reads one.** This was the same root cause in five separate
  places: the P0 data model was complete while the entry points were not, so
  anything gated on a human governance act was inert on delivery. SM-P1-01 is
  the unblocker — but only once its flag is on.
- **Do not plan an authoritative-ingestion connector (SM-P2-01/-02) before
  the review surface is genuinely in use**, or the first ingest is
  all-§9.2-waiver or all-blocked.
- **"Delivered" and "in effect" are different claims.** Say which one is
  meant.
