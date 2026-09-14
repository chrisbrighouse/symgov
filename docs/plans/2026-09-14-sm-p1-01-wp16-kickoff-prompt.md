# SM-P1-01 WP1.6 kickoff prompt

Copy the "Prompt" section at the end of this file verbatim into a new Claude
Code session started in `/docker/openclaw-hz0t/data/symgov`.

This is the restart pack for **WP1.6, the closing package: route-policy
matrix, regression and acceptance**. It is the last work package in
SM-P1-01. The controlling plan —
`docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` — has
WP1.0 through WP1.5 recorded as delivered, and §4's decisions Q1–Q10 are all
resolved. The new session's job is to verify and accept, not to plan and not
to add product surface.

**Two things differ from every earlier pack in this programme, and both
matter:**

1. **`main` is ahead of `origin/main` and nothing is pushed.** Every earlier
   pack opened level with the remote. WP1.5 and this pack are committed
   locally only. §1 has the exact SHAs. **Do not push** — that is a separate
   approval Chris has not given.
2. **WP1.6 adds no product code.** Its deliverables are a complete
   tenant-isolation matrix, the named regression gates, and a written
   acceptance pass. If the session finds itself designing a route or a panel,
   it has misread the package.

---

## 0. Decisions already taken — nothing here is open

**D1, answered by Chris on 2026-09-14: WP1.5 was committed before WP1.6.**
The tree is therefore clean and the acceptance evidence attaches to a SHA
rather than to a hash manifest. `75fd5c2` carries WP1.5; a following docs
commit carries this pack and WP1.5's. Neither is pushed.

**There are no open decisions in this package.** §4's Q1–Q10 in the
controlling plan are all resolved, and WP1.6 introduces no new product choice
because it introduces no product surface. If the session believes it has
found a decision to make, it has almost certainly found a *finding to report*
instead — see §6.

---

## 1. Frozen baseline (captured 2026-09-14, after WP1.5's closing gates)

- Branch `main`. `HEAD` is the docs commit carrying this pack; its parent
  `75fd5c2` ("feat: forecast the approval in the review surfaces (SM-P1-01
  WP1.5)") is the package under acceptance, and *its* parent is `2c1aab1`
  (the WP1.2/WP1.4 amendment). `git log --oneline -3` shows all three.
- **`main` is AHEAD of `origin/main`, which is still at `2c1aab1`.**
  WP1.0–WP1.4 and the amendment are pushed; WP1.5 and this pack are committed
  locally and **not pushed**. Pushing is a separate approval — see §9.
- Working tree is clean apart from the two pre-existing items in §1.2.
- Sole Alembic head: `20260911_0057`. `git status --porcelain backend/alembic`
  is empty and must stay empty: **no package in SM-P1-01 has added a
  migration and WP1.6 must not either.**
- Production runs release `stage11-56677a9`. **The whole semantic review API
  and both surfaces are undeployed, and `SYMGOV_SEMANTIC_REVIEW_ENABLED` has
  never been activated.** This is load-bearing for §4's acceptance wording.

### 1.1 Test baselines to hold

| Gate | Baseline | Notes |
|---|---|---|
| Backend portable partition | **3988 passed, 3 skipped, 3 deselected** (1448s) | Postgres included. Was 3958 before WP1.5's 30 tests. |
| `npm run test:frontend` | **362 passed, 0 failed, 56 suites** (~13s) | Was 335 before WP1.5's 27 tests. |
| `npm run build` | succeeds, **87 modules**, `dist/assets/index-*.js` **651.22 kB** | The >500 kB chunk-size warning is pre-existing and is not yours. |

No work package may lower any of these. WP1.6 will add tests, so expect the
first two to rise; say by exactly how many and why.

### 1.2 Working-tree items — neither is yours

`git status --porcelain` should show exactly these two lines, both of which
predate this programme:

```
 M .claude/settings.local.json
?? UI-Design/
```

**Leave both untouched.** If anything else appears, stop and reconcile before
editing — it means another session touched the tree after this pack was
written.

### 1.3 WP1.5 identity manifest (SHA-256, first 16 hex)

The commit is the primary identity; this table is the cross-check. Verify it
before trusting the baselines above. A mismatch means the bytes moved after
WP1.5's gates ran, and the 3988/362 numbers no longer attach to what is on
disk.

These seventeen files are byte-identical to the ones the 3988-green run
stamped, verified before the commit. Only prose changed afterwards (the
controlling plan's WP1.5 record, and these packs), and no test reads any of
it.

```
7276f19ab6189003  backend/symgov_backend/classification_mapping.py
c861a6b7984e02a0  backend/symgov_backend/publication_handoff.py
ec81e409b9e1d741  backend/symgov_backend/routes/semantic_review.py
fb8741480b831c05  backend/symgov_backend/schemas.py
c1a98b4f84a9006e  frontend/src/App.jsx
275ca5635fe19dc4  frontend/src/OrganizationSymbolReviewQueuePanel.js
dc5ceea69388c1fe  frontend/src/OrganizationSymbolReviewsPage.js
f8aac1aada1e33b0  frontend/src/api.js
62e1211da73f644d  frontend/src/styles.css
13daf216fc6bc8fd  frontend/src/ReviewClassificationForecast.js
0b2a09c176590f8b  frontend/src/reviewSemanticPreview.test.js
bbcc38cc00696c0f  frontend/src/organizationSymbolDrafts.test.js
f11b824c0350fd12  frontend/src/semanticReviewApi.test.js
f49264a95c34d92d  tests/test_classification_mapping.py
d6c9c468a983ed8e  tests/test_classification_mapping_postgresql.py
8d300f7daf936e59  tests/test_semantic_review_routes.py
6e1b23dd9c371e43  tests/test_semantic_review_routes_postgresql.py
```

The controlling plan is deliberately excluded: WP1.6 will edit it to record
its own closure, so pinning its hash here would guarantee a false mismatch.
`git show --stat 75fd5c2` is the authoritative list of what WP1.5 changed.

### 1.4 Test invocations

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

It takes ~24 minutes. Run it in the background and stamp the identity
(`git rev-parse HEAD` plus `sha256sum` of every changed path) into the *same*
output, before and after, so the result cannot later be attached to different
bytes. WP1.5 did exactly this; copy that shape.

**Environment note.** In the WP1.5 session every `Bash` call failed at
bubblewrap setup with `bwrap: Can't create file at
/docker/openclaw-hz0t/data/symgov/.mcp.json: Permission denied`, so every
command ran with the sandbox disabled. The `Monitor` tool has no sandbox
override and is therefore unusable; use `Bash` with `run_in_background` and an
`until` loop to wait on long gates. If the same failure appears, say so once
and carry on unsandboxed rather than re-diagnosing it.

---

## 2. What WP1.6 is

The plan's sentence, unchanged and not re-scoped:

> Full portable regression, frontend tests, `npm run build`, a
> tenant-isolation matrix proving no organisation-private symbol existence
> leaks through any new endpoint (§14.2, §16.1), and an acceptance pass
> against the §16.1 criteria this package claims to close.

Three deliverables, in this order: **§3 the matrix**, **§4 the acceptance
pass**, **§5 the gates**. The gates run last because they must run on the
final bytes.

---

## 3. Deliverable 1 — the tenant-isolation matrix

### 3.1 The surface is nineteen routes, not seventeen

`backend/symgov_backend/routes/semantic_review.py` carries **19** route
decorators. Seventeen are in `SEMANTIC_REVIEW_ROUTES` in
`tests/test_semantic_review_routes.py`; the other two are WP1.3's rights
writes, which share the router but have their own matrix in
`tests/test_rights_review_routes.py`. Earlier prose in the plan says
"sixteenth route" and "seventeenth route" — both count matrix entries, not
decorators. **Count the decorators yourself before asserting a total.**

```
GET  /queues/symbol-classifications
GET  /queues/symbol-semantic-assignments
GET  /queues/concept-classifications
GET  /queues/concept-external-mappings
GET  /queues/rights-records
GET  /classification-schemes
GET  /review-cases/{review_case_id}/classification-preview
GET  /symbol-revisions/{symbol_revision_id}
POST /concepts
POST /concepts/{concept_id}/revisions
POST /concept-revisions/{revision_id}/transition
POST /symbol-revisions/{symbol_revision_id}/semantic-assignments
POST /semantic-assignments/{assignment_id}/decision
POST /symbol-revisions/{symbol_revision_id}/classifications
POST /symbol-classifications/{assignment_id}/decision
POST /concepts/{concept_id}/external-mappings
POST /external-mappings/{reference_id}/decision
POST /rights-records
POST /rights-records/{record_id}/decision
```

### 3.2 What already exists — do not rebuild it

Tenant isolation is *sampled* today, not swept. These pass and are good:

- `tests/test_semantic_review_queries_postgresql.py` — the platform queue
  never reveals a private symbol; an organisation-scoped queue adds only its
  own; the semantic-assignment and rights queues are scoped.
- `tests/test_semantic_review_routes_postgresql.py` — a private symbol is
  invisible to a platform-scoped queue; an organisation reviewer sees its own
  and no others; another organisation's row is absent rather than forbidden;
  the whole surface is absent when the flag is off.
- `tests/test_rights_review_routes_postgresql.py` — a package-subject record
  is platform-level; another organisation's revision and another
  organisation's record are both absent rather than forbidden.

### 3.3 What the matrix must add

**A sweep, not a sample.** A parametrized matrix over *every* route that
resolves a symbol-scoped row, asserting that a row belonging to another
organisation answers **404 and never 403, and never content**. The existing
tests prove the rule on a handful of routes; the matrix proves no route was
missed, and it fails when someone adds an eighteenth symbol-scoped route
without the predicate.

**And the complement, stated with its argument.** Some routes carry no tenant
predicate *by decision*, and the matrix must name them as a closed list
rather than leaving a reader to wonder whether they were forgotten:

| Route | Why unscoped |
|---|---|
| `/queues/concept-classifications` | a concept→node assertion names no symbol (WP1.1; §17 made concept governance platform-level) |
| `/queues/concept-external-mappings` | a concept→external-release assertion names no symbol |
| `/classification-schemes` | seeded platform reference data naming no symbol |
| `/review-cases/{id}/classification-preview` | a review case names no symbol and no organisation; neither `ReviewCase` nor `ClassificationRecord` nor `IntakeRecord` carries one |
| the three concept-lifecycle writes | platform-admin only, and concepts are platform-level |
| package- and standard-subject rights records | named no symbol; withholding them would hide the records §9.2's rights dimension needs approved |

Each already carries that argument in its own docstring. The matrix's job is
to make the list **exhaustive and checkable**, so "unscoped" is always a
recorded decision and never an omission.

**Platform Admin is not a bypass.** WP1.2 decided this and the router applies
it: `_scope` reads `active_organization_id`, and a personal-mode session is
public-only whatever the role. The matrix must include a Platform Admin
probe, because "the most privileged principal still cannot see another
organisation's private symbol" is the assertion §14.2 actually needs.

Put the sweep where the seams are real: PostgreSQL files, not the portable
partition. The portable fixture drops PostgreSQL-dialect CHECKs and stubs the
five queue reads, so a tenant assertion there would pass without exercising
the predicate.

---

## 4. Deliverable 2 — the acceptance pass

Two §16.1 criteria, written into the plan as a new closing section. For each,
map the criterion to **named evidence** — a test path and a test name, not a
claim.

1. *"The review workflow can see proposed semantic/classification/source
   assertions with evidence and status."*
2. *"No organisation-private symbol existence is revealed by public semantic
   endpoints."*

**The acceptance pass must be honest about what is not closed.** These are
the facts a reader would otherwise have to discover for themselves, and every
one of them is measured, not suspected:

- **Nothing is reachable in production.** The flag is default-off and has
  never been activated, and it is read at import so activation needs a
  process restart. SM-P1-01 is **repo-complete, not in effect**. Say that
  plainly and do not let "delivered" imply "live".
- **`ConceptClassificationAssignment` has a queue read and no decision
  route.** WP1.4 renders that queue read-only with the reason stated. The
  criterion says "can see", which this satisfies, but the asymmetry belongs
  in the acceptance record.
- **External mappings are not in the revision detail**, by
  `SymbolRevisionSemanticStateResponse`'s own decision — a mapping hangs off
  a concept, and choosing which of an assigned concept's mappings are "this
  revision's" would assert a relationship the model does not hold. They are
  reviewed in their own queue.
- **The 132 backfilled rows can be seen, rejected and re-proposed, but never
  verified** (§12.3's CHECK constraint). The re-proposal remedy became
  reachable only with the 2026-09-14 amendment.
- **The classification preview is a forecast, not recorded state** (Q9), and
  the organisation-page panel is absent for a session the router would refuse
  (Q10). Neither is a claim about what the system has recorded.
- **Two defects are carried, not fixed.** `propose_external_mapping` lets a
  duplicate active mapping escape as a 500 (recorded by the amendment; not
  reachable from any UI). And `ensure_approved_child_symbol_revision` passes
  `load_child_classification_record` the child's position among the
  *approved* children, so approving only some children of a split sheet can
  match a child against another child's `symbol_region_index` (recorded by
  WP1.5). Both need scheduling; neither is WP1.6's to fix.

---

## 5. Deliverable 3 — the gates

Run each **once, on the final bytes**, after §3's matrix is green:

- the backend portable partition (§1.4's command), identity-stamped;
- `npm run test:frontend`;
- `npm run build`;
- `git diff --check`, plus `git diff --no-index --check /dev/null -- <path>`
  for every untracked file in scope (`git diff --check` does not inspect
  untracked files; exit 1 there is the clean result, exit 3 is a real
  failure);
- `git status --porcelain`, compared line by line against §1.2.

Report the exact totals and the delta from §1.1, with the arithmetic. "No
regression" is not a number.

---

## 6. Explicitly out of scope

- **Any new product surface.** No route, no panel, no schema, no service
  function. If a matrix gap can only be closed by adding a predicate to a
  route, that is a *finding* to report, not a change to make.
- **Fixing the two carried defects** in §4. Record them; schedule them
  separately.
- **`provenance_assessments` and the §13.1 publication-gate evaluation.**
  SM-P1-06's, unchanged.
- **Activating the flag, committing, pushing, deploying, restarting.** Each
  is its own operation under its own approval. D1 covers the commit question
  and nothing else.
- **The Industry/Application and process-category schemes.** Chris's separate
  research; no scheme is seeded by anything in SM-P1-01.

---

## 7. Required reading before writing anything

1. `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` —
   the controlling plan. §2's WP1.0–WP1.5 delivery records (they name every
   decision already taken), §4's Q1–Q10, §5's regression standard, §6's
   prohibited side effects.
2. `backend/symgov_backend/routes/semantic_review.py` — **the module
   docstring in full**, which states the §14.2 boundary and names the two
   reads that deliberately carry no tenant predicate; then `_scope` and
   `_visible_revision`.
3. `tests/test_semantic_review_routes_postgresql.py` and
   `tests/test_semantic_review_queries_postgresql.py` — the existing
   isolation tests listed in §3.2, so the matrix extends them instead of
   duplicating them.
4. `docs/plans/2026-09-14-sm-p1-01-wp15-kickoff-prompt.md` — the previous
   pack, for the Q9/Q10 reasoning the acceptance pass has to summarise
   accurately.
5. `CLAUDE.md` (repo root) — governs every session.

---

## 8. Load this Hermes skill

`symgov-feature-implementation`
(`/root/.hermes/profiles/symgov/skills/symgov/symgov-feature-implementation/SKILL.md`)
— in particular its **frozen dirty-worktree final-gate discipline** and
**verification evidence** sections, which are the two this package lives in.
Do **not** load `symgov-programme-planning` (the plan exists) or
`symgov-release-operations` (nothing here deploys).

---

## 9. Prohibited side effects (invariants, not preferences)

- No migration. The head stays at `20260911_0057` and
  `git status --porcelain backend/alembic` stays empty.
- The feature flag ships **default-off** and is never activated.
- No push, no deployment, no service restart, no `build:publish`, no
  `publish:static`, no live mutation.
- **No push.** `main` is ahead of `origin/main`; pushing WP1.5 or anything
  else is a separate approval Chris has not given.
- A commit of WP1.6's own work needs its own approval; D1 covered WP1.5 only.
- No change to `automation_policy.evaluate_publication_automation_gate` or
  `provenance_assessments`.
- No change to SM-P0-09's `NON_DISPLAY_MATCH_BASES` deny-list or the legacy
  `GovernedSymbol.category`/`.discipline` derivation.
- No seeding of an Industry/Application or process-category scheme.
- Leave `.claude/settings.local.json` and `UI-Design/` untouched.
- Human-readable symbol IDs and operator-readable timestamps stay prominent;
  do not substitute UUIDs in compact UI.

---

## Prompt

> Continue SM-P1-01 in `/docker/openclaw-hz0t/data/symgov`, executing WP1.6 — the closing package: route-policy matrix, regression and acceptance. It is the last work package in SM-P1-01.
>
> Read `docs/plans/2026-09-14-sm-p1-01-wp16-kickoff-prompt.md` first — it is the restart pack. WP1.5 is committed as `75fd5c2` and the working tree is clean apart from the modified `.claude/settings.local.json` and the untracked `UI-Design/`, both of which predate this programme and neither of which is yours. **`main` is ahead of `origin/main`, which is still at `2c1aab1`: WP1.5 and the packs are committed locally and not pushed.** The pack's §1.3 pins the WP1.5 file hashes as a cross-check on the commit; verify them before trusting the baselines. Then read the controlling plan `docs/plans/2026-09-12-sm-p1-01-semantic-review-implementation-plan.md` in full (§4's Q1–Q10 are all resolved — do not re-litigate them), then the module docstring of `backend/symgov_backend/routes/semantic_review.py`, then `CLAUDE.md`.
>
> Load the Hermes skill `symgov-feature-implementation`. Do not load `symgov-programme-planning` or `symgov-release-operations`.
>
> **There are no open decisions in this package** — §0 records that D1 is answered and WP1.5 is committed, and §4's Q1–Q10 in the plan are all resolved. Do not ask me to re-open any of them.
>
> The baselines to hold are **3988 passed / 3 skipped / 3 deselected** on the backend portable partition, **362 passed / 0 failed / 56 suites** on `npm run test:frontend`, and `npm run build` succeeding at 87 modules. WP1.6 will add tests, so report the delta with its arithmetic rather than saying "no regression".
>
> **WP1.6 adds no product code.** Its three deliverables are §3's tenant-isolation matrix (a sweep over all nineteen routes on the router, not a sample — including the closed list of routes that carry no tenant predicate by decision, and a Platform-Admin-is-not-a-bypass probe), §4's written acceptance pass against the two §16.1 criteria, and §5's gates run once on the final bytes. If closing a matrix gap would need a change to a route, that is a finding to report, not a change to make.
>
> §4's acceptance pass must be honest about what is *not* closed — above all that the flag has never been activated, so SM-P1-01 is repo-complete and not in effect. The pack lists the rest. Hold §9's prohibited side effects as invariants: no migration, the flag is never activated, no deployment, no service restart, no `build:publish` or `publish:static`, **no push** (`main` is already ahead of the remote), and no commit of WP1.6's own work without my say-so.
