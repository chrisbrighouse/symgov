# F0.5 — Backend account-security invariants

> For Hermes: execute this goal in the durable serialized Kanban/Cody lane. Keep mutable implementation, immutable Stage 1 spec review, immutable Stage 2 security/code-quality review, and final verification as separate cards with frozen evidence.

Status: SPECIFIED, NOT IMPLEMENTED
Parent backlog: docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md (F0.5)
Baseline inspected: main/origin/main at 65508719cf075f5baa961f15ec0b704c02f4fddc on 2026-07-30

Goal
Enforce account-security invariants in backend authorization/runtime paths so direct API use cannot bypass forced PIN change, stale sessions cannot survive reset/deactivation, login abuse is throttled and auditable, and cookie-authenticated mutations share one fail-closed CSRF contract across v1 and legacy aliases.

Architecture boundary
Keep F0.4 publication/request-attribution behaviour intact. Do not add organizations/tenancy/payment flows. Do not change gateway/runtime deployment in this goal.

---

## 1) Current-state reconciliation (code-backed)

1. must_change_pin enforcement is frontend-only today.
- Frontend gates navigation to /change-pin when mustChangePin is true (frontend/src/App.jsx:389,479,499), but backend dependencies only enforce authenticated user/roles (backend/symgov_backend/dependencies.py:147-175).
- Authenticated callers can still execute other privileged routes if they keep a valid session.

2. Current-PIN reuse is accepted.
- /auth/change-pin verifies current PIN then writes new hash without checking newPin != currentPin (backend/symgov_backend/routes/auth.py:114-118).

3. Session revocation is incomplete.
- Deletion revokes active sessions (backend/symgov_backend/routes/admin.py:299-303).
- Deactivation in PATCH /admin/users/{id} does not revoke sessions (backend/symgov_backend/routes/admin.py:213-230).
- PIN reset sets must_change_pin true but does not revoke sessions (backend/symgov_backend/routes/admin.py:319-324).

4. Login throttling and attempt audit are absent.
- authenticate_user performs direct user lookup + PIN verify with no per-account/per-IP throttle state or attempt audit persistence (backend/symgov_backend/auth.py:192-202).

5. CSRF policy is inconsistent.
- Profile subscription mutations already enforce JSON + same-origin Origin/Referer host check (backend/symgov_backend/routes/profile.py:45-59).
- Other cookie-authenticated mutating routes do not share this guard (auth/admin/published/workspace/llm/public route families).

---

## 2) In-scope behaviour

### Slice A — Enforce must_change_pin in backend dependencies

Introduce a central dependency guard for session-authenticated routes:
- If authenticated user has must_change_pin=true, deny all non-essential routes with 403.
- Essential allowlist (v1 + legacy aliases where they exist):
  - POST /api/v1/auth/change-pin and /api/auth/change-pin
  - GET /api/v1/auth/me and /api/auth/me
  - POST /api/v1/auth/logout and /api/auth/logout
  - GET /api/v1/profile
- Login remains unauthenticated and unaffected.

Rules
- Guard must execute before route handler side effects.
- Guard is path/method/template based (not string-prefix heuristics on raw URL).
- Guard must apply uniformly to mounted legacy aliases.

Out of scope
- Changing role model, subscription semantics, or workspace route-policy classes from F0.2.

### Slice B — Reject current-PIN reuse

- /auth/change-pin must reject newPin equal to current verified PIN with 400 and deterministic error detail.
- Admin reset-pin may set any valid PIN (including previous unknown values), but if reset value equals current hash-equivalent PIN, reject with 400 to avoid no-op resets and to keep audit semantics clear.

### Slice C — Revoke sessions on deactivation and PIN reset

- On admin deactivation (isActive false transition), revoke all unrevoked user_sessions rows atomically in same transaction.
- On admin reset-pin, revoke all unrevoked sessions for target user in same transaction as PIN update.
- Existing delete behaviour remains and should use same shared revocation helper.
- Reactivation must not un-revoke prior sessions; user must authenticate again.

### Slice D — Bounded login throttling + attempt audit + operator recovery

Implement deterministic backend throttling for /auth/login:

Minimum data model (new migration)
- auth_login_attempt_events (append-only): timestamp, normalized email key hash, resolved user_id nullable, client_ip_hash nullable, outcome (success|failure|throttled), failure_reason (invalid_credentials|inactive_or_deleted|throttled_account|throttled_ip), request metadata snapshot (safe, non-PII raw values redacted/hashed).
- auth_login_throttle_buckets (mutable window state): scope (account|ip), bucket_key_hash, window_started_at, failure_count, blocked_until nullable, updated_at.

Policy
- Per-account and per-IP failure windows with explicit configurable limits and block durations from settings.
- Evaluate throttle before expensive credential verification where possible, but still emit attempt audit for throttled events.
- Successful login clears or decays account failure state according to policy; IP state should decay by window time, not global reset by one success.
- Use deterministic hashing/salting for stored email/IP keys; never persist raw PIN.

Safe operator recovery
- Admin-only recovery endpoint(s) to clear account/IP throttle buckets with audited actor, target and reason.
- Recovery must be bounded and explicit (single account key or single IP key; optional carefully constrained bulk clear with separate reason field).

### Slice E — One consistent CSRF policy for cookie-authenticated mutations

Create one reusable guard for cookie-authenticated mutation routes:
- Require either Origin or Referer for browser-like requests; if present, enforce same-origin host match against trusted host policy.
- Require Content-Type application/json and valid JSON object for JSON-mutation endpoints.
- Enforce before domain writes.

Apply to all cookie-authenticated mutating endpoints (including legacy aliases):
- /auth/change-pin, /auth/logout
- /profile/subscription/* mutations
- /admin/** mutations
- /workspace/** mutations
- /published mutating routes (favourites/commands etc.)
- /llm/chat and other cookie-auth mutations that perform backend work
- /public/external-submissions if session-cookie authenticated

Exclusions
- Catalog API-key routes are not cookie-auth CSRF targets; preserve current API-key auth model.

---

## 3) Authorization, transition, and audit rules

- must_change_pin guard is authorization-like and must fail before write.
- Session revocation writes and account mutation writes must be one transaction.
- Login throttling and attempt events must record both denied and successful outcomes.
- Operator recovery actions must be admin-only and auditable (actor_id from session, never from client payload).

---

## 4) API/data contract changes

API
- New deterministic 403 detail for must_change_pin gate denials.
- New deterministic 400 details for PIN reuse/no-op reset rejections.
- Deterministic 429 response(s) for login throttle denials with non-sensitive retry hint.
- Admin throttle-recovery route(s) under /admin/auth/... with explicit request schema.

Data
- Add migration for throttle state + attempt audit tables and indexes.
- Reuse existing user_sessions table for revocation writes.

---

## 5) Test requirements

Focused tests (must be added/updated)
1. must_change_pin backend guard matrix
- For must_change_pin=true user: allow only essential endpoints; deny representative admin/workspace/published/profile-mutation paths.
- Include legacy alias parity where aliases exist.
- Prove fail-before-write with inert endpoint/database dependency probes.

2. PIN-change and reset rules
- change-pin rejects same current/new PIN.
- reset-pin rejects no-op reset and revokes sessions on success.

3. Deactivation/reset revocation
- deactivation revokes existing sessions; reactivation does not reactivate old session tokens.

4. Throttling/audit
- deterministic per-account and per-IP lock behaviour,
- lock expiry/unblock behaviour,
- successful login behaviour,
- audit row coverage for success/failure/throttled,
- admin recovery path and authorization.

5. Unified CSRF guard
- every cookie-auth mutation route family rejects cross-origin and malformed JSON before write.
- v1/legacy parity for shared surfaces.

Regression partitions to run at final verification
- ./scripts/test-backend.sh
- targeted auth/admin/profile/route-policy suites for F0.5
- include external partition if changed tests rely on external workspace wiring: ./scripts/test-backend.sh --external

---

## 6) Migration and rollback

Migration
- Single Alembic revision for throttle/audit tables + indexes.
- No destructive backfill required.

Rollback
- Code rollback + migration downgrade must remove only new F0.5 tables/constraints.
- Session revocation writes are runtime data and remain historically valid under rollback.

---

## 7) Serialized Cody execution contract

Execution order (strict)
1. Spec drafting/finalization against this file.
2. Fresh independent spec review.
3. Implementation/corrections on accepted spec.
4. Fresh immutable Stage 1 specification-compliance review.
5. Fresh immutable Stage 2 security/code-quality review.
6. Final verification + exact local checkpoint commit.

If any review finds issues
- Create correction card(s) and replacement review cards.
- Do not reuse stale approvals.

Completion gate for F0.5 parent milestone
- Accepted child chain is terminal,
- final local commit hash recorded,
- exact commands/results recorded,
- residual risks and non-deployed status recorded,
- no unapproved deploy/restart side effects.

Restart note requirement
- Record branch, baseline, controlling spec path, immutable review evidence references, exact verification commands/results, and next backlog path (F0.6).