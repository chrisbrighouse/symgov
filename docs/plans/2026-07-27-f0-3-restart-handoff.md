# F0.3 Session-Authoritative Attribution — Concrete Restart Handoff

Date: 2026-07-27

## Authoritative programme state

Repository: `/docker/openclaw-hz0t/data/symgov` (host alias `/data/symgov`)

Branch: `main`

Implementation baseline: `63edef801e45768ac3a402a44f6941f490226c58`

Remote relation at handoff creation: local `main` and `origin/main` both at `63edef801e45768ac3a402a44f6941f490226c58`; zero ahead/behind.

Production state at handoff creation:

- F0.2 commit `63edef8` is pushed and deployed.
- `symgov-hermes-api` is running and healthy from `/data/symgov/backend`.
- API container start time: `2026-07-27T17:46:08.106974343Z`.
- Internal `/api/v1/health` returned `{"ok":true,"service":"symgov-api",...}` at the planning check.
- Production database remained at Alembic `20260721_0024 (head)` after the F0.2 release.
- F0.2 required no migration and no frontend publication.

Controlling programme record:

- `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`

F0.3 controlling specification:

- `docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md`

## Completed foundation evidence

### F0.1 — complete

Commit: `972f2b89ff6a534b6daa0572df644b041a770779`

Verified completion evidence is preserved in `docs/plans/2026-07-26-f0-1-restart-note.md`, including:

- portable backend: 756 passed, 3 deselected;
- full backend partitions: 784 backend nodes executed;
- frontend: 65 passed;
- Langfuse PoC: 12 passed;
- focused email outbox: 9 passed;
- two isolated Vite builds: 54 modules transformed each;
- verification-wrapper timeout, path-containment, partition and argument contracts passed;
- product behavior change: none;
- no migration/runtime activation was required for F0.1.

### F0.2 — complete and deployed

Commit: `63edef801e45768ac3a402a44f6941f490226c58`

Goal-local evidence in `docs/plans/2026-07-27-f0-2-workspace-authorization-spec.md`:

- 28 normalized workspace operations;
- 49 concrete route entries: 28 v1, 21 legacy;
- 10 normalized reviewer/admin and 18 normalized admin-only operations;
- focused auth matrix: 186 passed;
- workspace rights/asset regressions: 20 passed;
- portable backend: 933 passed, 3 deselected;
- Stage 1 `t_8c833387`: PASS;
- Stage 2 `t_b02100b2`: APPROVED;
- final commit task `t_241010cf`.

Broader pre-production evidence before release:

- full backend: 961 passed;
- frontend: 65 passed;
- Langfuse PoC: 12 passed;
- isolated production frontend build passed;
- wrapper contracts passed;
- 1,038 backend/frontend/Langfuse tests/assertions reported across the gate.

Live release verification:

- public health: HTTP 200;
- unauthenticated protected reviewer/admin routes: HTTP 401;
- temporary reviewer `/auth/me`: HTTP 200;
- v1 and legacy reviewer routes: HTTP 200;
- v1 and legacy admin-only routes: HTTP 403;
- temporary reviewer and verifier artifacts removed and absence verified;
- API healthy with restart count 0.

## F0.3 code-backed defect summary

At baseline `63edef8`, authorization is server-side but attribution is not:

- `schemas.py` accepts `deciderName`, `deciderRole`, and `updatedBy`;
- the live route handlers in `routes/workspace.py` persist those values or generic labels;
- human decisions have `decided_by=None`;
- human mutation audits frequently have `actor_id=None`;
- property feedback, rights evidence and duplicate overrides copy client identity;
- Rupert persistence records the publication service user as requester, approver and audit actor instead of the durable human decision actor;
- the React client creates/sends generic identity fields.

The existing schema already has nullable decision actor, audit actor and publication requester/approver foreign keys. F0.3 therefore expects no migration and no historical backfill.

## Approved execution lane

Use the durable `symgov` Kanban board assigned to profile `cody`, serialized on the shared repository:

1. F0.3 implementation;
2. fresh immutable Stage 1 specification review;
3. fresh immutable Stage 2 security/code-quality review;
4. final verification and one local commit.

Do not run parallel editing workers in this repository. If a review finds an actionable defect, create a correction card and a fresh replacement review chain.

## Preserved unrelated work

The authoritative main worktree was clean before these planning files were created.

Do not touch, merge, reset, clean or archive unrelated specialist worktrees:

- `/docker/openclaw-hz0t/data/symgov-langfuse-items45` — unfinished telemetry/usage-ledger work;
- `/docker/openclaw-hz0t/data/symgov-subscriptions-download-release-20260721` — unfinished email worker/configuration/documentation work;
- `/docker/openclaw-hz0t/data/symgov-profile-subscriptions-20260721` — retained historical feature worktree.

At pipeline start, the only intended uncommitted planning paths are:

- `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`;
- `docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md`;
- `docs/plans/2026-07-27-f0-3-restart-handoff.md`.

If status differs, stop and inventory it before editing.

## Safety and authority boundaries

F0.3 authorizes local source/test/documentation edits and test execution only.

Do not:

- push;
- deploy or rebuild production-mounted assets;
- migrate or mutate the production database;
- restart/recreate services or gateways;
- publish, withdraw or alter live symbols;
- send external messages;
- access or print secrets;
- clean, reset or stash unrelated work;
- begin F0.4.

The final F0.3 task was authorized to create one local commit only after both reviews approved the unchanged snapshot and every goal-local gate passed.

## F0.3 completed local checkpoint — 2026-07-27

Final task `t_ae5e0550` created one local F0.3 checkpoint commit from baseline `63edef801e45768ac3a402a44f6941f490226c58`. The task record is authoritative for the resulting SHA because this handoff is part of that same commit.

### Review and replacement history

- `t_ec15ea5f` — Stage 1 **FAIL** on five missing compliance-test boundaries; unrun Stage 2 `t_1ec5ceb0` archived; corrected by `t_505e9ad0`.
- `t_fe10e149` — replacement Stage 1 **FAIL** on missing durable human attribution in the real Libby follow-up path; unrun Stage 2 `t_e3732d53` archived; corrected by `t_ff7929fc`.
- `t_43629b56` — replacement Stage 1 **PASS**; `t_49658de9` — Stage 2 **FAIL** with one Important unbound publication revision-scope finding; corrected by `t_90916d04`.
- `t_e449d550` and child `t_ea7f07fc` — replacement review pair archived unrun when delayed planning review `deleg_4263a155` exposed additional live-runner, durable-queue, actor/executor and rollout boundaries; corrected by `t_fc2721b5`.
- `t_bcd01d5d` — final complete-boundary Stage 1 **PASS**; `t_af9698b2` — final Stage 2 **APPROVED**, no actionable findings. Final-task opening identity matched their 21 path hashes and all three patch hashes exactly.

### Fresh final gate evidence

UTC timestamps on 2026-07-27:

- `22:43:15` exact 12-file `py_compile`: pass, 0.11 seconds.
- `22:43:15` exact nine-file isolated focused pytest gate, including repository-runner import-boundary tests: 279 passed/64 warnings in 4.95 seconds, 6.17 seconds wall.
- `22:43:27` portable backend: 1,002 passed/3 deselected/1,180 warnings in 21.27 seconds, 23.43 seconds wall.
- `22:43:50` frontend: 67 passed/0 failed in 229.73 milliseconds, 0.26 seconds wall.
- `22:43:50` isolated Vite build: 54 modules, 1.54 seconds, 1.90 seconds wall.
- `22:43:52` verification-script contracts: pass, 2.32 seconds.
- `22:44:16` canonical `npm run build`: 54 modules, 1.50 seconds, 1.84 seconds wall.
- `22:44:18` tracked diff check and all four untracked no-index checks: clean, 0.01 seconds for the tracked check.

### Migration, runtime, deployment and residual state

- Migration: none; no schema revision and no historical actor backfill.
- Runtime/deployment: F0.2 remains the deployed production state described above. F0.3 is local only: no push, deployment, migration, asset publication, service/gateway restart, database mutation, live publication/withdrawal or external message.
- Release requirement: pause all four review mutations, publication handoffs and Rupert claims; drain active publication execution; atomically activate one immutable backend/frontend/repository-runner release; pass all section 10 health/auth/422/no-side-effect/legacy-absence/frontend/import/synthetic-attribution smoke gates; resume only after complete success, otherwise roll back atomically while paused. Mixed-version rollout is prohibited.
- Residuals: reviewer eligibility remains global until F1.3; feedback/review requests can still affect publication state until F0.4; per-child transaction boundaries and publication idempotency remain later work; historical actor-null records remain unattributed by design; FastAPI `on_event` deprecation warnings remain.
- Next: F0.4, whose implementation-ready specification does not yet exist. No F0.4 work was performed.

## Copy-ready continuation prompt

```text
Continue the Symgov Trial Readiness programme in /docker/openclaw-hz0t/data/symgov.
Read the master backlog, the F0.3 controlling spec and this completed handoff first.
Verify the current branch, clean status, local/remote relation and the exact F0.3 commit
recorded by Kanban task t_ae5e0550 before relying on this point-in-time note.

F0.3 is complete locally but not pushed or deployed. Do not deploy it unless separately
authorized; any release must use the exact paused atomic backend/frontend/repository-runner
sequence and all smoke/synthetic-attribution/rollback gates in section 10 of the F0.3 spec.

The next backlog goal is F0.4 — Separate review requests from publication withdrawal.
Author its implementation-ready specification first, including code-backed current state,
authorization/actor/lifecycle rules, tests, migration/runtime implications, completion gate
and fresh serialized Kanban review chain. Do not implement F0.4 in the specification session.
Do not push, deploy, migrate, restart services/gateways, publish/withdraw, send external
messages, or clean/reset/stash without separate authorization.
```
