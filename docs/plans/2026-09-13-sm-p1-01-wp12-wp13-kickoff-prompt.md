# SM-P1-01 WP1.2 + WP1.3 kickoff prompt

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This is the restart pack for the **route layer** of SM-P1-01: WP1.2 (semantic
review API) and WP1.3 (rights record review API). The controlling plan —
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` —
already exists, its §4 decisions are all resolved, and WP1.0 and WP1.1 are
delivered. The new session's job is to execute two work packages, not to plan.

---

## 1. Frozen baseline (captured 2026-09-13)

**Read this section before touching anything. It differs from every previous
SM-P1-01 kickoff in one important way: the working tree is dirty on purpose.**

- Branch `main`, `HEAD` = `b157e04` ("refactor: split the backfill's rewrite count into label and column changes"), identical to `origin/main` (`git rev-list --left-right --count origin/main...main` → `0 0`).
- Sole Alembic head: `20260911_0057`. Neither WP1.0 nor WP1.1 added a migration, and neither should WP1.2 or WP1.3.
- Production runs release `stage11-56677a9`.
- **Regression baseline: 3764 passed, 3 skipped, 3 deselected** (portable partition, Postgres tests included, ~1100s). No work package may lower this.

### 1.1 WP1.0 and WP1.1 are complete but UNCOMMITTED

They live in the working tree as uncommitted changes. **Do not `git stash`,
`git reset`, `git checkout --`, or "clean up" anything.** These files are the
foundation WP1.2 and WP1.3 build on:

| Path | State | SHA-256 |
|---|---|---|
| `backend/symgov_backend/semantic_review.py` | new (WP1.1) | `1cdadd0ac641ff2bd603b2bb5163a37cd8775001a7956593f42aeac48419740a` |
| `backend/symgov_backend/organization_promotion_handoff.py` | modified (WP1.0) | `16ed57981f4ef209774cd8980860354f485965ac557f00510a94a365eddd62a7` |
| `backend/symgov_backend/publication_handoff.py` | modified (WP1.0) | `ac141e8a9b1bb5d9720c02363800398f2de08fad4e262f8ceba99fe5ca26dcf5` |
| `tests/test_semantic_review_queries.py` | new (WP1.1) | `bcde1bfbade3cdd7883fa72377876375b69195068f152b989658c5374a1aa14d` |
| `tests/test_semantic_review_queries_postgresql.py` | new (WP1.1) | `719913dfb1a48cac93402e3d07703c4ca6d4bd3a9c31c35d705c9237be8b4dd2` |
| `tests/test_wp73_promotion_publication_handoff_postgresql.py` | modified (WP1.0) | `1d1f9d5f7e3b5552ea90faf14b57f7e765f7a3cea1ea130cada933d5ec2367c2` |
| `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` | untracked, amended 2026-09-13 for Q7/Q8 | `b161d8209b247a5c9aa0e8b007e659918fedda9390d0fcc4eba3b8ca55d72f7a` |

Also present and **not yours to touch**: the pre-existing
`.claude/settings.local.json` modification and the untracked `UI-Design/`
directory. Both predate this programme.

If any hash has drifted, stop and reconcile against the live repository before
editing. Re-run `git status`, `git log --oneline -5` and the Alembic head check
first regardless.

**Harness note, not a product defect:** `scripts/test-backend.sh` bounds the
portable partition at `timeout 300s`, which now expires at ~53% on this host.
Use the long-timeout invocation instead:

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

It takes ~18 minutes. Run it in the background and stamp the identity
(`git rev-parse HEAD` plus `sha256sum` of every changed path) into the same
output, so the result cannot later be attached to different bytes.

---

## 2. What WP1.2 and WP1.3 are

From plan §2, with §4's decisions already applied:

**WP1.2 — Semantic review API.** New `routes/semantic_review.py`, mounted
**once at `settings.api_prefix`** (v1-only, §6 Q7) behind
`SYMGOV_SEMANTIC_REVIEW_ENABLED` (default off), with the same
`csrf, session_access` dependencies every other authenticated router uses and
**no step-up dependency** (§6 Q8).
Read endpoints for WP1.1's five queues and for one symbol revision's full
semantic state. Write endpoints for concept create / revision add / revision
transition, and for assignment, classification and external-mapping decisions.
Pydantic schemas in `schemas.py` following the existing house shape.

**WP1.3 — Rights record review API.** Propose and decide endpoints for
`rights_records`, *including the reviewer's own proposal*. Today only
`publication_gate.propose_intake_rights_record` can create a rights record and
it always writes `ai_assisted`, which §8.4 makes permanently unapprovable — so
without a reviewer-proposal route the gate's rights dimension stays
unsatisfiable and SM-P2 authoritative ingestion stays blocked.

**Authorization (§4 Q2, resolved):** concept lifecycle — create, add revision,
transition — is `require_platform_admin` only, matching §17's "concept
governance is platform-level initially". Symbol semantic assignment,
classification assignment, external-mapping and rights decisions are open to
`admin` or `reviewer`, matching the existing Reviews surface.

**The §16.1 criteria these close:** "The review workflow can see proposed
semantic/classification/source assertions with evidence and status", and "No
organisation-private symbol existence is revealed by public semantic
endpoints."

---

## 3. Required reading before writing anything

1. `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — the controlling plan. §2 (sequence, with WP1.0/WP1.1 delivery notes), §4 (all six decisions, resolved), §6 (prohibited side effects).
2. `backend/symgov_backend/semantic_review.py` — WP1.1's module. **Read its docstring in full.** It states which queues carry §14.2's tenant predicate and which deliberately do not, and why. WP1.2 must not re-decide that.
3. `docs/SymGov_Semantic_Model_Classification_Change_Specification_v0.1.docx` — §15.2 (the package row), §16.1–16.2 (acceptance), §14.2 (private/public boundary), §14.4 (audit and retention), §8.4 (verification authority), §7.12 (rights decision triple), §17 (decision register).
4. `backend/symgov_backend/publication_gate.py:1-120` — its module header measures which §9.2 dimensions are satisfiable and why. Do not re-derive. Its closing line matters for WP1.2: "No read route is exposed here, so section 14.2's private-symbol boundary is untouched" — **WP1.2 is the first read route over these tables, so that boundary becomes live surface for the first time.**
5. `CLAUDE.md` (repo root) — governs every session.

---

## 4. Load this Hermes skill

`symgov-feature-implementation`
(`/root/.hermes/profiles/symgov/skills/symgov/symgov-feature-implementation/SKILL.md`)
— strict vertical-slice TDD, interruption-safe checkpoints,
evidence-separated verification. Do **not** load `symgov-programme-planning`
(the plan exists) or `symgov-release-operations` (nothing here deploys).

---

## 5. Measured route-layer facts — do not re-derive these

All confirmed against the live repository on 2026-09-13.

**Router registration** (`app.py:99-168`). `csrf = Depends(require_cookie_mutation_security)` and `session_access = Depends(require_session_access)` are built once and passed to every `include_router`. A flag-gated router adds its guard *first* in the dependency list:

```python
app.include_router(
    symbol_demotion_router,
    prefix=settings.api_prefix,
    dependencies=[Depends(symbol_demotion_route_guard), csrf, session_access],
)
```

**The flag-guard pattern** (`routes/symbol_demotion.py:31-33`) — a 404, not a 403, so a disabled feature does not advertise its own existence:

```python
def symbol_demotion_route_guard(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and ...):
        raise HTTPException(status_code=404, detail="Not found.")
```

**The settings flag** (`settings.py:155-205`) — every I-20 flag is the same literal form, evaluated at import, so activation needs a process restart:

```python
semantic_review_enabled: bool = os.environ.get("SYMGOV_SEMANTIC_REVIEW_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
```

Expose it on `/auth/me` as `semanticReviewEnabled` alongside the existing seven (`routes/auth.py:102-112`).

**Authorization dependencies** (`dependencies.py`): `require_platform_admin:569` (403 "Platform Admin privileges are required."), `require_any_role(roles):519` (403 "Insufficient role for this operation."), `require_user:499` (401 "Authentication required."). Use `require_any_role({"admin", "reviewer"})` for the decision routes — **not** `require_workspace_access`, which is route-template-driven off `WORKSPACE_OPERATIONS` and would drag this router into the workspace policy inventory.

**Schema house shape** (`schemas.py`, 1599 lines): literal camelCase field names, no alias generator; `model_config = ConfigDict(extra="forbid")` on every request model; `APIErrorResponse` / `APIValidationErrorResponse` at `:20-27`.

**Declare 422 explicitly.** `routes/projects.py:26` is the reference:
`responses={401: {"model": APIErrorResponse}, 403: {...}, 404: {...}, 422: {"model": APIValidationErrorResponse}}`.
FastAPI's auto-generated `HTTPValidationError` is a contract defect here — the app returns its own envelope (`app.py:93-98`: `{"error": "validation_error", "detail": ..., "issues": [...]}`). Verify operation-by-operation through `create_app().openapi()`, not by reading the decorators.

**You do NOT need to touch `WORKSPACE_OPERATIONS` or `EXPECTED_V1_ONLY`.** `tests/test_route_auth_enforcement.py:141` is workspace-scoped and driven by `expand_workspace_operations()`; a new non-workspace router does not enter that inventory.

**Every router added since Stage 4 is v1-only** — `projects`, `symbol_sets`, `symbol_context`, `organization_symbols`, `symbol_demotion` are mounted at `settings.api_prefix` and have no `/api` legacy twin. Only six legacy routers exist (`auth`, `admin`, `public`, `published`, `workspace`, `llm`). This is the measurement behind §6 Q7, which resolved v1-only: follow it, and add no `/api` mount.

**`require_recent_step_up` is not used by either package** (§6 Q8). It appears in `routes/symbol_demotion.py` and stays there.

**Test fixture facts learned the hard way in WP1.1**, which will bite again:

- The disposable Postgres engine connects as `postgres`, not `symgov_app`, so new tables need no `GRANT` (the `GRANT`s in `test_wp73_...py`'s fixture are belt-and-braces).
- Pin every `*_postgresql.py` fixture at `20260911_0057`. The ORM is one global object that always reflects head.
- An **active** `Organization` requires an active Organization Administrator (`enforce_active_organization_admin_minimum`). Build fixture organisations as `is_active=False, entitlement_status="suspended"` unless membership is the thing under test.
- A `published` `SymbolRevision` requires its symbol to already hold a canonical catalog identifier (`validate_catalog_symbol_publication_invariant`). Allocate via `ensure_catalog_symbol_id` *before* creating the revision. Identifiers are `S-000001`, not `SYM-...`.
- Organisation-private symbols should be `lifecycle_state="draft"`; the publication invariant refuses them otherwise.

---

## 6. Every decision is resolved — nothing blocks the start

Plan §4's six decisions were resolved on 2026-09-12. The two route-layer
decisions this pack raised were resolved by Chris on 2026-09-13:

**Q7. Does the new router get a legacy `/api` mount? → No. v1-only.**
Plan §2's WP1.2 line originally listed "legacy `/api` prefix parity" in its
test set; that sentence predated the route inventory and **the plan has been
amended to match this decision** — do not reinstate it from a stale reading.
Every router added since Stage 4 (`projects`, `symbol_sets`, `symbol_context`,
`organization_symbols`, `symbol_demotion`) is mounted at
`settings.api_prefix` with no `/api` twin; the six legacy routers are all
pre-Stage-4 surfaces kept for existing clients. WP1.4's frontend will be this
API's first consumer, so a legacy mount would be a compatibility surface for
zero callers and would double the route-policy matrix. Mount once, at
`settings.api_prefix`.

**Q8. Does any of this require step-up re-authentication? → No, not in v1.**
Do not add `require_recent_step_up` (`dependencies.py:586`) to any route in
either package, including concept publication and rights approval. Every act
in WP1.2 and WP1.3 is a reversible governed transition with a succession
model and a full audit trail — a wrong `verified` is retired by the next
decision rather than destroying anything. `symbol_demotion` is the one
surface that uses step-up, and it is genuinely destructive; these are not.
If a later package introduces an irreversible act, that package raises the
question again.

**Do not re-litigate any of the eight.** If implementation evidence genuinely
contradicts one, raise it as a question rather than deciding unilaterally, and
record the outcome in the plan's §4.

## 7. Deliberately out of scope — record, do not fix, do not silently re-open

- **Industry/Application and process-category vocabularies** — Chris is researching both separately. No scheme is seeded by any work package. ICS is the intended Industry/Application source pending licensing confirmation.
- **`USE-CASE`, `DOCUMENT-TYPE`, `REPRESENTATION-TYPE`** — read-only in v1 per §4 Q6. Display existing assignments; expose no control that creates one.
- **`classification_records.industry`** — logged as a defect 2026-09-11; four hard-coded values, three of them discipline names.
- **Catalogue facet over-matching** — `catalog_search.py:125` ORs an unconditional `payload_json` ILIKE into every facet, so `Equipment` and `Process` match all 84 published symbols. Pre-existing and unassigned.
- **§7.3 `SemanticConceptRelationship`** — no table, in no §15.1 row. A missing P0 table; its own package, before SM-P1-04.
- **`publication_gate_evaluations` is write-only** — SM-P1-06's data source, already accumulating. WP1.2 may surface one revision's latest evaluation read-only; it must not add a dashboard.
- **M3 provisional concept candidates** — deliberately not in this package. Manual concept creation lands first so there is something to review before a generator fills the queue.
- **`ConceptTerm` and organisation-scoped concepts** — deferred by §17 decision, not rejected.
- **The frontend** — WP1.4. These two packages stop at the API.

---

## 8. Prohibited side effects (plan §6, unchanged)

- No `npm run build:publish`, `npm run publish:static`, deployment, service restart, live migration, or **feature activation** without Chris's explicit approval for that specific operation. `SYMGOV_SEMANTIC_REVIEW_ENABLED` ships default-off and stays off.
- No commit and no push without explicit approval.
- No change to SM-P0-09's `NON_DISPLAY_MATCH_BASES` derivation rules.
- No change to `automation_policy.evaluate_publication_automation_gate` or `provenance_assessments` — the two publication gates coexist and stay strangers.
- No change to WP1.1's tenant-scope decisions without raising it as a question first.
- Leave `.claude/settings.local.json` and `UI-Design/` untouched.

---

## 9. Test-artifact inventory (plan §3)

| Path | New/extend | Covers |
|---|---|---|
| `tests/test_semantic_review_routes.py` | new | WP1.2 route-policy matrix (v1 surface only) |
| `tests/test_semantic_review_routes_postgresql.py` | new | WP1.2 end-to-end governance acts |
| `tests/test_rights_review_routes.py` | new | WP1.3 |
| `tests/test_rights_review_routes_postgresql.py` | new | WP1.3 |
| `tests/test_publication_gate_postgresql.py` | extend | WP1.3: an approved permissive record satisfying the gate's rights dimension end to end |

The WP1.3 gate test is the one that proves the package's whole point. Without
it, "rights approval works" is a claim about a row, not about the gate.

---

## Prompt

> Continue SM-P1-01 in `/docker/openclaw-hz0t/data/symgov`, executing WP1.2 (semantic review API) and WP1.3 (rights record review API).
>
> Read `docs/plans/2026-09-13-sm-p1-01-wp12-wp13-kickoff-prompt.md` first — it is the restart pack, and its §1 matters most: **WP1.0 and WP1.1 are complete but uncommitted in the working tree**, with per-file hashes to verify. Do not stash, reset, clean or "tidy" anything; those files are what WP1.2 builds on, and `.claude/settings.local.json` and `UI-Design/` are pre-existing and not yours. Then read the controlling plan `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` in full (§4's decisions are all resolved — do not re-litigate them), then `backend/symgov_backend/semantic_review.py`'s docstring, then `CLAUDE.md`, then the specification sections the pack names, then `backend/symgov_backend/publication_gate.py:1-120`.
>
> Load the Hermes skill `symgov-feature-implementation`. Do not load `symgov-programme-planning` or `symgov-release-operations`.
>
> Re-verify the git and Alembic baseline and the §1.1 file hashes before assuming the pack is still accurate. The regression baseline to hold is **3764 passed / 3 skipped / 3 deselected** — use the pack's long-timeout pytest invocation, because `scripts/test-backend.sh`'s own 300s bound expires at ~53% on this host, which is a harness limit and not a product regression.
>
> **Every decision is already made — there are none outstanding and none to ask me about.** Plan §4's six were resolved on 2026-09-12; the two route-layer ones were resolved on 2026-09-13 and are recorded in the pack's §6: the router is **v1-only**, mounted once at `settings.api_prefix` with no legacy `/api` twin (Q7), and **no route in either package takes `require_recent_step_up`** (Q8). The controlling plan has been amended to match Q7, so do not reinstate legacy-parity testing from a stale reading of it. If implementation evidence genuinely contradicts a decision, raise it rather than deciding unilaterally. The pack's §5 carries the measured route-layer facts — registration, the flag guard, the authorization dependencies, the schema shape, the explicit-422 rule, and five Postgres fixture traps — so do not re-derive those either.
>
> Your first action is therefore to re-verify the baseline in §1 and then ask me for the go-ahead to start WP1.2.
>
> Execute WP1.2 and WP1.3 as vertical slices with strict TDD, each with its own tests and its own go-ahead from me before it starts. Hold §8's prohibited side effects as invariants: the feature flag ships default-off and is never activated, no migration, no commit, no push, no deployment, no service restart, and no change to WP1.1's tenant-scope decisions without asking. Stop at repo-side completion of WP1.3; WP1.4 is the frontend and is a separate package.
