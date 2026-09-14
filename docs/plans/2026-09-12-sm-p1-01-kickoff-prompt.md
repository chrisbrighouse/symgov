# Semantic Model & Classification — SM-P1-01 kickoff prompt

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This is the restart pack for the first P1 package of the semantic model
programme. The controlling plan already exists —
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — so
unlike the Stage 4–11 kickoffs, the new session's first deliverable is **not**
another plan document. It is getting the plan's §4 decisions answered, then
executing the work-package sequence.

## Frozen baseline (captured 2026-09-12)

- Branch: `main`
- Local `HEAD`: `b157e04` ("refactor: split the backfill's rewrite count into label and column changes")
- `origin/main`: identical — fully pushed (`git rev-list --left-right --count origin/main...main` → `0 0`)
- Sole Alembic head: `20260911_0057` (verified by walking `down_revision` across all 60 migration files)
- Tracked tree clean except the pre-existing, unrelated `.claude/settings.local.json` diff, plus untracked `UI-Design/` — leave both untouched, same as every prior stage's baseline note.
- Backend portable regression baseline, re-run independently on 2026-09-12 rather than quoted from an earlier package note: **3696 passed, 3 skipped, 3 deselected** in 1103s (Postgres tests included). No work package may lower this.
- Production runs release `stage11-56677a9`. `b157e04` is the only `main` commit not deployed, and it is a report-field rename with no runtime effect.
- SM-P0-01 through SM-P0-10 are all committed and closed. Migrations `20260909_0047` → `20260911_0057` (eleven) were deployed to production on 2026-09-11 together with the SM-P0-10 backfill.

**Harness note, not a product defect:** `scripts/test-backend.sh` bounds the portable partition at `timeout 300s`, and that bound now expires at ~53% on this host. Run the same selection directly with a longer timeout to get a real baseline:

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

Re-run `git status`, `git log --oneline -5`, and re-confirm the Alembic head before doing anything else. If any of the above has drifted, reconcile against the live repository rather than trusting this file.

## What SM-P1-01 is

Specification §15.2, row `SM-P1-01`: *"Semantic review UI — Review concept assignment, qualifiers, external mappings and evidence in Workspace/organisation review."* Effort M.

It is the keystone of P1. A general review on 2026-09-12 established that the P0 data model is complete and its **entry points are not**, in five places at once:

- Zero production importers for `semantic_concepts` and `concept_external_references` — no `SemanticConcept` is ever created, so the whole concept layer (SM-P0-01/-02/-03) is structurally unreachable.
- No production caller for `transition_symbol_semantic_assignment`, `transition_symbol_revision_classification`, `transition_rights_record` or `record_asset_transformation` — nothing can verify an assignment, approve a rights record, or record asset lineage.
- `publication_gate.enforce_publication_gate` is live on both promotion paths but fires only on `package_type='authoritative_library'`; the only creator of one, `source_package_acquisition.register_source_package`, is called from tests only. The gate is `not_in_scope` for 100% of production traffic.
- No `routes/` file imports any semantic-model module, and `frontend/src` has zero references to it.

So SM-P1-01 unblocks §16.1's *"The review workflow can see proposed semantic/classification/source assertions with evidence and status"*, and it is the prerequisite for the first authoritative-ingestion connector (SM-P2-01/-02) — without it, the first ingest is either all-§9.2-waiver or all-refusal.

**It is a routes-and-UI package, not a domain-logic package.** Every governance act already exists as a tested service function with a frozen vocabulary; plan §1.1 tabulates them. The only genuinely new service code is the cross-target review-queue query (plan §1.8, WP1.1), which does not exist in any form today.

## Required reading before writing anything

1. `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — **the controlling plan.** Read it in full, including §4's open decisions and §6's prohibited side effects.
2. `docs/SymGov_Semantic_Model_Classification_Change_Specification_v0.1.docx` — §15.2 (the package row), §16.1–16.2 (acceptance), §9.2 (gate dimensions), §12.1–12.3 (migration phases and the legacy-field policy), §13.1–13.2 (traceability dimensions and level), §14.2 (private/public boundary), §14.4 (audit and retention), §17 (decision register).
3. `backend/symgov_backend/publication_gate.py:1-120` — its module header already measures which §9.2 dimensions are satisfiable and why. Do not re-derive this.
4. `docs/plans/2026-09-11-classification-industry-field-defect.md` — the Industry/Application and process-category vocabulary gaps, and why no scheme may be seeded from existing data.
5. `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md` — plan/closing-audit format precedent, if a closing audit is needed at the end.
6. `CLAUDE.md` (repo root) — governs every session. In particular: keep human-readable symbol IDs and operator-readable timestamps prominent and never substitute UUIDs in compact UI; preserve the Workspace / Reviews / Standards product split; do not invent production metrics, workflow states or backend contracts; never run `build:publish`, `publish:static`, deployment, service-restart or migration commands without Chris's explicit approval; never push without approval; report only tests actually run.

## Load this Hermes skill

`symgov-feature-implementation` (`/root/.hermes/profiles/symgov/skills/symgov/symgov-feature-implementation/SKILL.md`) — scoped worktrees, strict vertical-slice TDD, interruption-safe checkpoints, evidence-separated verification. This is the same skill every prior stage's implementation used.

Do **not** load `symgov-programme-planning`: the plan already exists and does not need reproducing. Do not load `symgov-release-operations` either — SM-P1-01 stops at repo-side completion; there is no deployment in this package.

## Measured facts the new session must not re-derive or contradict

- **The review population is 132 assignments across 73 of 96 production symbols**, all `proposed` / `legacy_backfill` / `primary`, zero verified, applied 2026-09-11. They belong to already-published symbols and most have no open `ReviewCase`. Every existing human review lane (`classification_review`, `raster_split_review`, `provenance_rights_review`, `routes/workspace.py:148`) is intake-scoped. This is why plan §4 Q1 is a real question.
- **A `legacy_backfill` assignment can never be verified**, by design (§12.3) — `ck_symbol_revision_classifications_backfill_not_verified`, verified against the live production database. It can only be rejected and re-proposed with a real method. The UI must present that as the available action, not an approve button the database will refuse.
- **The M5 catalogue read is ON in production** (`SYMGOV_CATALOG_CLASSIFICATION_ASSIGNMENTS_ENABLED=1`). It repaired three facets that had always returned nothing: `Piping / P&ID` 0→14, `Instrumentation & Controls` 0→12, `Vessels / Tanks` 0→6.
- **The `NON_DISPLAY_MATCH_BASES` deny-list stays exactly as it is.** SM-P0-09's coarsening reversal (`56677a9`) exists because the legacy taxonomy maps were built for browse bucketing and using them to rewrite a durable column destroyed distinctions operators had recorded — production rewrites went 63 → 0. A review UI adds a reviewer's own choice as a *new* candidate; it does not re-open that question.
- **`propose_intake_rights_record` writes an `ai_assisted` proposal, which is unapprovable by constraint.** A reviewer needs a route to propose their own record — that is plan WP1.3, conditional on Q4.

## Deliberately out of scope — record, do not fix, do not silently re-open

- **Industry/Application and process-category vocabularies — Chris is researching both separately.** No scheme is seeded by any work package in this plan. ICS is the intended Industry/Application source pending licensing confirmation.
- **`classification_records.industry`** — logged as a defect 2026-09-11; four hard-coded values, three of them discipline names. Whether it is an industry axis at all is still open.
- **Catalogue facet over-matching** — `catalog_search.py:125` ORs an unconditional `payload_json` ILIKE into every facet, so `Equipment` and `Process` match all 84 published symbols. Pre-existing, unassigned, and a behaviour change to the legacy path that needs its own decision.
- **§7.3 `SemanticConceptRelationship`** — has no table and appears in no §15.1 P0/P1/P2 row, which is why `parentEquipmentClass` can only be recorded as a `no_relationship_table` gap. A missing P0 table; it should become its own package before SM-P1-04.
- **`publication_gate_evaluations` is write-only** — all six §13.1 dimensions and the §13.2 level recorded for every publication, read by nothing. That is SM-P1-06's data source, already accumulating.
- **M3 (provisional `SemanticConcept` candidates)** — the one §12.1 phase never delivered, deliberately: no grouping rule and no review queue existed, so candidates would sit unreviewable. Manual concept creation lands first in this package; M3 is a later package.
- **`ConceptTerm` multilingual table and organisation-scoped concepts** — deferred by §17 decision, not rejected.
- **A unified governance decision-history table** serving both §7.10's missing rejection actor and §7.12's who/why — deferred during SM-P0-06, not rejected.

## Open decisions — present to Chris, do not resolve unilaterally

Plan §4 carries six, each with a recommendation and the evidence behind it. Summarised:

- **Q1** — standalone semantic review queue vs. a new `ReviewCase` stage vs. both. *Recommend standalone*; the backfilled rows have no `ReviewCase` and cannot be given one.
- **Q2** — who may perform each governance act. *Recommend* platform admin for concept lifecycle (matching §17's platform-level authority), `admin`/`reviewer` for assignment, classification and mapping decisions. A governance boundary, so confirm rather than default.
- **Q3** — feature flag. *Recommend* `SYMGOV_SEMANTIC_REVIEW_ENABLED`, default off, I-20 pattern.
- **Q4** — does this package include rights approval (WP1.3)? *Recommend yes*; without it the gate's rights dimension is permanently unsatisfiable and SM-P2 ingestion stays blocked.
- **Q5** — is the organisation-promotion dual-write repair (plan §1.4) in this package, sequenced first? *Recommend yes*; it is a P0 defect against phase M4, and a review queue built over a knowingly incomplete population wastes its first real use.
- **Q6** — may the UI assign into the three seeded-but-unused schemes (`USE-CASE`, `DOCUMENT-TYPE`, `REPRESENTATION-TYPE`)? *Recommend read-only in v1*; giving them their first writer is a product decision about what SymGov asks reviewers to record.

Q1, Q2 and Q5 block WP1.0/WP1.1. Q3 blocks WP1.2. Q4 blocks WP1.3. Q6 blocks WP1.4 only.

## Prompt

> Continue the Symgov semantic model programme in `/docker/openclaw-hz0t/data/symgov`. SM-P0-01 through SM-P0-10 are complete, committed through local `HEAD` `b157e04`, fully pushed, and deployed to production as `stage11-56677a9`; the sole Alembic head is `20260911_0057`. We are starting P1, beginning with SM-P1-01 (semantic review).
>
> The controlling plan already exists and does **not** need reproducing: read `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` in full first, then `docs/plans/2026-09-12-sm-p1-01-kickoff-prompt.md` (this file, for the frozen baseline and the measured facts), then `CLAUDE.md`, then the specification sections the plan names (§15.2, §16, §9.2, §12, §13, §14.2, §17), then `backend/symgov_backend/publication_gate.py:1-120` — its module header already measures which §9.2 dimensions are satisfiable, so do not re-derive that. Load the Hermes skill `symgov-feature-implementation`; do not load `symgov-programme-planning` or `symgov-release-operations`.
>
> Re-verify the git and Alembic baseline before assuming anything in either document is still accurate, and re-establish the regression baseline with the long-timeout pytest invocation the kickoff gives — `scripts/test-backend.sh`'s own 300s bound expires at ~53% on this host, which is a harness limit, not a product regression. The baseline to hold is 3696 passed / 3 skipped / 3 deselected.
>
> Your first action is to put the plan's §4 open decisions (Q1–Q6, six of them, each with a recommendation already written) to me and get my answers. Do not resolve them yourself and do not start WP1.0 or any later package before the decision it depends on is confirmed — Q1/Q2/Q5 block WP1.0 and WP1.1, Q3 blocks WP1.2, Q4 blocks WP1.3, Q6 blocks WP1.4. Once I have answered, execute the work-package sequence in plan §2 in order, each as its own vertical slice with its own tests and its own go-ahead from me before it starts.
>
> Hold the plan's §6 prohibited side effects as invariants: no seeding of an Industry/Application or process-category scheme (I am researching both vocabularies separately), no change to SM-P0-09's `NON_DISPLAY_MATCH_BASES` derivation rules, no change to `automation_policy.evaluate_publication_automation_gate` or `provenance_assessments`, and leave `.claude/settings.local.json` and `UI-Design/` untouched. Do not commit, push, migrate, deploy, restart a service, or activate a feature flag without my explicit go-ahead for that specific action.
