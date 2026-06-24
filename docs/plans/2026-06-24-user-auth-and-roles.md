# User Authentication and Additive Roles Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add first-class Symgov users with unique email/name, 4-digit PIN login, additive roles, and role-gated app surfaces while keeping published Standards browse available to any logged-in user.

**Architecture:** Extend the existing `users` table into an authentication principal, move from one role per user to additive `user_roles`, and add server-backed HTTP-only sessions. Enforce permissions in the FastAPI backend and mirror them in the React UI for route/nav visibility. Replace the submission PIN field once a submitter/admin user is logged in.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, React 19, Vite, pytest, Python stdlib `hashlib`/`secrets`/`hmac` for PIN hashing and session tokens.

---

## Confirmed product decisions

- First user is Chris/Alfi:
  - email: `chris.brighouse@hotmail.co.uk`
  - display name: `Alfi`
  - roles: `admin`, `submitter`, `reviewer`
  - initial PIN: `4590`
- User email and display name must both be unique, case-insensitive.
- Any authenticated user can browse published Standards.
- `reviewer` initially covers both symbol review and rights review; split later if needed.
- New/default PIN users should be forced to change PIN.
- Once logged in, the submission form should not ask for the old global submission PIN.

## Non-goals for this slice

- No email invitation/reset flow yet.
- No SSO/OAuth yet.
- No separate rights/provenance reviewer role yet.
- No public anonymous standards browse unless separately requested later; "any user" means any logged-in user.
- No migration of external identities into users yet.

## Permission model

| Surface / API area | Required access |
|---|---|
| Login/logout/me | no existing session required for login; session required for me/logout |
| Standards browse | authenticated user with any active role |
| Submissions | `submitter` or `admin` |
| Reviews | `reviewer` or `admin` |
| Rights review | `reviewer` or `admin` |
| Workspace / agent operations | `admin` |
| User management | `admin` |

## Data model target

### Existing `users` table changes

Current columns: `id`, `email`, `display_name`, `role`, `created_at`.

Target columns:

```text
id uuid primary key
email text not null
display_name text not null
pin_hash text not null
pin_set_at timestamptz not null
must_change_pin boolean not null default true
is_active boolean not null default true
created_at timestamptz not null
updated_at timestamptz not null
```

Indexes/constraints:

```text
uq_users_email_lower unique lower(email)
uq_users_display_name_lower unique lower(display_name)
```

Remove the old single-role check constraint/column after backfilling to `user_roles`.

### New `user_roles` table

```text
user_id uuid not null references users(id) on delete cascade
role text not null
created_at timestamptz not null
primary key (user_id, role)
check role in ('admin', 'submitter', 'reviewer')
```

### New `user_sessions` table

```text
id uuid primary key
auth_user_id uuid not null references users(id) on delete cascade
token_hash text not null unique
created_at timestamptz not null
expires_at timestamptz not null
revoked_at timestamptz null
last_seen_at timestamptz null
```

Use `auth_user_id` rather than `user_id` to avoid confusion with application domain relationships in route code.

---

## Task 1: Add PIN hashing utility using strict TDD

**Objective:** Provide safe creation and verification of 4-digit PIN hashes without storing raw PINs.

**Files:**
- Create: `backend/symgov_backend/auth.py`
- Test: `tests/test_auth_pin_hashing.py`

**Step 1: Write failing tests**

Create `tests/test_auth_pin_hashing.py`:

```python
from symgov_backend.auth import hash_pin, verify_pin, validate_pin


def test_hash_pin_does_not_store_raw_pin():
    hashed = hash_pin("4590")

    assert "4590" not in hashed
    assert hashed.startswith("pbkdf2_sha256$")


def test_verify_pin_accepts_matching_pin_and_rejects_other_pin():
    hashed = hash_pin("4590")

    assert verify_pin("4590", hashed) is True
    assert verify_pin("1234", hashed) is False


def test_validate_pin_requires_exactly_four_digits():
    assert validate_pin("4590") == "4590"

    for value in ["", "123", "12345", "12a4", " 4590 "]:
        try:
            validate_pin(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid PIN: {value!r}")
```

**Step 2: Run RED**

```bash
uv run --with-requirements backend/requirements.txt --with pytest python -m pytest tests/test_auth_pin_hashing.py -q
```

Expected: FAIL because `symgov_backend.auth` does not exist.

**Step 3: Implement minimal utility**

Create `backend/symgov_backend/auth.py` with:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 260_000


def validate_pin(pin: str) -> str:
    if not isinstance(pin, str) or len(pin) != 4 or not pin.isdigit():
        raise ValueError("PIN must be exactly four digits.")
    return pin


def hash_pin(pin: str) -> str:
    normalized = validate_pin(pin)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, PIN_HASH_ITERATIONS)
    return "$".join(
        [
            PIN_HASH_ALGORITHM,
            str(PIN_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_pin(pin: str, stored_hash: str) -> bool:
    try:
        normalized = validate_pin(pin)
        algorithm, iterations_raw, salt_raw, digest_raw = stored_hash.split("$", 3)
        if algorithm != PIN_HASH_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", normalized.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False
```

**Step 4: Run GREEN**

```bash
uv run --with-requirements backend/requirements.txt --with pytest python -m pytest tests/test_auth_pin_hashing.py -q
```

Expected: PASS.

---

## Task 2: Add Alembic migration for additive users/roles/sessions

**Objective:** Move from single-role users to additive roles and session-ready users.

**Files:**
- Create: `backend/alembic/versions/20260624_0017_user_auth_roles.py`
- Modify: `backend/symgov_backend/models/schema.py`
- Modify: `backend/symgov_backend/models/__init__.py`
- Test: `tests/test_user_auth_models.py`

**Step 1: Write model/migration tests**

Create `tests/test_user_auth_models.py` with SQLAlchemy metadata assertions rather than a live migration DB:

```python
from symgov_backend.models import User, UserRole, UserSession


def test_user_model_has_auth_columns():
    columns = User.__table__.columns

    assert "pin_hash" in columns
    assert "pin_set_at" in columns
    assert "must_change_pin" in columns
    assert "is_active" in columns
    assert "updated_at" in columns
    assert "role" not in columns


def test_user_role_model_is_additive():
    columns = UserRole.__table__.columns

    assert UserRole.__tablename__ == "user_roles"
    assert "user_id" in columns
    assert "role" in columns


def test_user_session_model_stores_token_hash_not_raw_token():
    columns = UserSession.__table__.columns

    assert UserSession.__tablename__ == "user_sessions"
    assert "token_hash" in columns
    assert "expires_at" in columns
    assert "revoked_at" in columns
    assert "token" not in columns
```

**Step 2: Run RED**

```bash
uv run --with-requirements backend/requirements.txt --with pytest python -m pytest tests/test_user_auth_models.py -q
```

Expected: FAIL because `UserRole` and `UserSession` do not exist and `User.role` still exists.

**Step 3: Update SQLAlchemy models**

In `backend/symgov_backend/models/schema.py`:

- update `User` columns/constraints;
- add `UserRole`;
- add `UserSession`;
- import any needed SQLAlchemy types already available in the file.

Important: keep existing FKs to `users.id` intact for domain records.

In `backend/symgov_backend/models/__init__.py`, export `UserRole` and `UserSession`.

**Step 4: Add migration**

Create `backend/alembic/versions/20260624_0017_user_auth_roles.py`:

- `down_revision = '20260619_0016'`
- Add nullable `pin_hash`, `pin_set_at`, `must_change_pin`, `is_active`, `updated_at` to `users`.
- Backfill existing users:
  - hash default `4590` in Python inside migration or set a placeholder generated by helper copied into migration. Prefer deterministic migration-local helper, not importing app code.
  - copy `created_at` into `pin_set_at` and `updated_at`.
  - set `must_change_pin = true`, `is_active = true`.
- Create `user_roles`, copy old `users.role` values into it with mapping:
  - `admin` -> `admin`
  - `reviewer` -> `reviewer`
  - old internal roles (`standards_owner`, `methods_lead`, `qa_admin`) -> `admin` unless Chris later says otherwise
- Drop old users role constraint and `users.role` column.
- Add `uq_users_display_name_lower`.
- Create `user_sessions`.

**Step 5: Run GREEN**

```bash
uv run --with-requirements backend/requirements.txt --with pytest python -m pytest tests/test_user_auth_models.py -q
```

Expected: PASS.

---

## Task 3: Add auth service and session token utilities

**Objective:** Create service functions for login, session creation, session lookup, logout, and bootstrap user upsert.

**Files:**
- Modify: `backend/symgov_backend/auth.py`
- Test: `tests/test_auth_service.py`

**Required behaviours:**

- Email lookup is case-insensitive.
- Display name uniqueness is case-insensitive during user creation/upsert.
- Login rejects inactive users.
- Login rejects wrong PIN.
- Login creates a session and returns raw token exactly once.
- Stored session token is hashed, not raw.
- `get_session_user` ignores expired/revoked sessions.
- Bootstrap/upsert can create Alfi with all roles and default PIN `4590`.

**Step 1: Write one failing test for bootstrap + roles**

Use an in-memory SQLite engine where possible, or a temporary SQLAlchemy session fixture. If JSONB/PostgreSQL-specific metadata blocks SQLite, use mocks around service functions and metadata-level tests; otherwise prefer real SQLAlchemy sessions.

**Step 2: Implement minimal service functions**

Suggested API:

```python
DEFAULT_INITIAL_PIN = "4590"
VALID_ROLES = {"admin", "submitter", "reviewer"}

@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    email: str
    display_name: str
    roles: tuple[str, ...]
    must_change_pin: bool


def create_session_token() -> tuple[str, str]: ...  # raw token, hash

def hash_session_token(token: str) -> str: ...

def authenticate_user(session: Session, *, email: str, pin: str) -> User | None: ...

def create_user_session(session: Session, *, user: User, ttl_hours: int = 24 * 14) -> str: ...

def user_roles(session: Session, user_id: uuid.UUID) -> tuple[str, ...]: ...

def current_user_from_token(session: Session, token: str) -> AuthenticatedUser | None: ...

def revoke_session(session: Session, token: str) -> bool: ...

def upsert_user(session: Session, *, email: str, display_name: str, roles: Iterable[str], pin: str = DEFAULT_INITIAL_PIN, must_change_pin: bool = True) -> User: ...
```

**Step 3: Add tests incrementally**

Follow RED/GREEN per behaviour, not one giant implementation pass.

---

## Task 4: Add auth routes and dependencies

**Objective:** Expose login/me/logout and reusable FastAPI role guards.

**Files:**
- Create: `backend/symgov_backend/routes/auth.py`
- Modify: `backend/symgov_backend/app.py`
- Modify: `backend/symgov_backend/dependencies.py`
- Modify: `backend/symgov_backend/schemas.py`
- Test: `tests/test_auth_routes.py`

**Schemas to add:**

```python
class AuthLoginRequest(BaseModel):
    email: str = Field(min_length=3)
    pin: str = Field(min_length=4, max_length=4)

class AuthUserResponse(BaseModel):
    id: str
    email: str
    displayName: str
    roles: list[str]
    mustChangePin: bool

class AuthLoginResponse(BaseModel):
    user: AuthUserResponse

class AuthMeResponse(BaseModel):
    user: AuthUserResponse | None
```

**Routes:**

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
POST /api/v1/auth/logout
```

Session cookie:

```text
symgov_session=<raw token>; HttpOnly; SameSite=Lax; Path=/; Secure on HTTPS
```

**Dependencies to add:**

```python
def get_current_user(...): ...
def require_user(...): ...
def require_role(role: str): ...
def require_any_role(roles: set[str]): ...
```

Return `401` for no/invalid session and `403` for authenticated but insufficient role.

---

## Task 5: Protect backend routes by role

**Objective:** Server-side permissions match the product model.

**Files:**
- Modify: `backend/symgov_backend/routes/workspace.py`
- Modify: `backend/symgov_backend/routes/public.py`
- Modify: `backend/symgov_backend/routes/published.py`
- Modify: `backend/symgov_backend/routes/admin.py`
- Test: add focused route guard tests, likely in `tests/test_auth_route_guards.py`

**Rules:**

- `published` routes: require any authenticated active user.
- submission routes: require `submitter` or `admin`; do not require global submission PIN for authenticated users.
- review and rights review routes: require `reviewer` or `admin`.
- workspace/admin operational routes: require `admin`.

**Important implementation note:** `routes/workspace.py` contains mixed endpoints. Apply guards at route level where needed rather than making the entire router `admin`, because review endpoints may live under workspace paths.

---

## Task 6: Add bootstrap-user CLI command

**Objective:** Seed the first Alfi user without hardcoding personal data into every migration run.

**Files:**
- Modify: `backend/manage_symgov.py`
- Test: `tests/test_manage_bootstrap_user.py`

**Command:**

```bash
uv run --with-requirements backend/requirements.txt python backend/manage_symgov.py bootstrap-user \
  --email chris.brighouse@hotmail.co.uk \
  --display-name Alfi \
  --role admin --role submitter --role reviewer \
  --pin 4590
```

Defaults may be:

```text
--email chris.brighouse@hotmail.co.uk
--display-name Alfi
--role admin --role submitter --role reviewer
--pin 4590
--must-change-pin true
```

Output JSON, for example:

```json
{"created": true, "email": "chris.brighouse@hotmail.co.uk", "displayName": "Alfi", "roles": ["admin", "submitter", "reviewer"], "mustChangePin": true}
```

---

## Task 7: Add change-PIN backend route

**Objective:** Support mandatory default PIN change.

**Files:**
- Modify: `backend/symgov_backend/routes/auth.py`
- Modify: `backend/symgov_backend/schemas.py`
- Test: `tests/test_change_pin.py`

**Route:**

```text
POST /api/v1/auth/change-pin
```

Request:

```json
{"currentPin": "4590", "newPin": "1234"}
```

Rules:

- User must be logged in.
- Current PIN must verify.
- New PIN must be exactly 4 digits.
- New PIN should not equal current PIN.
- On success, update `pin_hash`, `pin_set_at`, set `must_change_pin = false`.

---

## Task 8: Add frontend auth API client

**Objective:** Give React a small API layer for auth state.

**Files:**
- Modify: `frontend/src/api.js`
- Test: if no JS test setup exists, verify through build plus browser/manual smoke later.

Add exports:

```javascript
export async function loginUser({ email, pin }) { ... }
export async function fetchCurrentUser() { ... }
export async function logoutUser() { ... }
export async function changeCurrentUserPin({ currentPin, newPin }) { ... }
```

Use `credentials: 'include'` on auth and protected API calls so cookies are sent.

**Important:** Existing fetch calls that hit protected endpoints must also include `credentials: 'include'`.

---

## Task 9: Add React AuthProvider and login page

**Objective:** Let the UI know who is logged in and gate routes by role.

**Files:**
- Modify: `frontend/src/App.jsx`
- Optionally create: `frontend/src/auth.jsx` if splitting from the large App file is practical.

Components/helpers:

```javascript
function AuthProvider({ children }) { ... }
function useAuth() { ... }
function LoginPage() { ... }
function RequireAuth({ children }) { ... }
function RequireAnyRole({ roles, children }) { ... }
function defaultPathForUser(user) { ... }
```

Default landing:

```text
admin -> /workspace
reviewer -> /reviews
submitter -> /standards/submit
otherwise -> /standards
```

Routes:

- `/login` public
- `/standards` requires any user
- `/standards/submit` requires `submitter` or `admin`
- `/reviews` and `/rights` require `reviewer` or `admin`
- `/workspace` requires `admin`

---

## Task 10: Remove submission PIN from logged-in submission flow

**Objective:** Once logged in as submitter/admin, do not show or send the old global submission PIN.

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/api.js`
- Modify: `backend/symgov_backend/services/external_submissions.py`
- Modify: `backend/symgov_backend/routes/public.py`
- Test: update existing submission tests and add backend test for authenticated submission.

Current `ExternalSubmissionRequest` requires `pin`. For the authenticated route, either:

1. Make `pin` optional and branch on authenticated user; or
2. Add a new authenticated endpoint such as `POST /workspace/submissions` or `POST /public/external-submissions/authenticated`.

Recommendation: keep the URL stable and make the backend route accept authenticated submissions without PIN. The old global-PIN path can be removed from the UI and later disabled fully.

Backend should source submitter name/email from the logged-in user by default, while still allowing submission notes/description/files.

---

## Task 11: Add admin user management API

**Objective:** Let admins manage users and roles.

**Files:**
- Modify: `backend/symgov_backend/routes/admin.py`
- Modify: `backend/symgov_backend/schemas.py`
- Test: `tests/test_admin_users_api.py`

Routes:

```text
GET    /api/v1/admin/users
POST   /api/v1/admin/users
PATCH  /api/v1/admin/users/{user_id}
POST   /api/v1/admin/users/{user_id}/reset-pin
```

Rules:

- Admin only.
- Email and display name unique case-insensitively.
- Roles are additive and validated against `admin`, `submitter`, `reviewer`.
- New users default to PIN `4590`, `must_change_pin=true`.
- Reset PIN sets PIN to `4590`, `must_change_pin=true`.
- Deactivation sets `is_active=false` and revokes active sessions.

---

## Task 12: Add Workspace Users tab

**Objective:** Give Alfi/admins a simple user management UI.

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/api.js`
- Modify: CSS as needed in `frontend/src/App.css` or the current stylesheet path if different.

UI under Workspace:

```text
ADMIN WORKSPACE tabs: Agents | Sources | Curation | Intelligence | Users
```

Users tab features:

- List users with email, display name, roles, active status, must-change-PIN flag.
- Add user form: name, email, role checkboxes.
- Edit roles/status.
- Reset PIN button.

Keep it plain and robust. No invitation emails yet.

---

## Task 13: Build, test, and migration verification

**Objective:** Prove the full slice works locally before any public deploy.

**Commands:**

```bash
npm run build
uv run --with-requirements backend/requirements.txt --with pytest python -m pytest -q
```

Migration dry-run / upgrade check should be run against a safe database or explicit migration target, not blindly against production.

Also verify:

- Login with Alfi / `4590` succeeds.
- `mustChangePin` is true.
- Wrong PIN fails.
- Submitter/admin can submit without entering a submission PIN.
- Reviewer/admin can open Reviews and Rights.
- Submitter-only cannot open Workspace.
- Admin can create a user and reset PIN.

---

## Open implementation notes / risks

1. **Existing production users:** The existing table may contain service users such as publication-service accounts. Migration must preserve them and assign a safe role mapping.
2. **Global submission PIN compatibility:** Product decision says remove PIN once logged in. The UI should remove it immediately after auth lands. Backend can keep temporary compatibility only if needed for external callers.
3. **Rate limiting:** A 4-digit PIN needs rate limiting. If not done in this slice, add a follow-up task before exposing beyond trusted use.
4. **Cookie security:** Set `Secure` for HTTPS deployments. Local development can omit Secure for `localhost`/plain HTTP.
5. **Single large `App.jsx`:** Frontend auth may be cleaner if split into `auth.jsx`, but keep changes low-risk if the project convention is still single-file.

## Acceptance criteria

- Users have unique email and display name.
- Users can hold multiple roles.
- Alfi/Chris user exists with all initial roles and default PIN.
- Default PIN users are required to change PIN.
- Published Standards require a logged-in active user but no specific role.
- Submitter/admin can submit without entering the old submission PIN.
- Reviewer/admin can review both symbols and rights.
- Admin controls Workspace and user management.
- All backend tests pass through the established command.
- Frontend Vite build passes.
