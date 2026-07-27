# F0.3 — Session-Authoritative Review and Publication Attribution

> **Status:** COMPLETE, NOT DEPLOYED. F0.3 used the durable Symgov Kanban/Cody lane, made the authenticated session the sole authority for human actor identity, and passed fresh immutable Stage 1 and Stage 2 reviews. Do not begin F0.4 implementation before its own specification and review lane exist.

**Parent backlog:** `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`

**Baseline:** clean, pushed and deployed `main` at `63edef801e45768ac3a402a44f6941f490226c58` (`feat: enforce workspace operation authorization`)

**Goal:** Ensure every human review decision, rights decision, split-child decision, review-property edit, audit event and human-approved publication record is attributed to the authenticated human rather than to editable client fields, a generic label, `NULL`, or the publication service account.

**Architecture:** Keep the existing nullable `human_review_decisions.decided_by`, name/role snapshot columns, `audit_events.actor_id`, and `publication_jobs.requested_by`/`approved_by` columns. Inject `AuthenticatedUser` into each live human mutation route, derive a deterministic review-operation role from the session, persist the actor and snapshots in the same transaction, carry the decision identity through the publication handoff, and resolve the authoritative approver from the durable decision at publication persistence time. Historical rows remain unchanged and readable.

**Tech stack:** FastAPI dependencies, Pydantic, SQLAlchemy/PostgreSQL, React/Vite, pytest, Node tests, durable Rupert handoff.

---

## 1. Purpose and governance outcome

The F0.2 authorization boundary now limits review mutations to authenticated reviewers or administrators, but the live handlers still accept identity-like fields from the request body and often persist no actor ID:

- generic review decisions accept `deciderName` and `deciderRole`;
- rights decisions accept the same fields and copy them into provenance evidence;
- split-child decisions use the same client values in decisions, duplicate overrides and feedback events;
- symbol-property updates accept `updatedBy` and write an audit event with `actor_id=None`;
- human decision and split audit events use `actor_id=None`;
- publication handoff audit uses `actor_id=None`;
- Rupert persistence sets `PublicationJob.requested_by`, `approved_by`, and publication audit actors to the service user.

A caller who is legitimately authorized as themselves can therefore create durable records that appear to have been decided by another person or role. This is an accountability defect even though F0.2 prevents an unauthorized role from reaching the route.

Observable F0.3 outcome:

1. The session supplies an immutable actor UUID, display-name snapshot and effective role snapshot.
2. Client attempts to submit `deciderName`, `deciderRole`, or `updatedBy` are rejected before mutation or handoff.
3. New review decisions have `decided_by=<session user UUID>` and server-derived snapshots.
4. New human mutation audit events have `actor_id=<session user UUID>`.
5. Review actions created from a decision carry the same human `created_by_id`.
6. A publication caused by a human approval records that same human as the publication requester/approver and on the human handoff governance event; Rupert's service account is the actor on execution-completion events, with durable approval provenance retained in their metadata.
7. Pre-F0.3 decisions and publication rows remain readable without fabricated backfill.

---

## 2. Code-backed current state

### 2.1 Session identity is already authoritative and entitlement-aware

`backend/symgov_backend/auth.py` defines `AuthenticatedUser` with:

- `id`;
- `email`;
- `display_name`;
- effective `roles` after current subscription resolution;
- account and subscription state.

`current_user_from_token()` loads the active user and current effective roles. `backend/symgov_backend/dependencies.py` exposes `require_user()` and F0.2's `require_workspace_access()`. The live v1 and legacy workspace routers are mounted through `require_workspace_access()` in `backend/symgov_backend/app.py`.

The router-level dependency currently authorizes the request but does not place its return value in route function parameters. F0.3 must inject the same dependency into the four human mutation handlers so the actor used for persistence is the actor already authorized for that exact route.

### 2.2 Client-controlled request fields

`backend/symgov_backend/schemas.py` currently exposes:

- `WorkspaceReviewDecisionRequest.deciderName` / `.deciderRole`;
- `WorkspaceRightsReviewDecisionRequest.deciderName` / `.deciderRole`;
- `WorkspaceSplitReviewProcessRequest.deciderName` / `.deciderRole`;
- `WorkspaceReviewSymbolPropertiesUpdateRequest.updatedBy`.

`frontend/src/App.jsx` constructs and sends generic `Human`, `SME reviewer`, `sme_reviewer`, `rights_reviewer`, and `updatedBy: 'Human'` values. The identity is not visible as an editable text input in every case, but it is still attacker-controlled JSON.

### 2.3 Live human mutation sinks

The active module is `backend/symgov_backend/routes/workspace.py` (mounted by `app.py`). The similarly named `backend/symgov_backend/workspace.py` is not mounted and is outside F0.3 unless an import/search proves a live caller requires parity.

The live handlers requiring correction are:

1. `update_workspace_review_symbol_properties()`
   - writes `ReviewSymbolProperty.updated_by` from `request.updatedBy`;
   - builds feedback events from that client value;
   - writes `review_symbol_properties_updated` with `actor_id=None`.

2. `create_workspace_rights_review_decision()`
   - writes client identity into `reviewer_rights_correction` evidence;
   - writes `HumanReviewDecision.decided_by=None` and client snapshots;
   - writes `rights_review_decision_recorded` with `actor_id=None`.

3. `create_workspace_review_decision()`
   - writes `HumanReviewDecision.decided_by=None` and client snapshots;
   - writes `human_review_decision_recorded` with `actor_id=None`;
   - may immediately trigger publication or review follow-up.

4. `process_workspace_split_review_decisions()`
   - writes one or more decisions using client identity;
   - copies the client name into duplicate override evidence;
   - writes agent feedback using client name/role;
   - writes split-child and terminal/duplicate audit events with `actor_id=None`;
   - may trigger publication per child.

`create_review_action()` already copies `decision.decided_by` into `ReviewCaseAction.created_by_id`; it becomes correct when the decision is correct.

### 2.4 Publication attribution drift

`backend/symgov_backend/publication_handoff.py` creates Rupert work with `review_decision_id` and `human_approved=True`, but the queue payload does not contain an authoritative actor snapshot and `publication_handoff_completed` uses `actor_id=None`.

`backend/symgov_backend/runtime.py::persist_publication_execution()` currently creates or updates the publication job with the inactive publication service user as both requester and approver, and uses that service user as the actor on publication job, pack and page audit events. This records execution identity where human approval identity is required.

### 2.5 Existing schema supports F0.3

No new column is needed:

- `human_review_decisions.decided_by` is an existing nullable user foreign key;
- `decider_name` and `decider_role` are non-null historical snapshots;
- `audit_events.actor_id` is nullable;
- `publication_jobs.requested_by` and `approved_by` already reference users;
- `ReviewSymbolProperty.updated_by` can remain a display snapshot while its audit event carries actor UUID.

Historical nullable actor fields must not be guessed or bulk-filled.

---

## 3. Authoritative actor contract

### 3.1 Actor source

For every in-scope human mutation, derive values only from the injected `AuthenticatedUser`:

- actor UUID: `uuid.UUID(current_user.id)`;
- display-name snapshot: normalized non-empty `current_user.display_name`;
- current effective roles: `current_user.roles` as resolved by the authenticated session.

Never query identity from request JSON, hidden frontend state, a queue payload without database verification, or a fixed `Human`/agent label.

If the session actor ID is malformed or the display name is unexpectedly empty, fail before any mutation. Do not substitute a generic name.

### 3.2 Effective role snapshot

The current persisted `decider_role` is singular while sessions can carry multiple additive roles. For F0.3 review operations, derive one truthful authorization role with this deterministic precedence:

1. `reviewer` when the session has the effective `reviewer` role;
2. otherwise `admin` when the session has the effective `admin` role;
3. otherwise fail closed.

This records the role under which the human performed a review operation. It must never invent `sme_reviewer` or `rights_reviewer`, because those are not current authenticated roles. An admin who also has `reviewer` is recorded as `reviewer` for these reviewer/admin operations. Preserve the complete sorted session role set in decision/audit payload metadata only where useful for explanation; it is not a replacement for the singular effective-role snapshot.

Implement the derivation once in a small backend helper and unit-test reviewer-only, admin-only, reviewer+admin, empty, and unrelated-role cases.

### 3.3 Request spoofing policy

The three decision request models and the property-update model must explicitly reject these legacy identity keys when present:

- `deciderName`;
- `deciderRole`;
- `updatedBy`.

A direct or wrapped request containing one of these fields returns the normal FastAPI 422 validation contract before persistence, audit creation, queue write or runner invocation. Do not silently prefer the server value while continuing to advertise identity fields in OpenAPI/Pydantic schemas.

The request models may preserve current behavior for unrelated unknown fields if needed for compatibility; the security requirement is an explicit fail-closed check for the identity keys.

The React client must stop creating, retaining or sending these fields.

### 3.4 Historical read compatibility

- Keep `deciderName`, `deciderRole`, and `updatedBy` response fields so old snapshots remain human-readable.
- Keep `HumanReviewDecision.decided_by` nullable.
- Do not rewrite old decision summaries, provenance JSON, audit actors or publication jobs.
- New rows must always have the actor where the action is human.
- Tests must construct at least one historical decision with `decided_by=None` and verify existing list/detail serializers still return its stored name/role.

---

## 4. In-scope behavior

### 4.1 Review-property updates

For the v1-only `PATCH /api/v1/workspace/review-cases/{id}/symbol-properties` operation:

- inject the authorized session user;
- set `ReviewSymbolProperty.updated_by` to the session display-name snapshot;
- pass the server-derived name/effective role to agent-feedback construction;
- write `review_symbol_properties_updated.actor_id` as the session user UUID;
- include safe actor snapshot metadata in the audit payload if the existing audit conventions support it;
- reject `updatedBy` spoofing before calling `remember_property_option()`, adding feedback events, or mutating the property row.

No `updated_by_actor_id` schema column is introduced in F0.3; durable UUID accountability is supplied by the same-transaction audit event.

### 4.2 Generic review decisions

For the v1-only `POST /api/v1/workspace/review-cases/{id}/decisions` operation:

- inject the session actor;
- derive the effective role;
- persist `decided_by`, `decider_name`, and `decider_role` from the session;
- build `decision_summary` from the server-derived display name;
- write `human_review_decision_recorded.actor_id` as the same UUID;
- ensure every generated `ReviewCaseAction.created_by_id` equals that UUID;
- carry safe actor snapshots into downstream review-follow-up payloads from the persisted decision, not the request;
- reject identity spoofing before superseding previous decisions or creating any action.

### 4.3 Rights decisions

For the v1-only `POST /api/v1/workspace/rights-review-cases/{id}/decisions` operation:

- apply the same decision/audit rules;
- derive `reviewer_rights_correction.decider_name` and `.decider_role` from the session;
- retain evidence and rights corrections exactly as supplied within their existing validation rules;
- reject identity spoofing before superseding prior decisions or mutating the provenance assessment.

### 4.4 Split-child decisions

For the v1-only `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions` operation:

- use one frozen session actor snapshot for every child processed by the request;
- populate every `HumanReviewDecision` and every related audit event with that actor;
- populate duplicate override `reviewed_by` and feedback reviewer metadata from the session;
- ensure all actions have `created_by_id` equal to the actor;
- reject identity spoofing before processing the first child, so no partial mutation occurs because of spoof fields.

Existing per-child transaction behavior is not redesigned in F0.3.

### 4.5 Publication handoff and persistence

For a queue item marked `human_approved=True` and sourced from a review decision:

1. `publication_handoff.py` must use the persisted decision as the actor source.
2. The handoff payload may include actor UUID/name/effective-role snapshots for traceability, but those payload values are not authoritative at persistence time.
3. `publication_handoff_completed.actor_id` must be `decision.decided_by`.
4. `runtime.py::persist_publication_execution()` must load the referenced `HumanReviewDecision` from PostgreSQL using `review_decision_id`/`source_id` and validate:
   - outer `source_type` is exactly `review_decision`;
   - outer `source_id` equals payload `review_decision_id`;
   - the decision exists;
   - its code is `approve` for a human-approved publication;
   - payload `review_case_id` equals the decision's durable `review_case_id`;
   - `decided_by` is present;
   - the handoff and artifact revision lists are exact, non-empty, duplicate-free matches and every durable revision is bound to that decision;
   - any existing durable `AgentQueueItem` agrees with the runtime/file queue on agent, source, decision, case, revision, human-approval and approval-actor identity fields;
   - any queue actor snapshot agrees with the durable decision.
5. A missing/mismatched actor fails closed before creating/updating publication jobs, pages, entries or audit events.
6. Define `PublicationJob.requested_by` as the authenticated human whose approved decision triggered this job, and set both `requested_by` and `approved_by` to that durable human actor for this flow.
7. Persist an `approval_actor` snapshot containing the durable human ID, decision-time display name and effective role in `artifact_manifest_json` and publication governance audit payloads, so the approval remains readable if the user is later renamed or deactivated.
8. Set the human approval/handoff governance event `publication_handoff_completed.actor_id` to that human actor.
9. Set execution-completion events `publication_pack_published`, `publication_job_completed`, and `published_page_upserted` to the Rupert/publication service actor that performed the writes. Their payloads must retain `approval_actor`, `review_decision_id`, and `execution_actor`; the service actor must never replace approval provenance.

Tests must prove a spoofed queue actor cannot override the durable decision actor.

### 4.6 Human versus system events

Do not blanket-replace every `actor_id=None` in the repository. Agent-generated or system reconciliation events may legitimately use no human actor or an explicit service actor. F0.3 changes only events directly caused by the four authenticated human mutation routes and the human-approved publication chain derived from them.

---

## 5. Explicit exclusions

F0.3 does not include:

- reviewer discipline/case-scope authorization (F1.1–F1.3);
- review-without-unpublication behavior (F0.4);
- account security, forced PIN change, throttling or CSRF policy (F0.5);
- lifecycle transition-service redesign (F2.2);
- publication idempotency/outbox redesign (F2.4);
- autonomous publication or agent authority expansion;
- backfilling guessed actors into historical records;
- adding organizations, memberships or tenant identity;
- renaming authenticated roles or adding `sme_reviewer` / `rights_reviewer` roles;
- broad audit cleanup outside the in-scope human routes;
- deployment, production migration, API restart, push, external messaging, publication or withdrawal;
- edits to the inactive duplicate `backend/symgov_backend/workspace.py` unless a concrete live import is proved and documented before implementation.

---

## 6. Expected implementation paths

Expected modifications:

- `backend/symgov_backend/auth.py` or `backend/symgov_backend/dependencies.py`
  - one deterministic review-operation actor/effective-role helper.
- `backend/symgov_backend/schemas.py`
  - remove identity authority from request contracts and reject legacy spoof keys.
- `backend/symgov_backend/routes/workspace.py`
  - inject actor and persist it across four human mutation handlers/audits.
- `backend/symgov_backend/publication_handoff.py`
  - carry decision-derived actor and attribute handoff audit.
- `backend/symgov_backend/agent_queue_worker.py`
  - route every generic Rupert queue claim through the same repository-owned runner.
- `backend/symgov_backend/runtime.py`
  - resolve durable human approval and persist publication actor/executor separation.
- `scripts/run_rupert_publication.py`
  - repository-owned Rupert entrypoint that imports the repository backend as the authoritative runtime.
- `frontend/src/App.jsx`
  - remove generic identity state and payload fields; display the logged-in user where reviewer context is shown.
- optionally `frontend/src/api.js`
  - only if a narrow sanitizer/contract test is the cleanest way to prove identity fields are never sent.
- focused backend and frontend tests named below.
- `README.md` and `backend/README.md`
  - document session-authoritative review/publication identity.
- this specification and the master backlog completion/handoff sections.

A migration file is not expected. If implementation discovers a genuine schema requirement, block and return to specification review rather than adding an unreviewed migration.

---

## 7. Test contract

### 7.1 Actor helper tests

Add focused tests proving:

- reviewer-only → `reviewer`;
- admin-only → `admin`;
- reviewer+admin → `reviewer`;
- missing/unrelated role → fail closed;
- actor UUID and non-empty display snapshot come from `AuthenticatedUser`.

### 7.2 Route spoofing matrix

Using the real FastAPI app and dependency overrides, cover each concrete mutation operation on its declared surface. At baseline all four F0.3 mutation operations are deliberately v1-only; F0.3 must not add legacy aliases merely to create parity. Assert the existing legacy URL remains absent while the v1 route enforces the identity contract:

- generic v1 decision with spoofed `deciderName` and separately spoofed `deciderRole` → 422;
- rights v1 decision with both spoof keys → 422;
- split-processing v1 request with spoof keys → 422;
- property-update v1 request with spoofed `updatedBy` → 422;
- no persistence, audit, queue write, subprocess/runner or handler side effect occurs on each rejection.

For each corresponding `/api/workspace/...` legacy URL, assert route absence remains the baseline contract rather than treating 404 as an authorization result.

Retain F0.2 anonymous 401 and denied-role 403 behavior.

### 7.3 Persistence tests

For reviewer-only and admin-only authenticated sessions, assert at the ORM/session boundary:

- generic decision `decided_by`, name and role;
- rights decision actor plus provenance evidence snapshots;
- every split-child decision actor and duplicate/feedback snapshot;
- property `updated_by` display snapshot;
- all in-scope audit `actor_id` values;
- all action `created_by_id` values;
- server-generated summaries use the session display name;
- client cannot select another name or role.

A test that merely checks the HTTP response is insufficient; inspect the added/committed durable objects.

### 7.4 Publication-chain tests

Add tests proving:

- the Rupert queue is derived from an approved decision with a non-null human actor;
- `publication_handoff_completed` has the human actor;
- runtime publication persistence sets job requester/approver to the durable decision actor;
- handoff governance audit uses the human actor, while publication pack/job/page execution-completion audits use the service executor;
- publication artifact/audit payloads retain the decision-time approval display name and effective-role snapshot;
- every execution event retains durable `approval_actor`, review-decision lineage, and `execution_actor` metadata;
- independent source-type, source/decision-ID, case-ID, revision-scope, queue-actor, and durable-queue/file mismatch cases fail before service-user resolution or publication writes;
- missing decision, non-approve decision, and historical actor-null decision fail closed for new human-approved execution;
- a valid retry for the same decision retains the same actor;
- `publication_handoff.py` invokes the repository-owned Rupert runner and a subprocess import probe resolves `RuntimePersistenceBridge.__file__` to this repository's `backend/symgov_backend/runtime.py`.

### 7.5 Historical readability

Verify an existing-style `HumanReviewDecision(decided_by=None, decider_name=<stored>, decider_role=<stored>)` still serializes in list/detail/response form with the stored snapshots. Do not alter old data in the test.

### 7.6 Frontend contract

Add or update Node/source-contract tests proving:

- review, rights, split and property API payloads omit identity fields;
- no editable or hidden client state is treated as actor authority;
- reviewer context uses the authenticated user's display name rather than the hard-coded `Human` label;
- the production frontend build remains green.

---

## 8. Verification commands and completion gate

The bounded completion gate is directly runnable from the repository root:

```bash
python3 -m py_compile \
  backend/symgov_backend/agent_queue_worker.py \
  backend/symgov_backend/auth.py \
  backend/symgov_backend/schemas.py \
  backend/symgov_backend/routes/workspace.py \
  backend/symgov_backend/publication_handoff.py \
  backend/symgov_backend/review_followup_handoff.py \
  backend/symgov_backend/runtime.py \
  scripts/run_rupert_publication.py \
  tests/test_f0_3_session_attribution.py \
  tests/test_route_auth_enforcement.py \
  tests/test_rupert_runner_import_boundary.py \
  tests/test_workspace_rights_review_api.py

PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest \
  tests/test_auth_dependencies.py \
  tests/test_route_auth_enforcement.py \
  tests/test_workspace_rights_review_api.py \
  tests/test_published_symbol_review_workflow.py \
  tests/test_duplicate_exception_workflow.py \
  tests/test_publication_handoff_split_status.py \
  tests/test_service_user_entitlements.py \
  tests/test_f0_3_session_attribution.py \
  tests/test_rupert_runner_import_boundary.py -q

./scripts/test-backend.sh
./scripts/test-frontend.sh
./scripts/build-frontend-isolated.sh
./scripts/test-verification-scripts.sh
git diff --check
```

The `py_compile` list names every currently changed Python implementation/test path and the repository runner; if the final diff changes, regenerate this list deterministically with `git diff --name-only --diff-filter=ACMR HEAD -- '*.py'` plus `git ls-files --others --exclude-standard -- '*.py'`, sort/deduplicate it, and compile every result. Run exact frontend tests through the wrapper rather than assuming `npm test` exists. The isolated build must not alter repository `dist/`.

The broader `./scripts/test-backend.sh --full`, Langfuse PoC and production release/smoke gates are separately sequenced pre-production/release checks unless a changed external-workspace file makes them directly necessary. The local F0.3 completion gate still requires portable backend, frontend tests, isolated build and wrapper contracts because frontend payload behavior changes.

Record exact commands, outputs, exit codes, test counts and wall durations. Do not label a timeout or partitioned partial run as a full pass.

---

## 9. Review pipeline

Use one serialized dependency chain on the shared clean repository:

1. **Cody implementation** — spec-driven implementation with RED evidence for spoofing/attribution gaps, then GREEN focused and goal-local gates. No commit.
2. **Fresh Stage 1 specification review** — immutable read-only review of every changed/untracked path against this spec and the master backlog.
3. **Fresh Stage 2 security/code-quality review** — immutable read-only review after Stage 1 PASS, emphasizing spoofing, route parity, transaction ordering, queue trust, historical compatibility and service/human identity separation.
4. **Final verification/local checkpoint** — rerun all goal-local gates against the unchanged approved snapshot, update completion evidence, stage exact F0.3 paths and create one local conventional commit.

If either review finds an actionable issue, create a correction card and a fresh replacement review chain. Do not reuse a stale approval after edits.

Reviewers may freeze HEAD, branch/status and SHA-256 for every changed/untracked path using bounded read-only diagnostics. Any opening/closing identity drift is a blocking review result.

---

## 10. Migration, activation and rollback

### Data migration

None expected. Existing nullable actor columns and snapshot fields are retained. No historical backfill is permitted because the real actor cannot be reliably inferred.

### Deployment precondition

Before a later production activation, inspect pending/running Rupert queue items marked human-approved. A pre-F0.3 item whose durable decision has `decided_by=NULL` cannot satisfy the new fail-closed runtime contract; it must be resolved through a separately authorized human/operational procedure rather than silently attributed to the service user.

### Runtime/deployment

F0.3 changes backend, frontend, and the repository-owned Rupert runtime boundary, but this implementation pipeline authorizes no push, deployment, migration, rebuild of production-mounted assets, API restart or live smoke mutation. Release remains a separate CEO-authorized step after broader pre-production gates.

A later authorized release must use this fail-closed sequence; no mixed-version window is permitted:

1. Pause all four authenticated review mutation operations and publication handoffs, stop new Rupert queue claims, and verify no publication execution is running. Read-only traffic may continue.
2. Activate one immutable release containing the backend, frontend assets, and `scripts/run_rupert_publication.py`; the backend and `publication_handoff.py` must come from the same repository revision. Do not activate any one component early.
3. While the pause remains in force, pass every smoke gate: health returns 200; anonymous review mutations return 401; denied roles return 403; direct and wrapped spoof keys return 422 on all four v1 operations with no DB/audit/queue/runtime-file delta; all four legacy mutation aliases remain absent; the deployed frontend sends none of `deciderName`, `deciderRole`, or `updatedBy`; and a runner import probe proves `RuntimePersistenceBridge.__file__` is the deployed repository `backend/symgov_backend/runtime.py`.
4. Run one authorized synthetic approved-decision publication through Rupert and verify `PublicationJob.requested_by`/`approved_by` equal the human trigger, `publication_handoff_completed` uses the human actor, execution-completion events use the service actor, and all execution payloads retain approval/decision/executor lineage. Remove the synthetic records and verify absence.
5. Resume review/publication mutations and Rupert claims only after all gates pass. If any gate fails, keep the pause, atomically restore the prior backend/frontend/runner release, and repeat its health/import checks before deciding whether to resume.

### Rollback

Code rollback is sufficient because no schema change is expected. Rollback reopens impersonation and false approval attribution and is therefore a governance regression; if rollback is unavoidable, suspend human review/publication mutation access until corrected code is active.

---

## 11. Acceptance criteria

F0.3 is accepted only when all are observable:

1. All four live human mutation handlers obtain `AuthenticatedUser` from the route dependency.
2. Session UUID, display name and effective review role are derived once and used consistently.
3. Direct and wrapped spoof identity fields return 422 on all four declared v1 mutation surfaces before side effects; the deliberately absent legacy aliases remain absent.
4. New generic, rights and split decisions persist the session UUID and truthful snapshots.
5. Review property edits persist the session display snapshot and same-transaction actor-attributed audit.
6. Every in-scope human audit and action has the actual human actor.
7. Rights evidence, duplicate overrides and feedback events use server-derived actor snapshots.
8. Human-approved publication handoff and runtime persistence resolve the actor from the durable decision.
9. Publication job requester/approver and handoff governance audit actor are the human; pack/job/page execution-completion audit actors are the service executor, with human approval provenance separately durable.
10. Source/decision/case/revision/approval spoofing, durable queue/file disagreement, and missing durable approval fail closed before service-user resolution or publication mutation.
11. Historical actor-null decisions remain readable with stored snapshots and no guessed backfill.
12. Frontend payloads contain no client identity authority and show the authenticated user where context is displayed.
13. Focused route/persistence/publication tests, portable backend, frontend, isolated build, wrapper contracts and diff checks pass.
14. Fresh Stage 1 returns PASS and fresh Stage 2 returns APPROVED on the unchanged snapshot.
15. One exact local commit is created with a clean authoritative worktree; nothing is pushed or deployed.

---

## 12. Residual risks after F0.3

- Review eligibility remains global rather than discipline/case-scoped until F1.3.
- Feedback/review requests can still affect publication state until F0.4.
- Per-child processing transaction boundaries and publication handoff idempotency remain later lifecycle work.
- Historical actor-null records remain unattributed by design.
- F0.3 does not convert legitimate agent/system audit events into human events.
- The inactive duplicate workspace module remains technical debt unless separately removed after import proof.

The next backlog goal after F0.3 is **F0.4 — Separate review requests from publication withdrawal**.

---

## 13. Completion evidence — 2026-07-27

F0.3 is complete at one local checkpoint created by final task `t_ae5e0550`; the task record carries the resulting commit SHA because this completion record is committed atomically with the implementation and cannot self-embed its own Git identity.

### Immutable review chain

The review history is retained rather than presenting only the final green pair:

- Stage 1 `t_ec15ea5f` failed on five missing compliance-test boundaries; its unrun Stage 2 child `t_1ec5ceb0` was archived. Correction task `t_505e9ad0` added the required route, multi-child, real-handoff, no-write and role/ORM coverage.
- Replacement Stage 1 `t_fe10e149` failed on missing durable human attribution in the real Libby follow-up path; its unrun Stage 2 child `t_e3732d53` was archived. Correction task `t_ff7929fc` made that handoff fail closed and decision-attributed.
- Replacement Stage 1 `t_43629b56` passed; Stage 2 `t_49658de9` then found the Important unbound publication revision-scope defect. Correction task `t_90916d04` bound exact non-empty duplicate-free artifact/handoff scope and every durable revision to the human decision before writes.
- Replacement Stage 1 `t_e449d550` and its unrun child `t_ea7f07fc` were archived before review when delayed planning review `deleg_4263a155` identified broader live-runner, durable-queue, actor/executor and rollout boundaries. Correction task `t_fc2721b5` closed those boundaries.
- Final complete-boundary Stage 1 `t_bcd01d5d` returned **PASS** and final Stage 2 `t_af9698b2` returned **APPROVED** with no actionable finding. Final-task opening SHA-256 and patch identities exactly matched both reviews across all 21 changed/untracked paths.

### Fresh final verification

All times are UTC on 2026-07-27 and all commands exited 0:

- `22:43:15` — exact 12-file `python3 -m py_compile` list in section 8: passed in 0.11 seconds.
- `22:43:15` — exact nine-file isolated focused pytest command in section 8, including `tests/test_rupert_runner_import_boundary.py`: 279 passed, 64 warnings in 4.95 seconds (6.17 seconds wall).
- `22:43:27` — `./scripts/test-backend.sh`: 1,002 passed, 3 deselected, 1,180 warnings in 21.27 seconds (23.43 seconds wall).
- `22:43:50` — `./scripts/test-frontend.sh`: 67 passed, 0 failed in 229.73 milliseconds (0.26 seconds wall).
- `22:43:50` — `./scripts/build-frontend-isolated.sh`: 54 modules transformed, built in 1.54 seconds (1.90 seconds wall) outside the repository.
- `22:43:52` — `./scripts/test-verification-scripts.sh`: verification-script contract probes passed in 2.32 seconds.
- `22:44:16` — canonical `npm run build`: 54 modules transformed, built in 1.50 seconds (1.84 seconds wall).
- `22:44:18` — `git diff --check`: clean in 0.01 seconds; no-index checks for all four untracked files returned only the expected diff exit 1 with empty diagnostics.

The focused suite exercises the direct publication handoff and generic agent-worker repository-runner boundaries and proves in subprocesses that `RuntimePersistenceBridge.__file__` resolves to this repository's `backend/symgov_backend/runtime.py`.

### Migration, runtime and release state

- Data migration: none; no migration path changed and no historical actor backfill was attempted.
- Runtime/deployment: unchanged from the F0.2 deployment recorded in the restart handoff. F0.3 was not pushed, deployed, migrated, activated or smoke-tested against production; no service/gateway restart, database mutation, live publication/withdrawal or external message occurred.
- Rollout requirement: before any later activation, pause all four review mutations, publication handoffs and new Rupert claims; verify no publication execution is running; atomically activate one immutable backend/frontend/repository-runner release; pass every health/auth/422/no-side-effect/legacy-absence/frontend/import/synthetic-attribution smoke gate in section 10; resume only on complete success, otherwise roll back atomically while the pause remains.

### Residuals and next goal

Reviewer eligibility remains global until F1.3; feedback/review requests can still affect publication state until F0.4; per-child transaction boundaries and publication idempotency remain later work; historical actor-null records remain unattributed by design; and existing FastAPI `on_event` deprecation warnings remain. The next goal is F0.4, but its implementation-ready specification has not yet been authored; no F0.4 work was performed in this checkpoint.
