# Product Stage 4 — Luna Implementation Resume

Continue in `/docker/openclaw-hz0t/data/symgov`.

Product Stage 3 is complete. Product Stage 4 has a repository-grounded implementation plan but no Stage 4 product code has been started. Do not create another plan or reopen accepted product decisions unless live implementation evidence proves a contradiction.

## Authoritative inputs

- Product spec: `docs/Symbol Set Management Spec v0.3.md`
  - SHA-256: `f9e7a8979f08308763d4047aae17608c05e449df8725c49a8c451eccbd6de656`
- Accepted addendum: `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md`
  - SHA-256: `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`
- Stage 4 implementation plan: `docs/plans/2026-08-22-symbol-set-management-stage4-implementation-plan.md`
  - SHA-256: `56338ee4335cbdea9feb23d94d2d6cd69b4fe197a0ece823d7b402182d68eb66`
- Programme plan: `docs/2026-08-10-symbol-set-management-implementation-plan.md`
  - SHA-256 at planning checkpoint: `381dd2f962d8121a672093ad965247ac380426de494351d128b97435ead648e4`
  - Its earlier Stage 3 status is historical; its Stage 4 contract remains an input.

Read the accepted addendum and the Stage 4 plan in full. Read only the relevant specification and programme-plan ranges named by the Stage 4 plan. Recompute and compare all four hashes before implementation. Stop and inspect any mismatch; do not implement against a moving contract.

## Execution model

- Use `gpt-5.6-luna` in max mode for implementation and independent reviews.
- Use one fresh Luna context per work package/review. Do not attempt all of Stage 4 in one context.
- Keep writers serialized in the shared repository.
- Preferred durable method: Symgov Kanban/Cody lane with a card-scoped Luna/max model selection, one writer at a time, followed by the review graph in the plan.
- Do not create separate cards for RED, GREEN, checksums or duplicate final verification.

## Required skills

Load and availability-check these before any card creation or source edit:

- `symgov-feature-implementation`
- `test-driven-development`
- `symgov-programme-planning`

Stop if any required skill is unavailable.

## Frozen repository checkpoint

Planning checkpoint captured 2026-08-22:

- Branch: `main`
- `HEAD`: `9fa9fd10130de7aed50a05df1e14fda06308e09d`
- `origin/main`: `9fa9fd10130de7aed50a05df1e14fda06308e09d`
- Sole Alembic head: `20260821_0029`
- Stage 3 completion commit: `9fa9fd1` (`feat: complete product stage 3 governance workflows`)
- Tracked tree was clean before planning files.
- Preserve unrelated untracked `.claude/settings.local.json` exactly. Never reset, clean, stash, stage, edit or delete it.
- The Stage 4 plan and this resume are documentation-only untracked additions until separately authorized.
- Planning-time focused baselines were GREEN: backend `86 passed`; frontend `47 passed`. These are not broad release gates and must be refreshed by WP0 if implementation starts in a later context.

Refresh every point above before acting. A mismatch triggers bounded read-only drift attribution, not speculative implementation.

## Frozen Stage 4 decisions

1. Project means a real work/contract/programme context.
2. Every active member can select every active same-organization Project, including one with no available Sets.
3. No per-user Project assignment exists.
4. Project description is plain text, 0–50 Unicode code points at database/API/UI; JavaScript must use `Array.from(value).length`, with 50/51 astral tests.
5. Project and Set codes are immutable, organization-scoped and use accepted I-24 grammar.
6. Symbol Set availability is many-to-many with Projects.
7. Context fallback is request-time explicit eligible Set Code -> stored user preference -> Project default -> organization default -> none; `explicit` is reported only by the selection PUT response.
8. Organization default is eligible only when that Set is actively available to the selected Project.
9. Selected Project lives in a dedicated session-context row; it never mutates the session's organization.
10. Active-set preference is an optional row per `(user, project)` with a required Set UUID. Clearing deletes the row and falls through to defaults; Stage 4 has no durable null override.
11. Set items store stable governed-symbol UUID only, never revision UUID.
12. Before Stage 5, item addition accepts only a fully eligible current Public Catalog symbol using the existing complete published predicate.
13. Set-item add/remove locks the governed-symbol row using the same boundary Stage 7 demotion will reuse.
14. No private symbols, effective palette, usage telemetry, immutable releases, publication/demotion, offline package or flag activation belongs to Stage 4.
15. Ordinary Project/Set administration does not add step-up beyond accepted I-25.
16. Projects are `active -> closed` only; closed is terminal. Sets follow only `draft -> active|archived`, `active -> superseded|archived`, `superseded -> archived`; archived is terminal.
17. Set disciplines/use cases use dedicated bounded JSON arrays; Project metadata/item provenance use bounded non-authoritative JSON objects.
18. A parent-side database trigger removes session Project context when any existing writer revokes/deletes its session; existing logout and bulk-revocation writers are not coordinated rollout dependencies.
19. Full Set copy is WP3 work after public eligibility and governed-symbol locks exist; it fails atomically if any source item is currently ineligible.
20. One Set may be default for several Projects; each Project still has at most one default, enforced under per-Project locks.
21. All Stage 4 services revalidate and lock the current user/session/Organization/membership/role transaction-locally; route principals are user-ID hints only, while the trusted Request cookie identifies the exact session row. `/auth/me` and route guards share one pilot-aware capability predicate.
22. Live errors are `{error,detail}` or validation `{error,detail,issues}`; item response keys are mandatory with explicit nullable values; generated `app.openapi()` and the 20-route inventory are tested.
23. WP3 is an immediate L3 Contract/Security review boundary before WP4; WP4 receives fresh reviews before frontend work.
24. Project metadata and item provenance share the exact sorted-key UTF-8 JSON, 16,384-byte, finite-number and root-depth-one contract from the plan.

## Verified implementation seams

- `backend/symgov_backend/models/schema.py:43-70` — immutable organization-bound full session.
- `backend/symgov_backend/models/schema.py:73-109` — Organization and future nullable default-set reference.
- `backend/symgov_backend/models/schema.py:563-575` — stable GovernedSymbol/current revision.
- `backend/symgov_backend/models/schema.py:633-642` — append-only AuditEvent.
- `backend/symgov_backend/organization_authorization.py:23-75` — bound active member authority.
- `backend/symgov_backend/settings.py:145-187` — default-off flags including Symbol Sets.
- `backend/symgov_backend/routes/auth.py:96-103` — server-derived `symbolSetsEnabled` capability.
- `backend/symgov_backend/auth.py:458-478`, `backend/symgov_backend/routes/auth.py:432-439` — existing revocation writers preserved by parent-side context cleanup.
- `backend/symgov_backend/routes/organizations.py:51-89` — established `/org/me` authority style.
- `backend/symgov_backend/app.py:83-107` — router/CSRF/session mounting.
- `backend/symgov_backend/published_catalog.py:31-38` — complete current public-eligibility predicate.
- `frontend/src/App.jsx:466-500`, `frontend/src/adminRoutes.js:8-37` — mounted route extension points.
- `scripts/test-backend.sh:39-75`, `scripts/test-frontend.sh:21-23` — broad gates only.

## First action: WP0 only

1. Load the required skills.
2. Read this resume and all authoritative inputs as directed.
3. Recompute and compare all authoritative hashes.
4. Refresh branch, `HEAD`, `origin/main`, exact dirty/untracked inventory and Alembic heads.
5. Confirm no Stage 4 implementation paths already exist unexpectedly.
6. Run the focused organization baseline:

```bash
cd /docker/openclaw-hz0t/data/symgov
PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest \
  tests/test_organization_migration.py \
  tests/test_organization_auth_context.py \
  tests/test_organization_admin_api.py -q
```

7. Run the focused frontend baseline:

```bash
node --test \
  frontend/src/organizationSession.test.js \
  frontend/src/organizationAdmin.test.js \
  frontend/src/adminJourneys.test.js \
  frontend/src/adminMountedJourneys.test.js
```

8. Report WP0 GREEN or the exact blocker. Do not begin WP1 automatically unless the active user/controller explicitly authorizes implementation.

## Next authorized implementation package after WP0

WP1 only: additive migration/ORM/live-PostgreSQL invariants as specified in the Stage 4 plan, including frozen indexes and old-writer-compatible session-context revocation cleanup. The intended migration is new `backend/alembic/versions/20260822_0030_project_symbol_sets.py` from sole head `20260821_0029`.

WP1 is L3. It requires strict behavioral RED, disposable PostgreSQL evidence, a fresh Contract Review and then a fresh Security Review on identical bytes. It must not add runtime routes or UI.

## Verification cadence

- Focused exact files during development.
- Adjacent affected tests once when bytes stabilize.
- Broad backend/frontend wrapper once at the plan's package/batch gate.
- Live PostgreSQL only for migration, cross-table constraints, locks and races.
- One compact evidence block per accepted package.
- Fresh review after any correction to reviewed bytes.

## Authority boundaries

Do not commit, push, apply migration `0030` to shared/real data, deploy, restart services, activate `SYMGOV_SYMBOL_SETS_ENABLED`, publish/withdraw content, start Stage 5, modify gateways, send external messages, reset/clean/stash the tree, or touch `.claude/settings.local.json` without fresh explicit authority.

Implementation completion is not deployment or activation.

## Copy-ready prompt

Read and follow `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-08-22-symbol-set-management-stage4-luna-resume.md` exactly. Use GPT-5.6-Luna in max mode. Perform WP0 only: load the named skills, verify all pinned hashes and the live repository/Alembic state, preserve `.claude/settings.local.json`, run the two focused baseline commands, and report GREEN or the exact blocker. Do not create cards, edit product source, commit, push, migrate shared/real data, deploy, restart, activate flags, or begin WP1 without fresh explicit authorization.
