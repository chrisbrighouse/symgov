# Symbol Set Management — Luna resume

Use this file to start each fresh Luna (max) stage without replaying the whole programme history.

## Controlling artifacts

- Product draft: `docs/Symbol Set Management Spec v0.3.md`
- Product-draft SHA-256: `42c240782a4732438a24a53d7ae80eefa6a78282601a1c4a91d19d86254a1344`
- Master execution plan: `docs/plans/2026-08-08-symbol-set-management-implementation-plan.md`
- Master-plan SHA-256 (frozen planning review candidate): `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`
- Accepted decision addendum: `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md`
- Accepted addendum SHA-256: `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`
- Planning baseline: `main` / `origin/main` at `a18d5b3587ebb11c95f45ca16643efe94b322c61`
- Alembic head at planning time: `20260802_0026`

## Programme status

- Plan prepared with Sol on 2026-08-08.
- Implementation status: Stage 0 — accepted contract reviewed; F0.5 implementation gate pending; no organization implementation started.
- Current stage: Stage 0 — route and verify the F0.5 prerequisite through the serialized implementation lane.
- No production migration, deployment, push, publication, withdrawal, gateway, or service action is authorized by the plan.
- Pre-existing untracked files at planning baseline: `CLAUDE.md` and `docs/Symbol Set Management Spec v0.3.md`.
- Plan files added by planning work:
  - `docs/plans/2026-08-08-symbol-set-management-implementation-plan.md`
  - `docs/plans/2026-08-08-symbol-set-management-luna-resume.md`
  - `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` (accepted 2026-08-08T19:08:02Z; SHA-256 `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`)

## Entry blockers

1. [complete] Chris accepted implementation decision contracts I-01–I-25 and specification open decisions O1–O6 without amendment at `2026-08-08T19:08:02Z`; the exact-hash specification review passed. The security/authority review is recorded as an implementation-gate failure because F0.5 is not implemented.
2. Complete F0.5 account-security invariants before organization-bound privileged sessions.
3. [complete] Correct the stale `tests/test_llm_usage_migration.py` expected-head assertion and prove the portable backend baseline is green before creating another migration.
4. Reconcile the canonical-ID plan's unfinished work and migration ownership after `0026` before creating the first organization migration.
5. Complete F0.6 Alfi/Telegram routing policy before Stage 10 organization agents.

## Visibility rollback floor

- The fully visibility-aware Stage 5 backend and its migrated public/background readers—including Hannah and Whitney—are the minimum rollback floor before any private row is created/imported/backfilled or demotion is enabled.
- Once private semantics/data exists, normal recovery is feature-disable plus roll-forward or deployment of a release at/above that floor; never claim pre-floor reader rollback.
- Emergency use of an older backend must first externally deny every public Catalog/published/page/package/download/asset/alias/Favorite route, then stop/drain all web/API/background readers. Keep routes denied and readers stopped until an at/above-floor release is restored and the full visibility/reader suite passes.

## Verified planning evidence

- Focused auth, canonical-ID, and canonical migration tests passed during source reconciliation.
- Frontend Node suite: 74 passed.
- Full portable backend suite after the separate stale-test correction: 1490 passed, 3 skipped, 3 deselected.
- Workspace-clean compile gate passed with temporary `PYTHONPYCACHEPREFIX`.
- `git diff --check`: passed for the tracked correction.
- Exact isolated Alembic check from `backend/`: `PYTHONPATH=. uv run --isolated --with-requirements requirements.txt --with-requirements requirements-test.txt alembic heads` → one head, `20260802_0026`.
- No migration, database mutation, deployment, push, publication, withdrawal, service restart, gateway change, or external message was performed.

## Live Stage 0 continuation checkpoint — 2026-08-08T19:08:02Z

- The separate baseline correction is `tests/test_llm_usage_migration.py`, changing only the expected head from `20260730_0025` to the actual `20260802_0026`.
- Decision register accepted without amendment by Chris Brighouse, CEO of Symgov, at `2026-08-08T19:08:02Z`: `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` (SHA-256 `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`).
- F0.5 remains `SPECIFIED, NOT IMPLEMENTED`; current auth/session/dependency inspection found no forced-PIN-limited-session guard, unified CSRF policy, or throttle/audit migration.
- Canonical-ID migration `0026`, ORM model, identifier service, and focused tests exist; the canonical plan's audited backfill, publication-completeness revision, shared resolver/caller migration, and UI/release work remain unfinished and retain the next migration slot conceptually.
- F0.6 remains a Stage 10 blocker: architecture/README policy says Alfi/main is the Telegram orchestrator while the manifest still contains a direct Telegram-to-Libby binding. No gateway/config change was made.
- Independent review of the accepted addendum is complete: specification-compliance review `PASS`; security/authority review `FAIL — implementation gate` because F0.5 remains specified but not implemented. No organization implementation has started.

## Accepted-contract review evidence — 2026-08-08

- Exact hashes verified by both bounded reviewers:
  - spec `42c240782a4732438a24a53d7ae80eefa6a78282601a1c4a91d19d86254a1344`;
  - master plan `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`;
  - accepted addendum `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`.
- Specification-compliance review: `PASS`; I-01–I-25 and O1–O6 present once each, acceptance status/hash references correct, no contradiction found against the requested spec acceptance/open-decision sections, and the Stage 0 gate is consistent with the master plan. Review batch: `deleg_2b7c8e9c`, task 0.
- Security/authority review: `FAIL — implementation gate`, not a product-contract rejection. The addendum is explicit, but live source lacks forced-PIN ordering, immutable organization session context, challenge/step-up state, F0.5 throttling/audit, and the later organization authority controls. Review batch: `deleg_2b7c8e9c`, task 1.
- Blocking evidence: `backend/symgov_backend/routes/auth.py:60-64` creates a normal session after credential verification; `backend/symgov_backend/dependencies.py:147-175` has no `must_change_pin` guard; `backend/symgov_backend/auth.py:205-220` has no organization/session-mode context or challenge/step-up state.
- Previous broad review batch `deleg_63eabe5f` timed out without a verdict; it made no edits. It is historical evidence only and is not treated as approval.
- Review consequence: implement and verify F0.5 before organization-bound sessions or Stage 2; retain the protected-owner safeguard; do not treat generic role checks as platform-admin or human-governance authority.

## Stage index

0. Contract/prerequisite gate
1. Organization schema/invariants
2. Bound session and authorization
3. Organization/Platform Admin and icons
4. Projects and Symbol Set persistence
5. Private symbols, organization review, and shared visibility foundation
6. Effective palette and Set Builder
7. Public contribution and demotion
8. Catalog/favourites/UI integration
9. Usage telemetry and reputation
10. Agent oversight
11. Hardening, migration rehearsal, controlled release package

## Required skills

- Before each stage, call `skill_view(name='<required-skill>')` for every skill listed for that stage, verify each call succeeds and reports available/readiness success, and stop before card creation or implementation if any is unavailable. Keep the directly verified names `kanban-orchestrator`, `kanban-dependency-orchestration-safety`, and `kanban-worker` unchanged.
- Stage 0: load `symgov-product-planning`, `symgov-programme-planning`, `kanban-orchestrator`, and `kanban-dependency-orchestration-safety` before resolving contracts or creating the durable card graph.
- Stages 1–10: load `symgov-feature-implementation`, `test-driven-development`, `kanban-worker`, and `kanban-codex-lane`. Hermes remains the Kanban owner and independently verifies any Cody/Codex lane output.
- Every frozen implementation checkpoint: load `requesting-code-review` and obtain fresh specification-compliance and security/code-quality reviews of the exact post-correction snapshot.
- Stage 11: also load `symgov-release-operations`; the plan still does not authorize push, migration, deployment, service restart, publication, or withdrawal.

## Copy-ready prompt for the next Luna session

Work on the next approved prerequisite goal: F0.5 account-security invariants for the accepted Symbol Set Management contract in `/docker/openclaw-hz0t/data/symgov`.

Load and availability-check these skills before any card creation or implementation: `symgov-product-planning`, `symgov-programme-planning`, `kanban-orchestrator`, `kanban-dependency-orchestration-safety`, `symgov-feature-implementation`, `test-driven-development`, `kanban-worker`, `kanban-codex-lane`, and `requesting-code-review`. Stop if a required skill is unavailable.

Read first:
1. `/docker/openclaw-hz0t/data/symgov/CLAUDE.md`;
2. `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-08-08-symbol-set-management-luna-resume.md`;
3. `/docker/openclaw-hz0t/data/symgov/docs/Symbol Set Management Spec v0.3.md`;
4. `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-08-08-symbol-set-management-implementation-plan.md`, especially Sections 1–6 and the F0.5 prerequisite references;
5. `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-07-30-f0-5-account-security-invariants-spec.md`;
6. `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-08-02-catalog-canonical-urls-and-short-links-implementation-plan.md`;
7. live auth source: `backend/symgov_backend/auth.py`, `backend/symgov_backend/routes/auth.py`, and `backend/symgov_backend/dependencies.py`.

Before acting, verify the controlling hashes exactly:
- spec: `42c240782a4732438a24a53d7ae80eefa6a78282601a1c4a91d19d86254a1344`;
- master plan: `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`;
- accepted addendum: `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`.
Also verify `git status --short --branch`, `git rev-parse HEAD`, `git rev-parse origin/main`, and from `backend/` run `PYTHONPATH=. uv run --isolated --with-requirements requirements.txt --with-requirements requirements-test.txt alembic heads`.

Current authoritative decision: Chris Brighouse accepted all I-01–I-25 and O1–O6 defaults without amendment at `2026-08-08T19:08:02Z`. The specification-compliance review passed at the accepted hash. The security/authority review failed the implementation gate because F0.5 is specified but not implemented. Do not start organization-bound sessions or Stage 1 organization implementation until F0.5 is implemented, reviewed, and verified.

Execution lane:
- Route substantial work through the durable serialized Kanban/Cody lane assigned to the existing `cody` profile.
- Inspect the board first and continue an existing F0.5 card if one exists; do not create duplicate writers.
- Use the dependency sequence implementation → fresh Stage 1 specification review → fresh Stage 2 security/code-quality review → final verification.
- If Kanban mutation is unavailable in the current session context, stop and report that exact capability blocker. Do not substitute `delegate_task` for Kanban and do not edit the shared repository directly.
- Preserve unrelated dirty/untracked work. Do not clean, reset, stash, commit, push, migrate a non-disposable database, deploy, restart services, change gateways, publish, withdraw, or send external messages.

F0.5 acceptance scope:
- forced-PIN-limited session is issued after credential verification when `must_change_pin` is set;
- PIN change completes before organization selection or normal application-session issuance;
- central backend guard covers protected routes and mutation methods;
- CSRF policy is explicit and tested for browser mutations;
- login abuse throttling and attributable security/audit events are durable;
- session revocation behavior is explicit and tested;
- current-PIN reuse is rejected;
- focused boundary and regression tests cover the above without weakening personal-mode compatibility;
- the protected-owner safeguard remains in place until the separately reviewed Symgov bootstrap/cutover evidence exists.

Use strict RED→GREEN→REFACTOR for each behavior. Run the focused F0.5 tests, the portable backend suite, the frontend suite where affected, the workspace-clean compile gate, `git diff --check`, and the isolated Alembic-head check. Record exact results, changed paths, residual risks, and side effects not performed. Obtain fresh exact-snapshot Stage 1 and Stage 2 reviews after any correction; do not reuse a review from an earlier hash.

Freeze and later run the compile gate exactly as this workspace-clean shell, not plain `python -m compileall`:

```bash
(
  set -eu
  pycache_dir="$(mktemp -d)"
  trap 'rm -rf "$pycache_dir"' EXIT
  PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m compileall -q backend/symgov_backend scripts
)
```

Before stopping, obtain the required independent review for any frozen contract, update this resume with exact branch/HEAD/status/hashes/evidence and the next stage, and leave a copy-ready prompt for one fresh Luna context. Preserve unrelated dirty/untracked work.

## Resume update template

Replace the status sections after each completed stage with concrete values:

- completed stage and accepted source/addendum;
- branch, HEAD, origin/main, exact dirty/untracked paths;
- Alembic head(s) and migrations added/applied/rehearsed;
- exact focused and broad commands/results;
- immutable Stage 1 and Stage 2 review evidence;
- side effects explicitly not performed;
- residual risks/blockers;
- next stage number and exact files/sections to read;
- a copy-ready fresh-context prompt with no placeholders.

Do not rewrite historical plan claims to make later events appear to have happened earlier. If the programme design changes materially, create a dated addendum and link it here.
