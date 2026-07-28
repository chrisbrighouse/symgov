# Symgov Trial Readiness Implementation Backlog

> **For Hermes:** Execute one backlog goal at a time. Load `symgov-assistant-operations` and the relevant specialist skills before implementation. Route substantial Symgov coding through the durable Kanban/Cody lane with its review gates; use direct implementation only when the goal is small enough and that lane is explicitly chosen. This programme is spec-driven but does not require strict test-first TDD. Do not begin the next goal until the current goal has passed its completion gate and Chris has accepted any product decision it exposes.

**Goal:** Reconcile the shared ChatGPT trial-readiness proposal with the actual Symgov implementation and deliver a sequenced backlog of small, independently testable improvements leading to a controlled external trial.

**Architecture:** Preserve the existing individual-account, Free/Plus, additive-role, agent-queue, review, publication, Catalog, and transactional-outbox foundations. Close security and attribution defects first, then add reviewer scope, capability requests, lifecycle safety, quality gates, durable user journeys, operational controls, measured intelligence, and finally controlled-trial tooling. Human authority remains final for approval, publication, withdrawal, policy exceptions, and licensing conflicts.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic/PostgreSQL, React/Vite, pytest, Node tests, Hermes/Symgov agents, AgentMail, object storage, Docker/Traefik/Nginx.

**Source:** Shared ChatGPT conversation `https://chatgpt.com/s/t_6a65e6231ee4819182752cf42648e41c`, inspected 2026-07-26, then checked against repository `main` at `f5b1381`.

---

## 1. Planning rules

### 1.1 Definition of a backlog goal

Every goal below must receive a short implementation spec before code changes begin. The spec must contain:

1. Purpose and user/operational outcome.
2. Current-state evidence with exact files and tests.
3. In-scope behaviour and explicit exclusions.
4. Domain rules, state transitions, authorization and audit rules.
5. API/data/UI contracts where relevant.
6. Migration and rollback approach.
7. Acceptance criteria written as observable outcomes.
8. Focused verification commands and broader regression checks.
9. Deployment or runtime implications.
10. Completion gate and a copy-ready continuation prompt.

A spec may conclude that no code is needed or that a proposal item should be deferred.

### 1.2 Testing approach

Strict TDD is not mandatory. Each goal must nevertheless have tests at the right boundary:

- domain/state rules: focused unit/service tests;
- authorization and API contracts: route tests with real dependency guards;
- persistence: model plus Alembic upgrade/downgrade or disposable-database checks;
- frontend journeys: component/contract tests and a production build;
- agent/worker changes: deterministic fixture tests, idempotency and failure recovery;
- operational changes: failure-injection or disposable restore/recovery exercises.

Tests may be written before, during or immediately after the implementation, but the goal cannot close without executable evidence.

### 1.3 Baseline test partitions

Do not use one guessed `pytest` command as the only quality gate. The repository currently needs explicit partitions:

```bash
# Portable backend (clean uv, bounded outer timeout)
./scripts/test-backend.sh

# Separately managed agent workspaces, or both backend partitions
./scripts/test-backend.sh --external
./scripts/test-backend.sh --full

# Langfuse synthetic POC, frontend Node tests and an isolated production build
./scripts/test-langfuse-poc.sh
./scripts/test-frontend.sh
./scripts/build-frontend-isolated.sh
```

All partitions have finite outer timeouts. The portable backend defaults to five
minutes; each external backend process, the Langfuse POC, frontend Node tests and
the isolated build default to two minutes. Timeout overrides must be positive
integer seconds, and the isolated build canonicalizes its absolute output path and
refuses any path that resolves inside the repository. The isolated build rejects
all CLI arguments; its output can be customized only through the validated
`SYMGOV_BUILD_OUT_DIR` environment contract.

Current known stale fixtures must be repaired in Goal F0.1 rather than accepted as product failures.

### 1.4 Commit and session discipline

- One goal per branch/commit series unless a spec explicitly combines inseparable slices.
- Preserve unrelated dirty work and stage exact paths only.
- Do not push, deploy, migrate production, restart services, publish symbols, or change public gateways without explicit authorization.
- Close each goal with: changed files, test evidence, residual risks, migration/deployment state, and next backlog ID.
- Start a fresh session at every marked checkpoint or earlier if context becomes crowded.

---

## 2. Corrected assumptions from the shared proposal

| Proposal assumption | Code-backed correction |
|---|---|
| Identity, roles and Free/Plus are foundational work still to build | Individual PIN authentication, HTTP-only sessions, additive global roles, Free/Plus subscriptions, expiry reconciliation, protected owner rules, profile self-service and admin management already exist. Extend them; do not rebuild them. |
| Plus could grant or imply submitter/reviewer capability | Existing design correctly separates Plus from roles. Plus is necessary for privileged roles but self-service upgrade grants none. Capability requests are absent and must map to explicit role grants only after approval. |
| Customer/organization/trial membership already exists | There is no organization, tenant, membership or invitation domain. The confirmed commercial model is currently individual £50/year Plus. Do not introduce organizations without a separate product decision and real use case. |
| Reviewer disciplines can be added as a profile filter | Review items contain discipline data, but reviewers have no qualifications/discipline assignments and the backend returns the full review queue. This is a new authorization domain, not just UI filtering. |
| The proposed long symbol lifecycle describes current state | Revision lifecycle is only `draft/review/approved/published/deprecated`; review cases and split items use separate string states. Introduce transition services incrementally rather than replacing every state in one migration. |
| Human reviewer identity is trustworthy | Decision requests accept client-supplied reviewer name/role and often persist no actor. Session identity must become authoritative before external review. |
| Requesting review is harmless to publication | The current published-feedback path can move a published revision to `review`, removing it from the published read model before a governance withdrawal decision. Review and withdrawal must be separated. |
| BTX output existence proves conversion quality | The converter safely produces multiple derivatives but silently ignores some operators/features. Vlad may mark emitted output as passed. Unsupported or lossy conversions need release-blocking warnings and quarantine. |
| Libby's confidence threshold is meaningful | Classification and evidence records exist, but there is no labelled benchmark or calibration proving the `0.82` threshold. It must not authorize autonomous publication. |
| Agent notifications form a notification service | Existing agent status notifications are direct wrappers with partial runner coverage. Only subscription email has a transactional outbox. A general durable notification domain is still absent. |
| `/support` is a working support service | The main support page stores local React state only. Published/Catalog feedback is durable, but general cases, threads, ownership and status are absent. |
| `/health` proves readiness | Current health is shallow liveness. It does not prove DB, migration, object storage, workers, queues, email backlog or external dependencies. |
| Existing backup files prove recoverability | No scheduled, retained, verified database/object-store backup and restore drill is established. |
| All named agents are consistently routed | The manifest directly binds Telegram to Libby while architecture/README say Alfi should remain orchestrator. Resolve the drift before trial. |
| Full autonomous publication is a trial goal | It is not. For the first trial, agents may recommend and prepare evidence, but a human must approve publication and withdrawal. Automated no-human publication is explicitly deferred. |

---

## 3. Phased backlog

Status vocabulary: `SPECIFIED` means a controlling implementation-ready goal specification exists; `COMPLETE` means implemented and verified at an immutable commit; `DEPLOYED` additionally means the reviewed commit is active in production; `READY` means sufficiently understood to write or execute the goal spec; `DECISION` needs product confirmation inside the spec; `DEFERRED` is intentionally outside the initial trial. `SECURITY BLOCKER`, `GOVERNANCE BLOCKER`, `LIVE GOVERNANCE BLOCKER`, and `TRIAL BLOCKER` identify the risk boundary that prevents dependent work or release until the item is complete. The completion records below, rather than the original plan-creation wording, are authoritative.

## Phase F0 — Establish a trustworthy baseline

### F0.1 Repair and codify the test baseline — COMPLETE

**Purpose:** Make future red/green evidence trustworthy and fast enough to run per goal.

**Scope:**
- repair stale service-user fixtures in `tests/test_published_feedback_service.py`, `tests/test_published_symbol_review_workflow.py` and the three send-for-review tests reported by audit;
- add the missing test-only `httpx2` dependency through a documented development/test dependency mechanism rather than ad-hoc command knowledge;
- add package scripts or documented commands for backend, frontend Node, Langfuse POC and isolated frontend build partitions;
- record expected suite partitions without baking a transient test count into policy.

**Likely files:** `backend/requirements*.txt` or a new test requirements file, `package.json`, affected test fixtures, `README.md`, `backend/README.md`.

**Acceptance:** all previously stale focused tests pass; each partition can be run independently; no production dependency is added solely for tests unless Starlette requires it at runtime.

**Completion:** Commit `972f2b89ff6a534b6daa0572df644b041a770779`. Final evidence in `docs/plans/2026-07-26-f0-1-restart-note.md`: 784 backend nodes across full partitions, 65 frontend tests, 12 Langfuse tests, 9 focused email-outbox tests, two isolated Vite builds, and verification-wrapper timeout/path/partition/argument contracts passed. Fresh Stage 1 passed and Stage 2 approved. Product behavior changed: no; no migration or dedicated runtime activation was required.

### F0.2 Split workspace authorization by operation — COMPLETE, DEPLOYED

**Purpose:** Prevent a generic reviewer from accessing agent controls, scans, source configuration, worker health and operational queues.

**Rules:** Create a complete route-policy inventory for every workspace operation under both `/api/v1/workspace` and the legacy `/api/workspace` surface. Review/rights/property/preview endpoints may permit `reviewer|admin`; all unclassified, new, agent-operational and control routes default to `admin`. Frontend hiding is not a security control.

**Likely files:** `backend/symgov_backend/app.py`, `backend/symgov_backend/routes/workspace.py`, `backend/symgov_backend/dependencies.py`, `tests/test_route_auth_enforcement.py`, `tests/test_auth_dependencies.py`.

**Acceptance:** an automated route-policy matrix enumerates every current workspace route and method on both prefixes; reviewer access is allowlisted, all other routes return 403, new/unclassified routes fail closed to admin, legacy and v1 policies match, and admin behaviour remains intact.

**Completion:** Commit `63edef801e45768ac3a402a44f6941f490226c58`. Goal-local evidence in `docs/plans/2026-07-27-f0-2-workspace-authorization-spec.md`: 28 normalized operations/49 concrete routes; 186 focused authorization tests; 20 workspace regressions; portable backend 933 passed/3 deselected; Stage 1 `t_8c833387` PASS; Stage 2 `t_b02100b2` APPROVED. Broader pre-production gates passed: 961 backend, 65 frontend and 12 Langfuse tests plus isolated build and wrapper contracts. The commit was pushed and deployed on 2026-07-27; live health returned 200, reviewer/admin v1 and legacy boundaries returned the expected 200/403 outcomes, anonymous probes returned 401, temporary records were removed, and no migration was required.

### F0.3 Make review and publication attribution session-authoritative — COMPLETE, DEPLOYED

**Purpose:** Ensure decisions cannot impersonate another reviewer and publication records point to the actual human approver.

**Rules:** derive actor ID, display name and effective role from `AuthenticatedUser`; do not trust editable `deciderName`/`deciderRole`; persist actor into review decisions, audit events and human-approved publication jobs.

**Likely files:** `backend/symgov_backend/schemas.py`, `routes/workspace.py`, `publication_handoff.py`, `runtime.py`, `frontend/src/App.jsx`, review/publication tests.

**Acceptance:** spoofed identity input is rejected or ignored; persisted actor matches the session; historical response snapshots remain human-readable.

**Controlling spec and handoff:** `docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md` and `docs/plans/2026-07-27-f0-3-restart-handoff.md`. Execution used serialized Cody implementation, fresh immutable Stage 1, fresh immutable Stage 2 and final verification/local commit. No push or deployment was authorized by the implementation handoff.

**Historical release boundary:** the implementation checkpoint required a later authorized paused atomic backend/frontend/repository-owned-runner release. That point-in-time requirement is preserved in the controlling spec and restart records.

**Completion and deployment:** Final local checkpoint task `t_ae5e0550` closed F0.3 on 2026-07-27 at commit `c7833c8ba19c0c19c1cc7c5267303d324964d39b` (`feat: enforce session-authoritative attribution`) from baseline `63edef801e45768ac3a402a44f6941f490226c58`. The final immutable implementation snapshot passed Stage 1 `t_bcd01d5d` and was approved by Stage 2 `t_af9698b2`; both reviews recorded the same 21 per-path SHA-256 values and unchanged staged/unstaged/HEAD-to-worktree patch identities. Fresh final gates passed: exact 12-file `py_compile`; 279 focused tests including both repository-runner import boundaries; portable backend 1,002 passed/3 deselected; frontend 67 passed; isolated and canonical Vite builds each transformed 54 modules; verification-wrapper contracts; tracked and all four untracked whitespace checks. No migration or historical backfill was added at the checkpoint. The historical spec/handoff correctly record that no release occurred in that implementation session. Current read-only evidence in `docs/plans/2026-07-28-f0-3-deployment-addendum.md` establishes that `main`, `origin/main`, the immutable production backend/frontend release and repository-owned runner are now at `45fc6e00b1372fce1e092ebe282f264ccd401cb3`, containing F0.3 plus the verified Rupert durable-queue flush-order correction; both public root and health returned HTTP 200.

### F0.4 Separate review requests from publication withdrawal — SPECIFIED, LIVE GOVERNANCE BLOCKER

**Purpose:** Stop feedback or a review request from silently removing a currently published symbol from the public catalogue.

**Rules:** Feedback opens clarification/review work while the current revision remains published; only an authenticated, authorized human withdrawal decision may change public availability; record the actual requester and actor rather than attributing the action to Ed.

**Code-backed evidence:** `backend/symgov_backend/services/published_feedback.py:194-196` locks the selected published revision and changes it to `review`; `backend/symgov_backend/published_catalog.py:30-40` excludes that revision immediately. `backend/symgov_backend/routes/published.py:390-443` runs behind a mount-level session guard but does not inject its `AuthenticatedUser`, instead persisting Ed as browser submitter/audit actor/workflow owner. Catalog API-key attribution and scope enforcement are already authoritative at `backend/symgov_backend/routes/catalog.py:704-754`, but the APIs have no caller-stable request ID, so a transport retry is indistinguishable from intentional new feedback and duplicates intake/actions/queues/files; Catalog also returns `mutatesPublishedState: true` (`tests/test_catalog_feedback.py:443-479`). Runtime files are exposed before DB commit, Catalog auth commits `last_used_at` before payload/symbol validity is known, and symbol-level routing can silently choose the first of several publication placements. Existing tests explicitly preserve these defects and must be inverted/extended.

**Controlling specification:** `docs/plans/2026-07-28-f0-4-review-without-unpublication-spec.md`. It separates publication state, clarification/review workflow and future withdrawal/replacement; defines session/API-key requester attribution, Ed executor attribution, authorization, an indexed deterministic request-level audit anchor plus exact per-symbol identities, schema-valid canonical-page anchoring with a complete multi-placement snapshot, database-first runtime handoff limits, exact browser 200/202 and Catalog 201/202/usage-telemetry contracts, a repository-owned fail-closed intake/Ed-claim pause marker with mandatory pre-F0.4 API/worker shutdown-and-drain before backend-first activation and safe rollback rules, no-migration/historical-data treatment, UI behavior, exact tests and a serialized fresh Stage 1/Stage 2 chain.

**Acceptance:** every feedback/review-request route preserves the published read model, creates the expected exactly-once durable workflow under same-key replay (including disjoint-target conflicts), records the authenticated or API-key requester at request-anchor and symbol level, preserves valid open-case stage/ownership, returns the exact bounded wire contract, and has negative tests proving no implicit lifecycle change, invalid-request side effect or mixed-version activation window. This closes before any new reviewer, capability or submission feature work.

### F0.5 Enforce account security invariants in the backend — READY

**Slices:**
1. enforce `must_change_pin` in backend dependencies while allowing only auth/profile/PIN-change essentials;
2. reject current-PIN reuse;
3. revoke sessions on deactivation and PIN reset;
4. add bounded per-account and per-IP login throttling plus attempt audit;
5. define and apply one consistent Origin/Referer/JSON CSRF policy for cookie-authenticated mutations.

**Acceptance:** direct API calls cannot bypass forced PIN change; reactivated accounts do not regain old sessions; throttling has deterministic tests and safe operator recovery.

### F0.6 Resolve agent manifest and Telegram routing drift — READY

**Purpose:** Ensure Alfi remains the single Telegram orchestrator unless Chris explicitly approves another routing model.

**Likely files:** `openclaw-agents.manifest.json`, management/bootstrap tests, README/architecture docs.

**Acceptance:** one authoritative policy, a manifest-policy regression test, and no unapproved direct Libby Telegram binding. Changing a live gateway remains separately authorized.

**Phase F0 exit:** F0.1–F0.6 are committed at immutable local checkpoints; the complete route-policy matrix, actor-attribution tests and published-read-model regression pass; baseline partitions are green or have explicit blocking defects; agent routing policy is reconciled.

**Checkpoint CP0:** Create a concrete restart note containing the branch, commit IDs, exact clean/dirty paths, exact passing commands, unresolved failures, approved execution lane and next spec path before starting F1.

---

## Phase F1 — Reviewer scope, capabilities and auditable access

### F1.1 Specify the controlled discipline vocabulary — READY

**Purpose:** Establish the authoritative values shared by Libby classification, reviewer eligibility, review queues, notifications and analytics.

**Rules to decide in spec:** canonical IDs vs display labels; aliases; unknown/unclassified handling; multi-discipline symbols; rights-only cases; retirement/versioning; whether discipline is single or multiple on the current governed symbol.

**Evidence:** current disciplines are free text across `GovernedSymbol`, classification and review payloads.

**Acceptance:** versioned vocabulary and normalization service with fixture coverage; no reviewer assignment work starts until this contract is approved.

### F1.2 Add admin-managed reviewer discipline assignments — READY

**Purpose:** Record which disciplines a reviewer is authorized to review.

**Rules:** only active Plus users with reviewer role may have assignments; admin manages assignments for the first trial; reviewers may view but not self-assign; avoid an `ALL` magic value—represent broad permission explicitly and audit it; assignment changes do not silently re-authorize completed decisions.

**Likely files:** new Alembic migration, `models/schema.py`, admin schemas/routes, admin UI, auth/admin tests.

**Acceptance:** assignment CRUD is backend-enforced and audited; invalid/retired discipline is rejected; role/subscription loss makes assignments ineffective without deleting history.

### F1.3 Enforce discipline eligibility on review routes — READY, TRIAL BLOCKER

**Purpose:** Ensure reviewers can retrieve and act only on eligible review cases.

**Rules:** enforce on list, detail, asset, property and decision routes; admin may inspect all; define explicit treatment for unclassified and rights cases; multi-discipline matching is deterministic; add pagination and bounded filters.

**Acceptance:** no altered URL or direct API call exposes an out-of-scope case; matrix covers one, multiple, broad, unknown and mid-review assignment changes.

### F1.4 Add submitter/reviewer capability requests — READY

**Purpose:** Let a Plus user request, but not self-grant, submitter or reviewer access.

**Minimal states:** `pending`, `approved`, `rejected`, `withdrawn`; suspension remains an account/role action rather than a request state.

**Rules:** one active request per capability; reviewer request includes proposed disciplines and evidence/justification; admin decides; approval and exact role/discipline grant occur transactionally; every transition is audited and notified.

**Likely files:** migration/models, profile/admin routes and schemas, `ProfilePage.jsx`, admin UI, outbox integration, tests.

**Acceptance:** Free users cannot request; Plus users cannot grant themselves access; repeated requests are idempotent; rejection/withdrawal leaves roles unchanged.

### F1.5 Add account and access audit read surfaces — READY

**Purpose:** Make Reggie/admin able to answer who changed an account, subscription, role, discipline or capability and why.

**Scope:** audit user creation, activation/deactivation, role diffs, PIN reset, session revocation, discipline changes and capability transitions; add paginated admin read API with actor/entity/action/time filters.

**Acceptance:** mutation and audit commit together; actor is never client-supplied; deletion policy preserves governance history.

### F1.6 Organization accounts — DEFERRED

The trial will use individual accounts and a named user cohort. Do not introduce organizations, memberships, seats or tenant authorization unless Chris approves a separate product spec demonstrating the need. Never reinterpret `CatalogApiKey.customer_name` as tenancy.

**Phase F1 exit / Checkpoint CP1:** Discipline vocabulary is approved; assignment, queue/detail/action authorization and capability-request/audit journeys pass end to end. Commit a concrete restart note with route matrices, migration state, exact commands/results, dirty paths, branch/commits, lane and the F2.1 spec path.

---

## Phase F2 — Submission, lifecycle and publication safety

### F2.1 Add durable submission batches and submitter status — READY

**Purpose:** Let submitters see what was received, processing state, failures and final outcome.

**States:** `received`, `processing`, `needs_information`, `accepted`, `rejected`, `withdrawn`.

**Rules:** authenticated owner only; idempotency key; bounded file count/size/type; attachment lineage; compensating cleanup after partial persistence failure; pre-review withdrawal only; published-content withdrawal is separate.

**Likely files:** migration/models, `services/external_submissions.py`, public/submission routes, frontend submission/status pages, tests.

### F2.2a Add revision transition service — READY

**Purpose:** Centralize allowed revision transitions, actor, prerequisites and audit without a big-bang lifecycle migration.

**Acceptance:** newly touched revision mutations use the service; invalid transitions fail before side effects; existing states remain readable.

### F2.2b Add review-case transition service — READY

**Acceptance:** review stage changes use one constrained service with actor, prerequisites, audit, notification intent and idempotency.

### F2.2c Add split-item transition service — READY

**Acceptance:** split-child state groups are enforced outside route-local string assignments and invalid parent/child combinations fail.

### F2.2d Add publication-job transition service — READY

**Acceptance:** job claim, approval, execution, failure and completion transitions are constrained and recoverable; later F2.4 builds reliable cross-process execution on this service.

### F2.3 Add governed withdrawal, replacement and republishing — READY, TRIAL BLOCKER

**Purpose:** Make incorrect published symbols reversibly removable under human control.

**Required data:** actor, reason, effective time, replacement/superseding revision, impacted pages/packs and audit event.

**Acceptance:** publish → review request leaves publication intact; publish → authorized withdrawal removes it; withdrawal → corrected republish works; read model refresh is verified; unauthorized agent actions fail.

### F2.4 Make publication handoff idempotent and recoverable — READY

**Purpose:** Remove split-brain risk from synchronous cross-process Rupert execution.

**Approach:** transactional publication outbox or durable job claim, deterministic handoff ID, idempotent worker, explicit attempt/error state, reconciliation when Rupert commits but API follow-up fails.

**Acceptance:** retries return the same publication job and cannot duplicate pages/pack entries; crash points have recovery tests.

### F2.5 Preserve human authority for trial publication and licensing conflicts — READY

**Purpose:** Codify that agents prepare evidence and recommendations but cannot autonomously publish, withdraw or clear rights/licensing conflicts during the initial trial.

**Acceptance:** automation policy and route/handoff tests prove authenticated human approval is required; unresolved Tracy rights/licensing conflicts block publication; agents cannot record the conflict-resolution decision; the durable human decision includes actor, rationale and evidence; a publication pause blocks handoff without corrupting queued work.

**Phase F2 exit / Checkpoint CP2:** The submit → process → review → human/licensing-approved publish → feedback-without-unpublication → withdraw → corrected republish simulation passes, including idempotency/crash recovery. Commit a concrete restart note with exact evidence and the F3.1 spec path.

---

## Phase F3 — BTX, Vlad and symbol-quality evidence

### F3.1 Define a versioned BTX reference corpus — READY

**Purpose:** Establish representative valid, lossy, unsupported and historically broken BTX cases across converter versions.

**Corpus:** paths, fills, strokes, text, images/XObjects, clipping, transforms, transparency/graphics state, rotations, unusual bounds/scales, partial failures and multi-symbol files. Store originals, expected warnings and approved rendered references where licensing permits.

### F3.2 Add BTX conversion outcome and quarantine policy — READY, TRIAL BLOCKER

**Outcomes:** `converted`, `converted_with_warnings`, `unsupported`, `failed`, `quarantined`.

**Rules:** ignored or unsupported operators that can alter meaning are release-blocking; originals are immutable; quarantine blocks review/publication handoff; only authorized human release with rationale can override a warning class permitted by policy.

**Likely files:** `services/btx_converter.py`, Vlad runner, validation models/migration, handoff guards, BTX tests.

### F3.3 Add repeatable render/geometry regression checks — READY

**Purpose:** Detect converter changes that lose or materially alter symbol content.

**Checks:** entity/operator counts, non-empty bounds, scale/clipping anomalies, raster/vector perceptual comparison where meaningful, deterministic hashes for stable artifacts, converter/policy version comparison.

**Acceptance:** known lossy fixtures fail or quarantine; valid corpus remains within approved thresholds; report explains each warning.

### F3.4 Make Vlad processing records reproducible and portable — READY

**Scope:** persist validator/converter/tool/policy versions, normalized operations, hashes, measurements and warnings; replace durable host-local paths with object keys or logical references; distinguish measured confidence from fixed prose confidence.

**Acceptance:** an operator can reconstruct what ran without access to the original host path; unsupported BTX features influence final validation outcome.

**Phase F3 exit / Checkpoint CP3:** The versioned golden corpus is reproducible; known lossy/unsupported BTX quarantines; Vlad evidence is portable; quarantine blocks handoff. Commit a concrete restart note with corpus/policy versions, exact results and the F4.1 spec path.

---

## Phase F4 — Complete user journeys and durable communication

### F4.1 Public product and Free/Plus explanation — READY

**Purpose:** Explain the symbol-reference/governance service, £50/year Plus model, trial limitations and role-request separation without overlapping Idox drawing-management products.

**Scope:** public landing content and plan comparison. Payment collection remains out of scope until separately specified.

### F4.2 Controlled registration and onboarding — DECISION

For the first trial, prefer invitation or admin-approved registration rather than unrestricted public sign-up. The spec must decide email verification, account creation, initial PIN/password mechanism, forced change, consent and expiry. Password reset/recovery must not be assumed to exist merely because login exists.

### F4.3 Complete capability/profile/admin journeys — READY

Build the UI over F1.2–F1.4: request submitter/reviewer access, propose reviewer disciplines, view status, withdraw pending request, admin decision with rationale, and user-visible effective roles/assignments.

### F4.4 Generalize the transactional notification service — READY

**Purpose:** Reuse outbox principles beyond subscription email.

**Slices:**
1. notification event and delivery-attempt models;
2. safe row claiming/lease or `SKIP LOCKED`;
3. bounded retry, bounce recording and dead-letter/terminal failure;
4. template and policy versioning;
5. recipient/channel policy, optional-email preferences and mandatory-event overrides;
6. duplicate suppression, per-recipient/event rate limiting and safe non-delivery test mode;
7. digest scheduling for eligible non-urgent events;
8. admin backlog visibility, delivery evidence and audited replay controls.

**Rules:** domain mutation and event enqueue are atomic; sending is asynchronous; no secrets/errors leak; AgentMail idempotency is retained; SMTP cannot claim provider idempotency it lacks; security, entitlement and governance messages cannot be disabled as optional mail.

**Acceptance:** concurrent workers do not double-claim; every event exposes queued/sent/bounced/retrying/dead-letter state without leaking provider bodies; safe test mode proves recipient selection without contacting real users; optional preferences and rate limits are enforced; replay is idempotent and audited.

### F4.5 Add trial-critical notification events — READY, INVITATION BLOCKER

**Order:** capability request received/decided, submission received/status/outcome, review work available, review outcome, publication/withdrawal, support acknowledgement/update, high-severity incident. Reviewer notifications use the same server-side discipline eligibility service as queues.

**Acceptance:** given a symbol/review event, Symgov records exactly which reviewers matched, the discipline/broad-access rule that matched each one, exclusions and duplicate suppression; urgent review mail links to the authenticated, server-filtered queue; eligible routine work can be digested; failed/bounced delivery is visible and retryable; incident-alert delivery and escalation to Alfi are exercised end to end before invitations.

### F4.6 Make Ed support intake durable — READY

**Purpose:** Replace local `/support` state with persisted requests, messages, status, category, severity, owner and timestamps.

**Rules:** authenticated requester sees own history; admin/Ed sees queue; product guidance may be deterministic/LLM-assisted but cannot change subscriptions or governance decisions; widespread issues can raise an incident candidate for human confirmation.

**Acceptance:** refresh retains the case; requester receives acknowledgement; status/escalation is audited; existing published/Catalog feedback links rather than duplicates cases.

**Phase F4 exit / Checkpoint CP4:** Invitation/onboarding, capability journeys, durable notification matching/delivery evidence and support survive browser refresh and backend restart; invitation-blocking mail tests pass. Commit a concrete restart note with exact results and the F5.1 spec path.

---

## Phase F5 — Measured intelligence, not autonomous authority

### F5.1 Create a versioned Libby benchmark — READY

**Purpose:** Measure category, discipline, family/name, aliases/keywords and abstention on labelled representative data.

**Outputs:** per-field accuracy/F1, confusion matrices, abstention rate, calibration error and failure categories, tied to dataset/taxonomy/prompt/model versions.

### F5.2 Replace unvalidated confidence with policy evidence — READY

The current `0.82` threshold may continue only as a descriptive heuristic until benchmarked. Store policy version and evidence; low/ambiguous cases require human confirmation. No classification score may directly authorize publication in the first trial.

### F5.3 Make corrections reusable — READY

Record reviewer correction against the original proposal and versions, provide a curated export for future evaluation, and prevent taxonomy changes from silently rewriting historical decisions.

### F5.4 Structured agent evidence bundle — READY

Define one backend-owned evidence contract combining Scott lineage, Tracy rights/provenance, Vlad validation, Libby classification, warnings/exceptions and versions for Daisy/human review/Rupert. Missing or stale evidence blocks handoff.

### F5.5 No-human-review publication — DEFERRED

Do not implement autonomous Rupert handoff for the initial external trial. Revisit only after benchmark, quality, audit, withdrawal, pause and incident controls are proven and Chris explicitly authorizes a separate governance spec.

**Phase F5 exit / Checkpoint CP5:** Libby benchmark reports are repeatable and versioned; confidence policy is evidence-based; corrections are exportable; human review consumes a complete versioned evidence bundle. Commit a concrete restart note with benchmark versions/results and the F6.1 spec path.

---

## Phase F6 — Operations, resilience and controlled trial

### F6.1 Separate liveness from readiness — READY

Add `/health/live` for process liveness and protected/operator `/health/ready` checks for DB, migration head, object storage, worker freshness, publication/email backlog and required integrations. Add external website/API uptime probes. Each dependency failure must be attributable and must not expose secrets.

### F6.2 Add host and workflow monitoring — READY

Monitor disk, CPU, memory, database reachability, backup freshness, failed-login rate, conversion failures, agent execution failures, queue age, symbols stuck by lifecycle stage, reviewer coverage gaps, review duration and unexplained publication-volume change. Define thresholds, collection intervals, missing-data behaviour and alert ownership.

**Acceptance:** fixture/failure injection proves each critical signal can open or update an incident; stale monitoring itself alerts; website/API outage and host resource thresholds are exercised.

### F6.3 Add persisted incidents and severity policy — READY

**Severity:** critical, high, medium, low with deterministic triggers, human acknowledgement, owner, status, timeline and resolution. Critical events can pause only the affected automation. Alerts summarize; they do not dump raw logs into Alfi.

**Acceptance:** alert delivery, acknowledgement timeout and escalation to Alfi are tested end to end, including delivery failure and duplicate suppression.

### F6.4 Generate Alfi operational summaries from deterministic data — READY

Daily/weekly summaries should report service readiness, host capacity, failed logins, stuck queues, review coverage gaps, quarantine, email failures, incidents, backup freshness and trial measures. LLM use is for concise explanation, not inventing status.

### F6.5 Add safe agent recovery controls — READY

Persist/deduplicate Reggie findings; add audited operator actions for stale lease recovery, requeue and runtime/DB reconciliation; require dry-run/precondition checks; never let an LLM directly rewrite terminal state.

### F6.6 Add human pause controls for every automated workflow — READY, TRIAL BLOCKER

Define backend-owned pause state and authorization for intake, validation/conversion, provenance, classification, review coordination, publication, notifications and other scheduled agent work, plus a first-class emergency **global pause**. Global pause is one atomic authoritative state that overrides every workflow-specific state; setting or clearing it requires an authenticated authorized human, reason and audit event. Workflow pauses remain independently addressable beneath it. Pausing stops new claims while preserving durable queued/in-flight evidence; workers finish or checkpoint in-flight work according to a documented per-worker rule. Resumption is explicit and audited. Critical incidents may recommend or invoke only policy-approved scoped pauses; only the authorized human path may set/clear global pause.

**Acceptance:** each worker/runner checks global pause before workflow pause and before claiming work; atomic set/clear, precedence and read-after-write behavior are tested; route and worker tests prove agents and unauthorized users cannot clear either state; scoped pause does not affect unrelated workflows; global pause stops all new claims; pause/resume and crash-during-pause retain work without duplicate execution.

### F6.7 Prove backup, restore and disaster recovery — READY, TRIAL BLOCKER

Define RPO/RTO, scheduled PostgreSQL and object-storage backups, retention/encryption/access, manifest/checksum validation and a disposable restore drill. Write and execute a versioned disaster-recovery runbook covering loss of DB, object storage, application host and credentials without recording secrets. Evidence must include database restore, Alembic head, object presence, invariant queries, application readiness and measured recovery time. Existing one-off backup files do not satisfy this goal.

### F6.8 Add the security, privacy and abuse release gate — READY, TRIAL BLOCKER

**Scope:** repository and runtime secret scanning; dependency/vulnerability scanning with severity/exception policy; secure external secret storage and rotation runbook; upload type/size/archive/path restrictions plus malware/content-scanning or a documented quarantine-safe alternative; API/login/upload/support/notification abuse-rate controls; security headers/cookie checks; audit completeness for agent and human governance actions; privacy/retention verification.

**Acceptance:** CI/release commands produce reviewable reports; no unresolved critical/high finding without an explicit human exception; malicious/path-traversal/archive-bomb fixtures quarantine safely; rate-limit matrices cover authentication and costly/mutating endpoints; every agent decision influencing governance is attributable and explainable.

### F6.9 Add user-level controlled-trial cohort — READY

**Commercial boundary:** the trial uses individual £50/year Plus accounts, with manual/admin trial entitlement and no payment collection. Organizations, seats and tenant billing remain deferred.

**Scope:** named cohort, invitation/enrollment state, consent/version, start/end, Free/Plus test assignment, feature flags/kill switches, onboarding/offboarding and participant contact policy. Organizations remain out of scope.

### F6.10 Add privacy-safe trial analytics and decision thresholds — READY

Allowlisted events: invitation, activation, first successful lookup/submission, review request, feedback, repeat use, capability request, conversion, support burden, LLM cost and satisfaction. Provide aggregate export/dashboard and deletion/retention policy. Catalog API-key usage alone is insufficient; either implement the declared `catalog.usage.read` endpoint under its scope or remove/correct the declaration.

Before invitations, Chris must approve measurable success, stop and expansion criteria: minimum activation/task-success/return-use targets, maximum critical incident/support/quality-failure thresholds, cost ceiling, trial review dates, immediate-stop conditions and the gate for expanding beyond the initial cohort.

### F6.11 Publish trial policy and participant documentation — DECISION

Requires Chris-approved privacy notice, trial terms, provenance/IP declaration, known limitations, support expectations, incident communication, contact permission and ending/conversion process.

### F6.12 Run the trial rehearsal and go/no-go gate — READY

Use seeded users across Free, Plus, submitter, discipline-limited reviewer and admin roles; use several disciplines and BTX outcomes. Exercise registration/invitation, capability approval, submission, upload scanning/quarantine, processing, review, licensing-conflict block, publication, feedback without unpublication, withdrawal/replacement, notification match evidence/failure/bounce/retry, support, alert delivery, scoped and global human pauses, dependency scan, secrets scan, incident escalation, backup/DR restore and participant offboarding.

**Go/no-go:** every blocker in this plan is closed; no known critical conversion defect in the trial corpus; complete backend authorization and abuse-rate matrices pass; all agent/human governance actions are attributable; unresolved licensing conflicts block publication; publication/withdrawal is reversible and human-controlled; every automated workflow can be paused; notification matching and failures are explainable/recoverable; support and incidents are durable; external/host monitoring and Alfi escalation are proven; security/privacy scans meet policy; restore/DR drill is current; legal/trial documents and measurable success/stop/expansion criteria are approved.

**Phase F6 exit / Checkpoint CP6:** Record branch and immutable commits, all exact gate commands/results, approved thresholds, known limitations and CEO decision. External invitations and later cohort expansion each require explicit CEO authorization after their recorded gate.

---

## 4. Recommended execution order

Do not execute the original ChatGPT phases literally. Use this dependency order:

1. F0.1 test baseline.
2. F0.2 complete workspace authorization matrix.
3. F0.3–F0.4 trusted attribution and immediate review-without-unpublication correction.
4. F0.5–F0.6 account hardening and agent routing.
5. F1.1–F1.3 discipline vocabulary, assignments and secure filtering.
6. F1.4–F1.5 capability requests and audit reader.
7. F2.1 and F2.2a–F2.2d submission/status and separately closable transition services.
8. F2.3–F2.5 withdrawal and reliable human/licensing-controlled publication.
9. F3.1–F3.4 BTX/Vlad corpus, quarantine and reproducible evidence.
10. F4.1–F4.6 product/onboarding, complete access journeys, durable notifications and support.
11. F5.1–F5.4 measured Libby intelligence and evidence bundle.
12. F6.1–F6.8 readiness, host/workflow monitoring, incidents, reporting, recovery, pause, DR and security release gate.
13. F6.9–F6.12 cohort, analytics/thresholds, documents, rehearsal and go/no-go.

This order deliberately places authorization, actor identity, conversion quarantine, withdrawal and restore ahead of growth or autonomous operation.

---

## 5. Session handoff protocol

A checkpoint is not complete until a concrete restart note is saved under `docs/plans/` and committed. Generic placeholders are not a handoff. The restart note must record:

```text
Backlog goal and exact goal-spec path
Approved execution lane: durable Kanban/Cody or explicitly approved direct lane
Branch, baseline commit and controlling-plan commit
Exact preserved dirty/untracked files and owner/purpose
Implemented observable behaviours
Exact verification commands, results and durations
Migration/runtime/deployment state
Residual risks and unresolved failures
Next backlog goal and exact next-spec path
```

The corresponding fresh-session prompt must repeat those concrete values, not `<ID>` placeholders. Each CP0–CP6 note is an immutable phase record; if later work changes the facts, create a newer note rather than rewriting historical evidence.

### Current completed checkpoint: F0.3

F0.1, F0.2 and F0.3 are complete. F0.3 is pushed and deployed at production release `45fc6e00b1372fce1e092ebe282f264ccd401cb3`, which contains implementation commit `c7833c8ba19c0c19c1cc7c5267303d324964d39b` and the verified Rupert durable-queue flush-order correction. The current deployment evidence is in `docs/plans/2026-07-28-f0-3-deployment-addendum.md`; older “not deployed” statements remain valid historical checkpoint records.

The controlling F0.3 goal spec is:

`docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md`

The concrete restart handoff is `docs/plans/2026-07-27-f0-3-restart-handoff.md`. It records the historical baseline/deployment state, completed evidence, exact scope, review replacement history, fresh final gates, preserved safety boundaries and the then-pending atomic release requirement. The implementation baseline was clean `main`/`origin/main` at `63edef801e45768ac3a402a44f6941f490226c58`; final task `t_ae5e0550` created local commit `c7833c8ba19c0c19c1cc7c5267303d324964d39b`. The post-checkpoint record is `docs/plans/2026-07-27-f0-3-post-checkpoint-and-remaining-stages.md`; the newer deployment state is recorded separately in `docs/plans/2026-07-28-f0-3-deployment-addendum.md` rather than rewriting either historical record.

### Template for future goal-spec creation

Use this only to author the next concrete spec; do not present it as a runnable handoff:

```text
Inspect the current repository and the Symgov Trial Readiness master backlog before
trusting old assumptions. Write the implementation-ready spec for the next recorded
goal only, with exact files, exclusions, state/authorization/audit rules, acceptance
criteria, verification commands, migration/rollback notes, approved execution lane
and a concrete restart prompt. Correct the master backlog if code or product decisions
have changed. Do not implement code in the planning session.
```

---

## 6. Immediate next action

The next backlog goal is **F0.4 — Separate review requests from publication withdrawal**. Its implementation-ready controlling specification is `docs/plans/2026-07-28-f0-4-review-without-unpublication-spec.md`. This planning session authorizes no F0.4 source implementation; use its serialized Cody implementation, fresh immutable Stage 1, fresh immutable Stage 2 and final-verification chain in a later session. F0.3 is already deployed as recorded in the separate 2026-07-28 addendum.

For a concise current checkpoint, the separate F0.3 release track, and the remaining F0–F6 programme stages, read `docs/plans/2026-07-27-f0-3-post-checkpoint-and-remaining-stages.md`.
