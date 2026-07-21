# Customer Profile and Self-Service Subscriptions Implementation Plan

> **For Hermes:** Implement in vertical TDD slices and independently review the final diff.

**Goal:** Add an authenticated profile page where customers can view identity/subscription state, self-activate one to five years of Plus at £50/year without payment, immediately downgrade, and trigger reliable customer/admin email notifications without self-assigning roles.

**Architecture:** Add a session-owned profile API that never accepts a target user ID and delegates entitlement changes to the existing locked subscription service. Extend subscription audit events with an origin, enqueue two transactional email-outbox rows in the same commit as each successful mutation, and deliver them through an optional SMTP worker configured only from environment variables. Add a dedicated React profile component and keep policy values server-authoritative.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Alembic/PostgreSQL, React/Vite, pytest.

---

### Task 1: Add audit-origin and transactional email-outbox persistence

**Objective:** Persist self-service audit origin and reliable email work in the same database transaction as a subscription change.

**Files:**
- Create: `backend/alembic/versions/20260721_0024_profile_subscription_outbox.py`
- Modify: `backend/symgov_backend/models/schema.py`
- Modify: `backend/symgov_backend/models/__init__.py`
- Modify: `backend/symgov_backend/subscriptions.py`
- Test: `tests/test_profile_subscriptions.py`
- Test: `tests/test_subscription_migration.py`

**Steps:** Write failing model/service/migration tests; verify RED; add `subscription_events.origin`, an indexed `email_outbox` model with bounded status/attempt metadata, and origin propagation through existing mutation services; verify GREEN. Keep administrator callers defaulted to `admin`, automatic reconciliation as `system`/`expiry`, and self-service explicit.

### Task 2: Add authenticated profile and self-service subscription routes

**Objective:** Expose server-authoritative profile/plan data and safe immediate upgrade/downgrade operations for the current session only.

**Files:**
- Create: `backend/symgov_backend/routes/profile.py`
- Create: `backend/symgov_backend/email_outbox.py`
- Modify: `backend/symgov_backend/app.py`
- Modify: `backend/symgov_backend/schemas.py`
- Modify: `backend/symgov_backend/settings.py`
- Test: `tests/test_profile_subscriptions.py`

**Steps:** In vertical TDD slices, prove anonymous rejection; own-profile response; exact integer year validation 1–5; £50/year plan metadata; immediate upgrade using `years * 12` calendar months; no role grant; protected owner downgrade rejection; immediate ordinary downgrade and role removal; repeat/concurrent fail-closed behavior; two outbox rows per successful mutation. Use the session identity only and require explicit confirmation booleans.

### Task 3: Add provider-independent SMTP delivery worker

**Objective:** Deliver queued subscription emails without coupling HTTP mutation success to SMTP availability.

**Files:**
- Create: `backend/symgov_backend/email_worker.py`
- Modify: `backend/symgov_backend/app.py`
- Modify: `backend/symgov_backend/settings.py`
- Modify: `backend/README.md`
- Test: `tests/test_email_outbox.py`

**Steps:** Write failing tests for pending delivery, sent-state idempotency, sanitized retry state, and disabled/unconfigured transport; verify RED; implement an injectable batch sender plus optional startup worker driven by protected SMTP environment variables; verify GREEN. Never persist SMTP credentials or expose message delivery errors to the subscription response.

### Task 4: Add profile page and banner navigation

**Objective:** Give authenticated customers a clear, responsive profile and two-step self-service controls.

**Files:**
- Create: `frontend/src/ProfilePage.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/styles.css`
- Test: `tests/test_profile_subscription_ui.py`

**Steps:** Write failing source-contract tests for `/profile`, accessible name link, identity/tier/date rendering, 1–5 year selection, server-provided GBP pricing, no-payment notice, explicit upgrade review, immediate-downgrade warning, protected owner state, disabled duplicate submit, and auth refresh; verify RED; implement the minimal UI; run tests/build to GREEN.

### Task 5: Documentation, migration rehearsal, and independent review

**Objective:** Prove the complete implementation is safe and operable without publishing it.

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`

**Steps:** Document self-service semantics, role non-assignment, immediate downgrade, pricing policy, audit origin, SMTP/outbox configuration, and retry behavior. Run focused tests, the repository test root, frontend production build in this isolated non-mounted worktree, Python compilation, `git diff --check`, Alembic single-head check, and a disposable PostgreSQL migration rehearsal where available. Independently review authorization, concurrent mutations, email privacy/reliability, migration correctness, and frontend/backend contract. Do not deploy, restart, migrate production, push, or publish static assets without separate authorization.
