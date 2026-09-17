# Semantic Model & Classification — SM-P1-02 Industry Axis Implementation Plan

**Written 2026-09-17, at `e1873fd`, against defect §4.2 item 4 of
`docs/handover/2026-09-14-symgov-state-of-play.md`.**

This plan closes the defect recorded in
`docs/plans/2026-09-11-classification-industry-field-defect.md`:
`classification_records.industry` has one writer, four hard-coded values, no
editor and no governed vocabulary.

It is written **after** the two decisions that unblocked it, both taken by
Chris on 2026-09-17 and recorded in that defect document:

- **ICS licensing is closed** — ODC-By v1.0 via ISO Open Data. Embedding node
  labels and scope notes in the UI and API is permitted.
- **`industry` is a real axis.** It is not retired in favour of
  `ENGINEERING-DISCIPLINE`, and ICS is its governed vocabulary.

Nothing below re-opens either. What remains is entirely *how* the axis gets a
value, a corrector and a consumer.

---

## 1. Baseline — measured, not assumed

Every claim here was read out of the repository at `e1873fd`.

### 1.1 The vocabulary already exists in the repository, as draft

`ics_taxonomy.py` creates scheme `ISO-ICS-7` with **`status="draft"`** and
1381 nodes, each also `draft` (`ics_taxonomy.py:270-292`). The ingestion
machinery — `ics_import` (network + archive), `ics_taxonomy` (storage),
`ics_review` (disposition) — landed in `e6cd860` and migration
`20260916_0059`. `ICS.csv` is vendored and pinned by SHA-256.

**It has never been applied to any database.** The 22 domain→ICS crosswalk
mappings dispositioned on 2026-09-16 (16 approved, 6 rejected) exist as
decisions in a runbook register, not as rows anywhere.

### 1.2 Nothing can select an ICS node

`REVIEWER_ASSIGNABLE_SCHEME_CODES` is
`frozenset({"ENGINEERING-DISCIPLINE", "SYMBOL-CATEGORY-FAMILY"})`
(`routes/semantic_review.py:182`). It gates two things: the scheme/node
options route (`:655`) and the proposal route's refusal (`:1214`). `ISO-ICS-7`
is in neither, so no reviewer can propose an ICS assignment even once the
scheme exists.

### 1.3 The mapping layer records `industry` as a permanent gap

`plan_classification_mapping` does not call `facet()` for `industry` at all.
It appends an unconditional `MappingGap(field="industry", reason="no_scheme",
detail="Industry/Application scheme is not seeded; ICS is the intended
source")` (`classification_mapping.py:399-407`). Both the promotion path
(SM-P0-07) and the legacy backfill (SM-P0-10) route through this one
function, so both record the gap identically.

### 1.4 The existing matcher cannot bridge Libby's values to ICS codes

`_candidate_node_codes` is three deterministic rules — exact code folding, a
one-character plural variant, and the catalogue's legacy short-form tables —
and **none of them is similarity matching**, which §16.2 forbids
(`classification_mapping.py:_candidate_node_codes`).

This matters more than anything else in this plan. Libby's four values fold
to `GENERAL_INDUSTRY`, `MECHANICAL_ENGINEERING`, `PROCESS_ENGINEERING` and
`ARCHITECTURAL`. ICS node codes are dotted numerics (`21`, `13.220.20`) with
English titles (`Mechanical systems and components for general use`).
**No existing rule can match one to the other, and inventing one would be the
similarity matching §16.2 forbids.** So the value cannot simply be re-pointed
at a new scheme: where the value comes from is the decision this plan turns
on, and it is D1 below.

### 1.5 The writer and the absent corrector are unchanged

`scripts/run_libby_classification.py` is still the sole producer, with the
four branches at `:469`, `:532`, `:542`/`:552` and `:572`. The vision review
never writes the field, and `industry` appears nowhere in `frontend/src`.

---

## 2. Decisions required before implementation — Chris's

**Answered by Chris on 2026-09-17: D1-a, D2-a, D4 as recommended, and D3
activate.** Each subsection keeps the question and the options as written,
because the reasoning is what makes the answer reviewable; the ruling is
recorded at the head of each. **D3 carries operational findings that were not
known when the question was put — see "D3, as answered" below.**

**These four are genuinely product decisions and the plan does not assume
answers.** WP2.1 must not start before D1 and D2 return.

### D1. Where does `industry` get its value? *(the plan turns on this)*

**Answered 2026-09-17 (Chris): D1-a**, derivation from `standards_source`
provenance, with D1-b as the fallback for any symbol whose source standard
carries no ICS code. D1-c is closed.

The 2026-09-11 rationale for choosing ICS was explicit: *"every standard
SymGov models already carries ICS codes, so `industry` can be derived from
`standards_source` provenance rather than guessed."* That is an argument for
**derivation, not heuristics**. Three options:

| | Source | What it costs | What it buys |
|---|---|---|---|
| **D1-a** *(recommended)* | Derive from `standards_source` provenance: the source standard's own ICS codes, through SM-P0-05's `source_standards`/`source_packages` machinery | Requires an ICS code per source standard — a data-entry task over the seeded standards, about 238 CFIHOS rows plus the rest | Provenance-backed, not guessed. Matches the stated rationale. No similarity matching. |
| **D1-b** | Reviewer-only: no automatic proposal at all; `industry` is assigned by a human from the ICS picker | Nothing automatic; every symbol starts with no industry | Honest. Cheapest. Zero wrong values. |
| **D1-c** | A curated static map from the four Libby values to ICS nodes | Four mappings, written once | Preserves continuity with existing rows — but three of the four values are *disciplines*, so the map would encode the very confusion the defect is about |

**Recommendation: D1-a, with D1-b as the fallback for any symbol whose
source standard has no ICS code.** D1-c is not recommended: it launders a
known-wrong value into a governed assignment.

### D2. What happens to the existing values?

**Answered 2026-09-17 (Chris): D2-a.** The four legacy values stay in
`evidence_json` as the historical record; no assignment is written from them.

Every `classification_record` in production carries one of the four. Under
D1-a or D1-b none of them is a valid ICS assignment.

- **D2-a** *(recommended)*: leave them in `evidence_json` as the historical
  record, write no assignment from them, and let the new path populate going
  forward. Matches SM-P0-07's existing treatment and the `20260909_0052`
  precedent.
- **D2-b**: backfill via D1-c's map. Not recommended, for D1-c's reason.

### D3. Does `ISO-ICS-7` become `active`, and who activates it?

**Answered 2026-09-17 (Chris): yes, activate it, as
`chris.brighouse@hotmail.co.uk`.** What that instruction meets in the code is
recorded under "D3, as answered" below; the act is not performable today and
the reasons are not a matter of judgement.

The scheme imports as `draft` by deliberate design — *"activation requires
separate governance authority"* (`ics-taxonomy-runbook.md`). Reviewers cannot
be offered draft nodes. So activation is a prerequisite, and it is a
governance act, not an implementation step. **Ask: who performs it, and is
the whole 1381-node tree activated or only the ~40 fields in use?**

### D4. Which ICS depth is assignable?

**Answered 2026-09-17 (Chris): as recommended** — field level, group level
where a reviewer wants precision, never subgroup.

ICS is three levels (40 fields → 401 groups → 940 subgroups). Offering all
1381 as a flat picker is unusable. **Recommendation: assign at field level
(the 40), allow group level where a reviewer wants precision, and never
subgroup.** This is a UI and validation decision, not a data one.

---

### D3, as answered — four findings, measured 2026-09-17

The instruction is recorded and stands. It cannot be carried out yet, and
none of the four reasons is a preference:

1. **ICS is not in production at all.** Migrations `20260915_0058` and
   `20260916_0059` are undeployed — production's `alembic_version` is
   `20260911_0057` — and the `ics_import --apply` run has never happened
   there. There is no `ISO-ICS-7` scheme row in production to activate.
   That is WP2.0, and it is a separately approved production operation.
2. **Nothing in the product can activate a scheme.**
   `set_classification_scheme_status` exists in `classification_schemes.py`,
   is tested, and has **no caller anywhere outside the tests** — no route, no
   CLI, no service. This is the same defect shape as §4.3 item 9, which WP3.1
   just closed for concept classifications.
3. **An activation cannot be attributed to a user.** `ClassificationScheme`
   carries `created_by_user_id`, which records who *created* the scheme — the
   ICS importer, not an activating authority — and there is no column for the
   actor behind a status change. `set_classification_scheme_status` writes
   `status` and `updated_at` and takes no `actor` argument. So
   "as `chris.brighouse@hotmail.co.uk`" cannot be recorded against the scheme
   row as the model stands. Making a governance act attributable is a schema
   change, not a configuration one, and the runbook's own phrase — "separate
   governance authority" — currently names no mechanism.
4. **Whether that account is a Platform Administrator in production is
   unverified.** It has not been checked, and checking it is a production
   read.

**The ODC-By gate is not among these.** Activation alone conveys no ICS label
to anyone: the SME options route restricts choices to its existing allowed
schemes and nothing renders a node. The attribution obligation bites at
WP2.1/WP2.3, where a label first reaches the UI, which is where the plan
already places it.

**Recommended sequence, in this order:** WP2.0 (deploy + import + crosswalk
register) → an attributable activation path, sized with WP2.1 → the
activation itself, performed as an approved production operation.

---

## 3. Work packages

Sequenced so that each is independently gateable and nothing half-lands.

### WP2.0 — Apply the ICS import to production *(operations, not code)*

Prerequisite for everything else. The migration release through
`20260916_0059` and the `ics_import --apply` run are **separately approved
production operations** per `CLAUDE.md` and the runbook. Includes recording
the 22 already-dispositioned crosswalk decisions through `ics_review`, which
is the only way they reach storage.

*Gate:* the runbook's read-back SQL block.

### WP2.1 — Make `ISO-ICS-7` assignable

- Add `ISO-ICS-7` to `REVIEWER_ASSIGNABLE_SCHEME_CODES`, honouring D4's depth
  rule in the options route rather than by widening the allowlist alone.
- The options route currently returns every node of an allowed scheme; 1381
  nodes in one response is a contract change worth bounding here.
- The `uq_symbol_revision_classifications_active_node` index already prevents
  a duplicate live assignment, and `propose_symbol_classification` already
  refuses it with the 422 envelope — no new conflict handling is needed.

*Gate:* `sh scripts/test-backend.sh`; route-policy matrix extended for the
new scheme; a PostgreSQL test that the depth rule actually bounds the
response.

### WP2.2 — Give `industry` a writer *(shape depends entirely on D1)*

Under **D1-a**: a resolver from `standards_source` → ICS code → node, plugged
into `plan_classification_mapping` where the unconditional gap currently
sits. The gap stays for records whose source standard has no ICS code — with
a *new, distinguishable* reason (`no_source_ics_code`), never silently
dropped, satisfying §16.1.

Under **D1-b**: `plan_classification_mapping` keeps its gap and WP2.2 is
struck; the axis is reviewer-populated only.

**Whichever is chosen, the value must route through `plan_facet`**, so
promotion and backfill continue to agree by construction — the property
SM-P0-09's derivation depends on.

*Gate:* `tests/test_classification_mapping.py` (pure, no database) plus the
PostgreSQL mapping tests.

### WP2.3 — Give reviewers a corrector

The third "Wanted" item, and the only one that makes a wrong value fixable.
The mechanism already exists: `SemanticReviewPage.js` and the propose/decide
routes. This is composition, not new governance — an ICS picker in the review
surface, bounded by D4's depth rule.

**Carries the ODC-By gate.** This is the work package where ICS labels first
reach the UI, so the attribution notice and the codes-only clarification must
ship *in the same change*. See §4.

*Gate:* `npm run test:frontend`, plus the mounted-journey pattern used by
`semanticReviewMountedJourney.test.js`.

### WP2.4 — Retire or repoint the four heuristic branches

Only after WP2.2 has a working writer. Leaving `run_libby_classification.py`
writing discipline names into an axis that now means something else would
reintroduce the defect at the source.

---

## 4. The ODC-By attribution gate — binding on WP2.3

ODC-By v1.0 requires the attribution notice to travel with the data wherever
it is conveyed. The attribution, licence URL and codes-only clarification are
stored per import (`license_code`, `license_url`, `attribution`, and both
strings in the scheme description). Measured again 2026-09-17, and the earlier
measurement in this plan was **wrong**: `frontend/src/SupportDataSources.jsx`
already renders `source.attribution`, `source.clarification` and an explicit
"Open Data Commons Attribution License (ODC-By) v1.0" link, is mounted on the
Support route (`frontend/src/App.jsx`), and is covered by
`frontend/src/supportDataSources.test.js`. It landed in `e6cd860`, the same
commit as the ICS ingestion, so it predates the claim. The general notice is
therefore already carried.

**What actually remains is narrower than "surface the attribution".** The
Support page reads its strings from the vendored
`backend/symgov_backend/data/ics-source.json`, not from the stored
`ics_taxonomy_imports` row, so a re-import can leave the UI asserting a stale
licence; and the notice sits on Support rather than beside the labels
themselves. **WP2.3 must not merge without** the attribution travelling with
the ICS labels it renders, read from the stored import rather than re-typed.
Nothing conveys an ICS *label* today — the scheme is draft and excluded from
the options route — so this remains a precondition on activation rather than
a live breach.

---

## 5. Prohibited side effects

- No similarity matching, ever (§16.2). If a value cannot be resolved
  deterministically, it is a gap.
- No `industry` assignment written from a placeholder. `general_industry` is
  already in `PLACEHOLDER_DISCIPLINES` and must not become an ICS node.
- No silent dropping of a §9.3 field. Every unresolved `industry` value keeps
  a gap row with a reason that distinguishes *why*.
- No `GRANT` on any new table — §4.4 item 10 of the state-of-play, and the
  measured precedent of SM-P0-04 through -08.
- No production apply, migration or feature activation without Chris's
  explicit approval for that specific operation.

---

## 6. What this plan does not close

- **`processCategory`** — the sibling gap at
  `classification_mapping.py:409-416`, likewise unseeded and likewise Chris's
  own research. Deliberately out of scope.
- **§4.3's structural gaps** — covered by the companion plan
  `2026-09-17-sm-p1-03-structural-gaps-implementation-plan.md`.
- **The three dead schemes** (`USE-CASE`, `DOCUMENT-TYPE`,
  `REPRESENTATION-TYPE`) — decision Q6 keeps them read-only; see the
  companion plan.
