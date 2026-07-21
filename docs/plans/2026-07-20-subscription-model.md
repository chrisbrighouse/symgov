# Subscription Model Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add Free and Plus subscription lifecycle management, Plus-gated privileged roles, protected owner guarantees, scalable user administration, and user-visible Plus status.

**Architecture:** Store current entitlement in a one-to-one `user_subscriptions` row and immutable changes in `subscription_events`. Resolve expiry synchronously during authentication/authorization, remove roles when Plus ends, and retain a daily reconciliation path as housekeeping rather than a security dependency. Keep the protected Chris owner as perpetual Plus/Admin and reject deactivation, deletion, cancellation, or Admin-role removal.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic/PostgreSQL, Pydantic, React, pytest, npm/Vitest.

---

### Task 1: Add subscription persistence and calendar-month domain service

**Files:**
- Create: `backend/alembic/versions/20260720_0023_user_subscriptions.py`
- Create: `backend/symgov_backend/subscriptions.py`
- Modify: `backend/symgov_backend/models/schema.py`
- Modify: `backend/symgov_backend/models/__init__.py`
- Test: `tests/test_subscriptions.py`
- Test: `tests/test_subscription_migration.py`

**Steps:** Write failing tests for Free defaults, 3 January + 3 months = 3 April, end-of-month clamping, upgrade, adjustment, cancellation, expiry, event creation, and protected owner invariants. Run focused tests to prove RED; implement models/service/migration; rerun to GREEN.

### Task 2: Make authentication and authorization subscription-aware

**Files:**
- Modify: `backend/symgov_backend/auth.py`
- Modify: `backend/symgov_backend/schemas.py`
- Modify: `backend/symgov_backend/routes/auth.py`
- Test: `tests/test_auth_service.py`
- Test: `tests/test_auth_routes.py`

**Steps:** Add failing tests proving Free and expired users receive no privileged roles, expiry permanently removes stored roles, active Plus exposes roles and subscription metadata, and protected Chris remains perpetual Plus/Admin. Implement central effective-entitlement resolution and rerun focused tests.

### Task 3: Add scalable admin subscription and soft-delete APIs

**Files:**
- Modify: `backend/symgov_backend/routes/admin.py`
- Modify: `backend/symgov_backend/schemas.py`
- Test: `tests/test_admin_user_management_routes.py`

**Steps:** Add failing tests for Free-by-default creation, server pagination/search/filter/sort, Plus upgrade, extension/shortening with current-month floor, immediate cancellation, permanent role removal, soft deletion/session revocation, and protected-owner rejection paths. Implement dedicated subscription endpoints and paged bulk loading; rerun focused tests.

### Task 4: Add Manage Users controls and Plus badge

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`
- Test: `tests/test_admin_user_management_ui.py`

**Steps:** Add failing UI source tests for Tier/Start/Expiry columns, subscription actions, role disabling for Free users, soft deletion, pagination/search, and the signed-in Plus badge. Implement API helpers and UI controls; run focused tests and frontend build.

### Task 5: Document and verify

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`

**Steps:** Document subscription semantics, owner protections, API behavior, migration treatment of existing users, and operational expiry reconciliation. Run focused subscription/auth/admin tests, the complete backend suite, frontend tests/build, migration upgrade checks, static security scan, and independent diff review.
