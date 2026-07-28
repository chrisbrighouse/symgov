# F0.3 Post-Checkpoint Record and Remaining Trial-Readiness Stages

Date: 2026-07-27

## Purpose

This note records the exact Git identity produced after the F0.3 specification, implementation, correction, immutable review and final-verification records were committed together. It supplements, rather than rewrites, the point-in-time evidence in:

- `docs/plans/2026-07-27-f0-3-session-authoritative-attribution-spec.md`;
- `docs/plans/2026-07-27-f0-3-restart-handoff.md`;
- `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`.

## Exact completed checkpoint

- Repository: `/data/symgov`.
- Branch: `main`.
- Implementation baseline and current `origin/main`: `63edef801e45768ac3a402a44f6941f490226c58`.
- F0.3 local implementation commit: `c7833c8ba19c0c19c1cc7c5267303d324964d39b` (`feat: enforce session-authoritative attribution`).
- Remote relation after checkpoint: local `main` is exactly one commit ahead of `origin/main`.
- Worktree after checkpoint: clean.
- Final Stage 1: `t_bcd01d5d` — PASS, no actionable findings.
- Final Stage 2: `t_af9698b2` — APPROVED, no Critical, Important, security, quality or specification findings.
- Final verification/local commit: `t_ae5e0550` — complete.

Fresh final evidence recorded by the final task:

- exact focused F0.3 gate: 279 passed;
- portable backend: 1,002 passed, 3 deselected;
- frontend: 67 passed;
- isolated and canonical frontend builds: 54 modules transformed in each;
- Python compilation, verification-wrapper contracts and whitespace checks: passed;
- post-commit focused tests and frontend build: passed.

No push, production deployment, migration, service restart, database mutation, publication, withdrawal or external message occurred.

## Two separate next tracks

### Track A — separately authorized F0.3 release

F0.3 is complete locally but not available in production. A release is not implicit in this documentation update and still requires Chris's explicit authorization.

The release must follow the controlling specification's paused atomic sequence:

1. inspect pending/running human-approved Rupert work;
2. pause all four review mutations, publication handoffs and new Rupert claims;
3. verify no publication execution remains in flight;
4. activate one immutable release containing backend, frontend and repository-owned Rupert runner from the same revision;
5. run every health, authentication, authorization, spoof-rejection, no-side-effect, legacy-absence, frontend-payload, runtime-import and synthetic-attribution smoke gate;
6. resume only after complete success, otherwise restore the prior release while the pause remains.

A backend-first or frontend-first mixed-version rollout is prohibited.

### Track B — continue the trial-readiness backlog

The immediate planning action is F0.4. Its implementation-ready specification must be written and independently reviewed before any F0.4 source change.

## Remaining programme stages

### 1. Finish Foundation phase F0

- **F0.4 — review requests without implicit unpublication:** stop feedback or review requests from silently removing a published symbol. This is the immediate live governance blocker.
- **F0.5 — backend account-security invariants:** forced PIN-change enforcement, PIN-reuse prevention, session revocation, login throttling and a consistent cookie-mutation CSRF policy.
- **F0.6 — agent manifest and Telegram routing:** reconcile the authoritative policy so Alfi remains the single Telegram orchestrator unless Chris explicitly approves another model.
- **CP0:** record immutable commits, route and attribution evidence, test partitions, unresolved risks and the F1.1 spec path.

### 2. Phase F1 — reviewer scope, capabilities and auditable access

Define the controlled discipline vocabulary; add admin-managed reviewer assignments; enforce discipline eligibility on every queue/detail/asset/property/decision route; add submitter/reviewer capability requests; and add account/access audit read surfaces. Organization accounts remain deferred.

### 3. Phase F2 — submission, lifecycle and publication safety

Add durable submission batches and status; centralize revision, review-case, split-item and publication-job transitions; implement governed withdrawal/replacement/republishing; make publication handoff idempotent and recoverable; and preserve authenticated human authority for publication and licensing-conflict decisions.

### 4. Phase F3 — BTX, Vlad and symbol-quality evidence

Create a versioned BTX reference corpus; define conversion/quarantine outcomes; add repeatable render and geometry regression evidence; and make Vlad processing records reproducible and portable. Lossy or unsupported conversion must quarantine rather than flow silently toward publication.

### 5. Phase F4 — complete user journeys and durable communication

Publish the focused Symgov/Free/Plus explanation; decide and implement controlled registration/onboarding; complete capability/profile/admin journeys; generalize transactional notifications; add trial-critical notification events and reviewer-match evidence; and make Ed support intake durable.

### 6. Phase F5 — measured intelligence, not autonomous authority

Build a versioned Libby benchmark; replace unvalidated confidence with policy evidence; make reviewer corrections reusable; and define one structured agent evidence bundle. Autonomous no-human-review publication remains deferred for the initial trial.

### 7. Phase F6 — operations, resilience and controlled trial

Add readiness checks, host/workflow monitoring, persisted incidents, deterministic Alfi summaries, safe recovery controls, global and workflow-specific human pause controls, validated backup/restore and disaster recovery, and the security/privacy/abuse release gate. Then define the named trial cohort, privacy-safe analytics and CEO-approved success/stop/expansion thresholds, publish participant documentation, rehearse the whole trial, and obtain Chris's explicit go/no-go decision before invitations.

## Immediate next action

Author and independently review the F0.4 implementation-ready specification. Do not begin F0.4 implementation in that planning session. Keep F0.3 undeployed until a separately authorized paused atomic release completes every required smoke and rollback gate.
