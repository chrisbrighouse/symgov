# Session resume pack (2026-09-17, end of day)

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This pack supersedes `2026-09-17-defect-remediation-kickoff-prompt.md` as the
restart point. That pack is still the right description of *why* this work is
being done and of the decisions taken on 2026-09-16; this one records what
changed after it and what is in the tree right now.

---

## 0. State at handover — read this first

`main` is at **`e1873fd`** and level with `origin/main`. **The tree is not
clean**, and that is deliberate: two delivered, tested items are sitting
uncommitted because a commit was never explicitly approved.

Uncommitted, modified:

| File | Belongs to |
| --- | --- |
| `backend/symgov_backend/routes/semantic_review.py` | §4.1 item 1 **and** WP3.1 |
| `backend/symgov_backend/schemas.py` | WP3.1 |
| `frontend/src/SemanticReviewPage.js` | WP3.1 |
| `frontend/src/api.js` | WP3.1 |
| `frontend/src/semanticReview.test.js` | WP3.1 |
| `frontend/src/semanticReviewApi.test.js` | WP3.1 |
| `tests/test_semantic_review_routes.py` | WP3.1 |
| `tests/test_semantic_review_routes_postgresql.py` | §4.1 item 1 and WP3.1 |
| `tests/test_semantic_review_tenant_matrix.py` | WP3.1 |
| `docs/handover/2026-09-14-symgov-state-of-play.md` | §4 annotations |
| `docs/ics-taxonomy-runbook.md` | ICS crosswalk register |
| `docs/plans/2026-09-11-classification-industry-field-defect.md` | industry axis |
| `.claude/settings.local.json` | **pre-existing, not ours — leave it alone** |

Untracked, all written by these sessions and all wanted:

- `docs/plans/2026-09-17-defect-remediation-kickoff-prompt.md`
- `docs/plans/2026-09-17-sm-p1-02-industry-axis-implementation-plan.md`
- `docs/plans/2026-09-17-sm-p1-03-structural-gaps-implementation-plan.md`
- `docs/plans/2026-09-17-session-resume-pack.md` (this file)

**No migration was added by any of it.** Single alembic head is still
`20260916_0059`, so none of the seven sole-head assertions needed bumping.

### Gates actually run

Re-run on the current tree at the close of the session, not inherited:

- `tests/test_semantic_review_routes.py`, `..._routes_postgresql.py`,
  `..._tenant_matrix.py` — **222 passed**, 3:06, real PostgreSQL included.
- `npm run test:frontend` — **366 passed**, 0 failed.

The **full** backend sweep was *not* re-run at the close. The last recorded
full baseline for this work was **4134 passed / 3 skipped / 3 deselected**.
Before claiming a new baseline, read `full_backend_sweep_hangs_on_dxf_module`
in memory: `sh scripts/test-backend.sh` is the supported runner, it takes no
subcommand, and its 1800s portable default sits against a ~29:09 actual —
thin headroom that should be raised before the next package lands tests.

## 1. What was delivered and is uncommitted

**§4.1 item 1 — closed.** `propose_external_mapping` returned 500 on a
duplicate active mapping because the insert only reached the database at
`session.commit()`, outside every exception handler. Fixed by flushing inside
the `try` and matching `uq_concept_external_references_active_mapping` **by
name** (`_is_active_mapping_conflict`). The envelope is **422, not 409** —
the sibling `propose_symbol_classification` already made that call for its own
index and documented why, and 409 is not in the router's `_ERROR_RESPONSES`.
Verified RED then GREEN.

**SM-P1-03 WP3.1 — closed, §4.3 item 9.**
`POST /semantic-review/concept-classifications/{assignment_id}/decision`,
with the frontend wired to it. Three things worth carrying forward:

- The response is the concept's **whole** classification state
  (`_concept_classification_state`, modelled on `_concept_mapping_state`), not
  the single row named — verifying a `primary` retires the primary verified
  before it in the same scheme, and a caller handed back only its own row
  could not see that succession. The frontend replaces every returned row.
- **No tenant predicate, by decision**, registered in `UNSCOPED_BY_DECISION`
  rather than omitted from the matrix: a concept-to-node assertion names no
  symbol and concept governance is platform-level (§17). WP1.1 made the
  identical call for the queue this decides on.
- `SemanticReviewPage.js` previously said in as many words that "the API
  cannot perform" this act. That notice is gone; it renders `DecisionControls`
  driven by `capabilities`, so a `legacy_backfill` row is still barred from
  `verified` by §12.3 and says why.

## 2. Decisions taken 2026-09-17 — do not reopen

Chris answered **D1–D8**, each as recommended. D1–D4 are in the industry-axis
plan, D5–D8 in the structural-gaps plan; both plans carry the reasoning.

- **D1-a** industry derives from `standards_source` provenance.
- **D2-a** legacy `industry` values stay in `evidence_json`.
- **D4** field + group depth.
- **D5** the three dormant schemes stay read-only; revisit after the industry
  axis is in use.
- **D6** `SemanticConceptRelationship` is a **genuine P0 omission**. P0 is not
  complete until the table exists. This unblocks WP3.2's table-and-service
  half; its vocabulary half stays behind SM-P2-02.
- **D7-a** widen the gate scope *then* build the reader, in the sequence
  *activate SM-P1-01 → observe → decide the connector → gate scope → reader*.
  Nothing in WP3.3 or WP3.4 starts before that.
- **D8** yes to the decision route — delivered the same day as WP3.1.

**D3 could not be carried out and is not a pending task.** Chris instructed
activating `ISO-ICS-7` as `chris.brighouse@hotmail.co.uk`. It is blocked four
independent ways: ICS is not deployed to production at all (`alembic_version`
there is `20260911_0057`; the import never ran); `set_classification_scheme_status`
has no caller outside tests; `ClassificationScheme.created_by_user_id` records
the *creator*, so a status change has no actor column and the setter takes no
actor; and that account's production role is unverified. **Not** blocked by
ODC-By licensing, which closed on 2026-09-17.

## 3. What is unblocked next

Chris was asked to choose between these and the session closed before he
answered. **Ask again rather than assuming.**

1. **WP3.2 — `SemanticConceptRelationship`** (the only other unblocked
   SM-P1-03 package). Model, migration, service pair, and replacing
   `classification_mapping.py`'s `no_relationship_table` gap with a real
   proposal. A migration means the **seven sole-head assertions** must be
   bumped — see the ICS crosswalk note in the runbook. The *table* is
   independent of CFIHOS; the *vocabulary* (832 equipment classes) is not, and
   sits behind SM-P2-02 → the connector → SM-P1-01 being in use.
2. **SM-P1-02 — the industry axis.** Plan written, D1/D2/D4 answered, no code.
   The hard fact it turns on: `_candidate_node_codes` is three deterministic
   rules and §16.2 forbids similarity matching, so Libby's four values cannot
   be matched to ICS codes. The axis cannot be re-pointed at ICS.
3. **The unlocked-pre-load race.** Carried forward but never raised as a work
   item. A route doing `session.get(...)` before a service whose `_transition`
   re-reads `with_for_update=True` gets the lock but keeps the **pre-lock
   attributes**: SQLAlchemy's `get()` does not pass `populate_existing`
   alongside `with_for_update`, so every guard is evaluated against stale
   values, and with no `isolation_level` set anywhere READ COMMITTED makes the
   overwrite silent. WP3.1's own route was fixed by deleting the pre-load (the
   service's `LookupError` is the 404). **`decide_symbol_classification` and
   `decide_external_mapping` still have it** — they need the pre-load for
   tenancy, so they need an `expire`, not a deletion.
4. **Activating SM-P1-01**, which several of the above wait on. Measured
   read-only on the live box 2026-09-17: production runs release `615e617`, so
   router *and* UI are already deployed; `alembic_version` is `20260911_0057`,
   its release's exact head, and **SM-P1-01 added no migration, so activation
   needs none**. It is one line in `/docker/symgov-hermes/docker-compose.yml`
   (`SYMGOV_SEMANTIC_REVIEW_ENABLED: "1"`, beside the seven flags there) plus
   an api container recreate, because the flag is import-time. Rollback is the
   same edit reversed. **This is a live production mutation and needs Chris's
   explicit approval at the time.**

## 4. Standing constraints

- Do not commit, push, deploy, restart services, or run migrations without
  Chris's explicit approval for that operation. `npm run build:publish`,
  `npm run publish:static` and the deploy skill are all in that set.
- Leave `.claude/settings.local.json` and every other pre-existing
  modification alone.
- **The repo is public and world-readable.** Check before adding brand or
  credential material.
- `import httpx2`, never `import httpx`.
- The dev box's bwrap sandbox dies on `.mcp.json`; git and test commands need
  `dangerouslyDisableSandbox`.
- Do not invent production metrics, workflow states, or backend contracts.

---

## Prompt

> Resume Symgov work in `/docker/openclaw-hz0t/data/symgov`.
>
> Read `docs/plans/2026-09-17-session-resume-pack.md` first, then the two
> implementation plans it names, then §4 of
> `docs/handover/2026-09-14-symgov-state-of-play.md`.
>
> The working tree is intentionally dirty: the §4.1 item 1 fix and SM-P1-03
> WP3.1 are both delivered, both tested green (222 targeted backend, 366
> frontend), and both uncommitted because no commit was approved. Confirm that
> state with `git status` and the targeted tests before doing anything else.
>
> Then ask me two things: whether to commit that work, and which of the four
> items in §3 of the resume pack to take next. Do not start new code, commit,
> push, or touch production until I have answered.
