# SM-P1-01 WP1.5 kickoff prompt

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This is the restart pack for **WP1.5, the read-only semantic panel in the
existing review surfaces**. The controlling plan —
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — is
committed, its §4 decisions are all resolved (now Q1–Q10), and WP1.0 through
WP1.4 plus the 2026-09-14 amendment are delivered, committed **and pushed**.
The new session's job is to execute one work package, not to plan.

**What differs from the WP1.4 pack, and it is the thing that matters:**
WP1.5's scope was **re-scoped on 2026-09-14** after its two target surfaces
were measured against the tables and found empty at decision time. Decisions
Q9 and Q10 settle what replaces it. §3 below is the finding; do not re-derive
it and do not revert to the plan's original WP1.5 sentence.

---

## 1. Frozen baseline (captured 2026-09-14)

- Branch `main`, `HEAD` = `2c1aab1` ("feat: make the re-proposal remedy reachable (SM-P1-01 WP1.2/WP1.4 amendment)").
- **`main` is level with `origin/main`.** `git rev-parse origin/main` → `2c1aab1`. Nothing is outstanding. (An earlier note claiming the amendment was unpushed is stale.)

| Commit | Package |
|---|---|
| `2c1aab1` | feat: the WP1.2/WP1.4 re-proposal amendment |
| `4441425` | feat: the semantic review surface (WP1.4) |
| `0dc1378` | docs: the WP1.4 kickoff prompt |
| `3f09fb0` | docs: the plan and WP1.0–WP1.3 delivery notes |
| `b4ffbaa` | feat: the semantic and rights review API (WP1.2, WP1.3) |

- Sole Alembic head: `20260911_0057`. No package in SM-P1-01 has added a migration and **WP1.5 must not either** — nothing in Q9's shape needs one.
- Production runs release `stage11-56677a9`. **The whole semantic review API and surface are undeployed, and the flag has never been activated.**
- **Backend regression baseline: 3958 passed, 3 skipped, 3 deselected** (portable partition, Postgres included, ~1476s). No work package may lower this.
- **Frontend baseline: 335 passed, 52 suites, 0 failures** via `npm run test:frontend` (~12s, re-measured 2026-09-14). `npm run build` succeeds: 86 modules, `dist/assets/index-*.js` 642.66 kB. The >500 kB chunk-size warning is pre-existing and is not yours.

### 1.1 The only working-tree items are not yours

`git status --porcelain` should show exactly:

```
 M .claude/settings.local.json
?? UI-Design/
```

Both predate this programme. **Leave both untouched.** If anything else
appears — other than this pack and the plan amendment that introduced it —
stop and reconcile before editing.

### 1.2 Test invocations

Frontend: `npm run test:frontend` (wraps `node --test frontend/src/*.test.js`,
default 120s bound, raise with `SYMGOV_FRONTEND_TEST_TIMEOUT_SECONDS`).

Backend: `scripts/test-backend.sh` bounds the portable partition at
`timeout 300s`, which expires at ~53% on this host. That is a harness limit,
not a product regression. Use:

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

## 2. What WP1.5 was, and why it changed

The plan's original sentence:

> Embed a read-only "Engineering meaning" summary into the existing review
> case detail and the organisation symbol review page, so an SME reviewing an
> intake sees the proposed semantic state without leaving the lane. Read-only
> deliberately: the decision controls stay in one place (WP1.4) so there is
> one audit path.

That could not be built as written. §3 is why.

---

## 3. The finding — measured, not inferred. Do not re-derive it.

**Every governed semantic assertion in the system is written by an approval
handoff that runs after the review the panel would sit in.**

`SymbolRevisionClassificationAssignment` and `RightsRecord` rows are created
at exactly two call sites — `publication_handoff.py:604`/`:891` (intake
approval) and `organization_promotion_handoff.py:266` (promotion approval,
WP1.0's repair). Both run inside `execute_publication_handoff`, on the
decision itself.

1. **The intake review case detail has no symbol revision to ask about at
   all.** `/workspace/review-cases` (`workspace.py:2457`) lists only open
   cases (`closed_at IS NULL`) whose `source_entity_type` is
   `validation_report` or `provenance_assessment`. Neither has a
   `symbol_revisions` row: `ensure_approved_symbol_revision`
   (`publication_handoff.py:479`) *creates* the revision from the
   `HumanReviewDecision`, with a deterministic id derived from that decision.
   There is no identifier to pass to
   `GET /semantic-review/symbol-revisions/{id}`.
2. **The organisation symbol review page has a real revision id and nothing
   hanging off it.** `OrganizationSymbolDraftResponse.currentRevision.id`
   (`schemas.py:1383`) is a genuine `symbol_revisions` row and
   `_visible_revision` would resolve it for an organisation-mode session —
   but no path writes assignments to an organisation draft revision before
   promotion.

So the panel as specified would render "no semantic assertions yet" in exactly
the population it was designed for.

---

## 4. What to build instead (decisions Q9 and Q10)

### 4.1 Q9 — the intake panel forecasts rather than reports

On the intake review case detail, show **what approving this case will
assert, and which §9.3 fields will fall into a gap**. That is the one thing
genuinely non-empty before the decision, and it sits beside the raw
`ClassificationRecord` fields the detail pane already renders as Discipline /
Format / Industry / Symbol family.

**The forecast must be a composition of the existing mapping path, never a
restatement of its rules.** A second copy of the precedence would drift from
the one that actually writes, and a panel that forecasts something other than
what approval does is worse than no panel:

```
context           = load_review_context(session, review_case)               # publication_handoff.py:229
classification    = context["classification_record"]
symbol_properties = load_review_symbol_properties(session, review_case=...)   # publication_handoff.py:196
fields            = classification_fields_from_record(                       # classification_mapping.py:462
                        classification,
                        discipline=symbol_properties.discipline if symbol_properties else None,
                        category=symbol_properties.category  if symbol_properties else None)
plan              = plan_classification_mapping(fields)                      # classification_mapping.py:332
resolved          = resolve_node(session, scheme_code=..., candidates=...)    # classification_mapping.py:534
```

Three things about that composition, each load-bearing:

- **`plan_classification_mapping` is pure by its own docstring** — "no
  session, no clock, no identifiers. Every rule in this function is provable
  in `tests/test_classification_mapping.py` with no database." That is what
  makes the forecast cheap and testable.
- **The `discipline`/`category` precedence is not yours to choose.** It is
  `record_classification_mapping`'s (`publication_handoff.py:364-367`): the
  human-reviewed `ReviewSymbolProperty` wins over the classification record.
  The Reviews surface lets an SME edit those properties in place, so a preview
  that skipped the override would forecast the wrong node the moment a
  reviewer corrected a discipline. **There must be a test that fails if the
  override is dropped.**
- **Resolve the candidates; do not list raw codes.** A candidate that resolves
  to no active node is a gap the SME would otherwise not discover until after
  approval. `resolve_node` returns the node *and* the match basis, so the
  panel can say how the value was matched.

**Route.** One new read on the **existing** `semantic_review` router:

```
GET /semantic-review/review-cases/{review_case_id}/classification-preview
```

Same default-off `SYMGOV_SEMANTIC_REVIEW_ENABLED` flag, same
`require_any_role({"admin", "reviewer"})` boundary, same 422 envelope as its
sixteen siblings. WP1.5 therefore changes nothing in production until the flag
is activated under its own separate approval.

**No tenant predicate, and the reason is measured rather than assumed.**
Neither `IntakeRecord` (`models/schema.py:2053`) nor `ClassificationRecord`
(`:2121`) carries an organisation, and the intake lane feeds the public
catalog. §14.2 is about organisation-private *symbol existence*; a review case
naming no symbol and no organisation gives it nothing to protect. This is the
same reasoning that left `GET /semantic-review/classification-schemes`
unscoped. State the argument in the docstring — the convenience is not the
reason.

**Frontend.** Embed in `ReviewsPage`'s focus pane (`App.jsx:5069`), in the
`copy-block` idiom, beside the existing `review-support-facts`. **Label it
plainly as a forecast of the approval, never as recorded state** — `CLAUDE.md`
forbids presenting an illustrative value as a production one. `/reviews` is
already gated `RequireAnyRole roles={['admin','reviewer']}` (`App.jsx:547`),
an exact match for the router's boundary, so the lane needs no second role
rule — only the capability gate for the flag.

### 4.2 Q10 — the organisation page hides the panel when ungated

The governed-state panel stays in scope for the organisation symbol review
page, and **renders only for a session that satisfies the API's own boundary
and the flag; otherwise it is absent** — no failed request, no empty frame, no
promise the API will not keep.

The page is gated on an *organisation capability* —
`canReviewOrganizationSymbols` → `symbol_reviewer`, or organisation
`baseRole === 'admin'` (`projectContext.js:80`) — which is a different axis
from the platform role the router requires. An organisation reviewer without
platform `admin`/`reviewer` would otherwise meet a 403. This mirrors
`semanticReviewJourney.js`, which already reproduces the API boundary rather
than inventing a stricter or looser one.

**Widening the router to accept the organisation capability was considered and
rejected** — it would re-open decision Q2, which §4 records as settled. Do not
propose it again.

`activeDraft.currentRevision.id` is the identifier to pass to
`GET /semantic-review/symbol-revisions/{id}`; that read already exists and
WP1.4 already consumes it. Reuse `fetchSemanticReviewSymbolRevision`
(`api.js:1960`) rather than adding a helper.

### 4.3 Read-only throughout

The decision controls stay in WP1.4 so there is one audit path — exactly what
the original sentence intended, and the one part of it that survives intact.

---

## 5. Explicitly out of scope

- **`provenance_assessments` / the §13.1 publication-gate evaluation.** Plan
  §2 says WP1.5 "may surface a single revision's latest evaluation read-only
  if Q4 allows". Q9 did not take that up. It stays SM-P1-06's, and §6's
  prohibition on touching `automation_policy.evaluate_publication_automation_gate`
  and `provenance_assessments` is unchanged.
- **The promotion review panel** (`PlatformAdminPage.js:646`). It is a
  two-UUID form that says in its own copy "Accept-only: reject/changes-requested
  handling is not yet built". It is not the "existing review case detail" WP1.5
  names, and giving it a semantic panel would be widening scope.
- **Any rights forecast.** Q9's shape is the classification mapping only.
  `record_intake_rights_proposal` is a separate path and was not measured for
  this package.
- **The unfixed sibling defect in `propose_external_mapping`** (a duplicate
  active mapping escapes as a 500). Recorded by the 2026-09-14 amendment for
  scheduling; it is not reachable from any UI and is not WP1.5's.

---

## 6. Required reading before writing anything

1. `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — the controlling plan. §2's WP1.5 entry (re-scoped), §4 Q1–Q10, §6 prohibited side effects.
2. `backend/symgov_backend/routes/semantic_review.py` — **read the module docstring in full**, plus `_scope` (`:179`), `_invalid` (`:200`) and `classification_scheme_options` (`:587`), which is the closest precedent for an unscoped read on this router.
3. `backend/symgov_backend/classification_mapping.py` — `plan_classification_mapping` (`:332`), `classification_fields_from_record` (`:462`), `resolve_node` (`:534`), and the `PlannedAssignment`/`MappingGap`/`ClassificationMappingPlan` shapes (`:174-231`).
4. `backend/symgov_backend/publication_handoff.py` — `load_review_symbol_properties` (`:196`), `load_review_context` (`:229`), `record_classification_mapping` (`:329`, **its docstring carries the precedence rule**).
5. `frontend/src/App.jsx` — `ReviewsPage` (`:4550`) and its focus pane (`:5069`).
6. `frontend/src/OrganizationSymbolReviewQueuePanel.js` — 213 lines, the whole file.
7. `CLAUDE.md` (repo root) — governs every session. Its UI section is directly on point.

---

## 7. Load this Hermes skill

`symgov-feature-implementation`
(`/root/.hermes/profiles/symgov/skills/symgov/symgov-feature-implementation/SKILL.md`)
— strict vertical-slice TDD, interruption-safe checkpoints,
evidence-separated verification. Do **not** load `symgov-programme-planning`
(the plan exists) or `symgov-release-operations` (nothing here deploys).

---

## 8. Prohibited side effects (invariants, not preferences)

- No migration. The head stays at `20260911_0057`.
- The feature flag ships **default-off** and is never activated.
- No push, no deployment, no service restart, no `build:publish`, no `publish:static`, no live mutation.
- No change to `automation_policy.evaluate_publication_automation_gate` or `provenance_assessments`.
- No change to SM-P0-09's `NON_DISPLAY_MATCH_BASES` deny-list or the legacy `GovernedSymbol.category`/`.discipline` derivation.
- No seeding of an Industry/Application or process-category scheme.
- Leave `.claude/settings.local.json` and `UI-Design/` untouched.
- Human-readable symbol IDs and operator-readable timestamps stay prominent; do not substitute UUIDs in compact UI.

---

## Prompt

> Continue SM-P1-01 in `/docker/openclaw-hz0t/data/symgov`, executing WP1.5 (the read-only semantic panel in the existing review surfaces).
>
> Read `docs/plans/2026-09-14-sm-p1-01-wp15-kickoff-prompt.md` first — it is the restart pack. The working tree is clean apart from the modified `.claude/settings.local.json` and the untracked `UI-Design/`, both of which predate this programme and neither of which is yours. `main` is level with `origin/main` at `2c1aab1`; WP1.0–WP1.4 and the 2026-09-14 amendment are all committed and pushed. Then read the controlling plan `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` in full (§4's Q1–Q10 are all resolved — do not re-litigate them), then the module docstring of `backend/symgov_backend/routes/semantic_review.py`, then `CLAUDE.md`.
>
> Load the Hermes skill `symgov-feature-implementation`. Do not load `symgov-programme-planning` or `symgov-release-operations`.
>
> Re-verify the git and Alembic baseline before assuming the pack is accurate. The baselines to hold are **3958 passed / 3 skipped / 3 deselected** on the backend portable partition and **335 passed / 0 failed / 52 suites** on `npm run test:frontend`, with `npm run build` succeeding.
>
> **WP1.5 was re-scoped on 2026-09-14 and every decision is already made — there are none outstanding and none to ask me about.** The pack's §3 carries the measured finding that forced it: every governed semantic assertion is written by an approval handoff that runs *after* the review the panel would sit in, so both of the plan's original target surfaces are empty at decision time. §4 carries what replaces it — Q9, an intake panel that forecasts what approving the case *will* assert plus the fields that fall into a gap, composed from the existing mapping path and never restating its rules; and Q10, an organisation-page panel that is absent unless the session satisfies the router's own role boundary and the flag. Do not re-derive either, and do not revert to the plan's original WP1.5 sentence.
>
> Execute WP1.5 as vertical slices with strict TDD. Hold §8's prohibited side effects as invariants: no migration, the flag ships default-off and is never activated, no commit unless I ask, **no push**, no deployment, no service restart, and no `build:publish` or `publish:static`. Stop at repo-side completion of WP1.5; WP1.6 is a separate package.
