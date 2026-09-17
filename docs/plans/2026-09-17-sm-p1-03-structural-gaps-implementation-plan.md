# Semantic Model & Classification — SM-P1-03 Structural Gaps Implementation Plan

**Written 2026-09-17, at `e1873fd`, against §4.3 items 5–9 of
`docs/handover/2026-09-14-symgov-state-of-play.md`.**

§4.3 is not one defect. It is five findings that share a shape: **machinery
that exists and is not connected to anything.** Each was measured, and each
re-measurement below was taken at `e1873fd` for this plan.

They are deliberately kept in one plan because three of them
(items 7, 8 and 9) are the *same* disconnection seen from three sides, and
fixing one without the others produces a surface that still reports nothing.

---

## 1. The five, re-measured at `e1873fd`

### Item 5 — three seeded schemes have no writer and no reader

`USE-CASE` (seeded by `20260909_0051`), `DOCUMENT-TYPE` and
`REPRESENTATION-TYPE` (by `20260909_0052`) exist with nodes and are named in
exactly two places in the product: the Q6 exclusion comment at
`routes/semantic_review.py:175` and the refusal at `:1199`. Nothing writes
them; nothing reads them.

**This is a decision honoured, not a defect.** Decision Q6 (2026-09-12) kept
them read-only in v1. It is listed here so it is not mistaken for an
oversight, and so the question *"are they wanted at all?"* has somewhere to
be asked. **No work package is proposed.** See D5.

### Item 6 — §7.3 `SemanticConceptRelationship` has no table

The name appears in the codebase **only** as a gap reason:
`classification_mapping.py:114` defines `no_relationship_table`, and `:427`
emits it with the detail *"SemanticConceptRelationship (7.3) has no table;
CFIHOS equipment classes are the likely vocabulary"*. There is no model, no
migration and no work package.

The specification describes it as P0-shaped but it appears in no §15.1 row.
It is why `parentEquipmentClass` can only ever be recorded as a gap.
**This is a missing P0 table, not a deferral** — the distinction matters
because P0 packages are complete and this one silently is not.

### Item 7 — `publication_gate_evaluations` is write-only

`publication_gate.py:1072-1079` constructs and writes one row per
publication, recording all six §13.1 dimensions. The only `SELECT` anywhere
in the repository is in `tests/test_publication_gate_postgresql.py:863`.
**No production code reads the table.** It is SM-P1-06's data source and is
accumulating rows against a reader that does not exist yet.

### Item 8 — the gate's scope is unreachable in production

The gate fires only on `package_type='authoritative_library'`. The only
creator of one is `register_source_package`, and every caller is a test:
`test_publication_gate_postgresql.py`, `test_standard_source_precision*.py`,
`test_rights_review_routes_postgresql.py`. Re-confirmed at `e1873fd`.

**So the gate evaluates to `not_in_scope` for 100% of production traffic**,
and item 7's accumulating rows all say so. Items 7 and 8 together mean
SM-P1-06 would be built over a table whose every row is `not_in_scope`.

### Item 9 — `ConceptClassificationAssignment` has a queue read and no decision route

`/queues/concept-classifications` exists (`routes/semantic_review.py:539`)
and renders rows read-only, which WP1.4 did deliberately. The services
`propose_concept_classification` (`classification_assignments.py:154`) and
`transition_concept_classification` (`:254`) both exist and **no route calls
either**.

The consequence is the one already recorded in memory: nothing can move an
assignment to `verified`, so **every derive-from-verified read returns
empty** — a silent emptiness, not an error.

---

## 2. Decisions required — Chris's

**All four answered by Chris on 2026-09-17, each as recommended.** The
questions and their reasoning are kept below because the reasoning is what
makes the answer reviewable; each ruling is recorded at the head of its
subsection.

### D5. Are `USE-CASE`, `DOCUMENT-TYPE` and `REPRESENTATION-TYPE` wanted?

**Answered 2026-09-17 (Chris): keep them read-only** and revisit once the
industry axis is in use.

Three options: activate them (give each a writer and a reviewer path, roughly
WP2.1–2.3 of the industry plan repeated three times); keep them read-only
indefinitely (today's state, which is honest but leaves three schemes as
permanent furniture); or retire them. **Recommendation: keep read-only and
revisit after the industry axis is in use** — that will show whether a second
governed axis is wanted before three more are built.

### D6. Is `SemanticConceptRelationship` P0 or is the specification wrong?

**Answered 2026-09-17 (Chris): treat it as a genuine P0 omission.** P0 is
not complete until the table exists, so WP3.2 is unblocked in its
table-and-service half; its vocabulary half stays behind SM-P2-02.

Either §7.3 is a missing P0 table that must be built to close P0 honestly, or
the specification over-scoped it and the §15.1 omission was correct. **This
is a specification question, not an implementation one**, and it should be
answered before anyone claims P0 is complete. **Recommendation: treat it as
a genuine P0 omission** — `parentEquipmentClass` is a real field with a real
CFIHOS vocabulary waiting for it (832 equipment classes), and "it appears in
no §15.1 row" is weak evidence against the spec's own body text.

### D7. Should the publication gate's scope be widened, or should SM-P1-06 wait?

**Answered 2026-09-17 (Chris): D7-a, and in its stated sequence** — activate
SM-P1-01, observe real use, then decide the connector, then the gate scope,
then the reader. Nothing in WP3.3 or WP3.4 starts before that.

- **D7-a** *(recommended)*: widen the scope so the gate evaluates something
  real, **then** build the reader. Requires deciding which production path
  legitimately registers an authoritative library — probably the ingestion
  connector (SM-P2-01/-02), which is itself gated on SM-P1-01 being in use.
- **D7-b**: build SM-P1-06's reader now against `not_in_scope` rows. Produces
  a dashboard that correctly reports nothing.

**The standing advice in §9 of the state-of-play applies**: do not plan the
ingestion connector before SM-P1-01 is actually in use. So D7-a's real
sequence is *activate SM-P1-01 → observe → then decide the connector → then
the gate scope → then the reader.*

### D8. Does the concept-classification queue get a decision route?

**Answered 2026-09-17 (Chris): yes. Delivered the same day as WP3.1** — see
section 3. §4.3 item 9 is closed.

**Recommendation: yes, and it is the smallest high-value item in this plan.**
Both services exist and are tested; what is missing is a route of the same
shape as `decide_symbol_classification`, which is directly adjacent in the
same file. Until it exists, a governance state the model defines is
unreachable, and the emptiness is silent.

---

## 3. Work packages

Ordered by what is safe to do independently. **WP3.1 and WP3.2 are delivered**
(2026-09-17); WP3.3 and WP3.4 wait on D7's sequence and on SM-P1-01 being in
real use, and WP3.5 proposes no work.

### WP3.1 — A decision route for concept classifications *(item 9, DELIVERED 2026-09-17)*

**Delivered.** `POST /semantic-review/concept-classifications/{assignment_id}/decision`,
built exactly as specified below. Three things are worth carrying forward:

- **The response is the concept's whole classification state**, not the single
  row named — `_concept_classification_state`, modelled on
  `_concept_mapping_state`. Verifying a `primary` retires the primary verified
  before it *in the same scheme*, and a caller handed back only its own row
  could not see that succession. The frontend replaces every returned row in
  the queue for the same reason.
- **No tenant predicate, by the argument `_concept` already records**: a
  concept-to-node assertion names no symbol, concept governance is
  platform-level (§17), and WP1.1 made the identical call for the queue this
  decides on. It is registered in `UNSCOPED_BY_DECISION`, not omitted from the
  matrix.
- **The frontend's read-only notice is gone.** `SemanticReviewPage.js` said in
  as many words that "the API cannot perform" this act; it now renders
  `DecisionControls` driven by `capabilities`, so a `legacy_backfill` row is
  still barred from `verified` by §12.3 and says why.

The original specification follows unchanged.

A `POST /semantic-review/concept-classifications/{assignment_id}/decision`
modelled exactly on `decide_symbol_classification`: the same `reviewer`
dependency (Q2), the same `_visible_*` tenant predicate (§14.2), the same 404
/ 422 envelopes, and the same rule that a backfilled row cannot go straight
to `verified` (§12.3). Then the queue stops being read-only and WP1.4's
`mustRepropose` flag gains a path to act on.

*Gate:* `sh scripts/test-backend.sh`, the route-policy matrix extended, and
the tenant matrix — which interlocks two files, per the WP1.6 finding.

### WP3.2 — `SemanticConceptRelationship` *(item 6, DELIVERED 2026-09-17)*

**Delivered**, on D6. Migration `20260917_0060` creates
`semantic_concept_relationships`; `symgov_backend/concept_relationships.py`
carries the propose/transition pair, the directed reads and the verified
`broader` walk. Two decisions Chris took at build time are recorded here
because the code alone does not explain them:

- **`method` and `confidence` are carried** although §7.3's field list omits
  them, matching `concept_classification_assignments` so §8.4's explicit
  auto-verification policy has a column to inspect. `legacy_backfill` is the
  one sibling value deliberately left out: §12.1 phase M2 backfills
  classifications, and a relationship backfill does not exist to write it.
- **The `no_relationship_table` gap became `no_concept_target`** rather than a
  real proposal. The ordering trap below is why: the table is independent of
  CFIHOS, the vocabulary is not, so the honest gap is now "there is no concept
  to point at", and building the resolution rule would be the vocabulary half.
  `no_relationship_table` has left `MAPPING_GAP_REASONS`.

Three further notes for whoever builds on it. A row is **one directed
assertion** and nothing derives, mints or refuses an inverse — a test pins the
absence of any inverse map, because inferring one would put an unreviewed
assertion into the record (P-07). **There is no succession rule**: §7.3 makes
no two verified relationships mutually exclusive, so verifying retires nothing.
And **cycles are not constrained** — no check constraint sees more than one
row, so `broader_concept_ids` walks with a visited set and a depth ceiling, and
the PostgreSQL rehearsal stores a three-row cycle to prove the reader survives
it.

**No route and no UI**, which is the package as scoped. Nothing in the product
proposes a relationship yet; the service is reached from tests only, and that
is the same shape `propose_concept_classification` had between SM-P0-04 and
WP3.1. It should not be left there indefinitely.

**The ordering trap, unchanged:** the vocabulary wants CFIHOS imported, which is
SM-P2-02, which is downstream of the connector, which is downstream of SM-P1-01
being in use. The *table* is independent; the *vocabulary* is not.

*Gate as run:* `sh scripts/test-backend.sh` (full sweep, real PostgreSQL) plus
the seven sole-head assertions bumped to `20260917_0060`. The runner's portable
default was raised from 1800s to 2700s in the same change: 1800s sat at 97% of
a measured 29:09 sweep, so this package's own tests would have timed the runner
out rather than failed anything.

### WP3.3 — Gate scope *(item 8, blocked on D7 and on SM-P1-01 usage)*

Not started before the connector decision. Widening the scope without a
legitimate production creator of an authoritative library would mean
inventing a workflow state, which `CLAUDE.md` forbids.

### WP3.4 — `publication_gate_evaluations` reader *(item 7, = SM-P1-06)*

Downstream of WP3.3 by D7-a. Building it first yields a correct report of
nothing.

### WP3.5 — The three dormant schemes *(item 5, blocked on D5)*

No work proposed. Listed so the decision has a home.

---

## 4. What this plan asserts about P0

Item 6 was the uncomfortable one. D6 returned "P0", so for one day the claim
that all ten P0 packages were complete was wrong — not by a defect in any
delivered package, but because a P0-shaped entity had never been given one.
**WP3.2 closed it on 2026-09-17**, and the claim stands again. What remains
true, and should be stated wherever P0 completeness is claimed: the table
exists and nothing in the product writes to it yet.

---

## 5. Prohibited side effects

- No inventing production metrics or workflow states to give the gate
  something to evaluate.
- No `GRANT` on any new table (§4.4 item 10 and the SM-P0-04..-08 precedent).
- No migration without bumping the seven sole-head assertions.
- No production apply, migration or feature activation without Chris's
  explicit approval for that specific operation.

---

## 6. Relationship to the other open work

- The industry axis is
  `docs/plans/2026-09-17-sm-p1-02-industry-axis-implementation-plan.md`.
- **Activating SM-P1-01 gates three of the five items here.** Items 7, 8 and
  WP3.2's vocabulary all sit downstream of the ingestion connector, and §9 of
  the state-of-play is explicit that the connector must not be planned before
  the review surface is in real use.
