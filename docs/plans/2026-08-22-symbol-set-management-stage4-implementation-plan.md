# Symbol Set Management — Product Stage 4 Implementation Plan

> **For Hermes / GPT-5.6-Luna (max):** execute one work package per fresh context. Load `symgov-feature-implementation` and `test-driven-development` before coding. Use the durable serialized Symgov lane for implementation and fresh review contexts; never run two writers in this shared repository.

**Status:** IMPLEMENTATION-READY DRAFT — Product Stage 3 is complete at repository commit `9fa9fd10130de7aed50a05df1e14fda06308e09d`; Product Stage 4 is planned but not started.

**Goal:** Deliver organization Projects, organization-owned Symbol Set persistence, many-to-many project availability and defaults, and one server-authoritative active Symbol Set per user/project, without yet introducing private-symbol visibility or the Stage 6 effective palette.

**Architecture:** Add one additive PostgreSQL migration and matching SQLAlchemy models, then build session-scoped Project/Symbol Set services and APIs behind the existing default-off `SYMGOV_SYMBOL_SETS_ENABLED` capability. Project context is mutable in a dedicated session-context row; active-set preference is durable per `(user, project)`. All authority comes from the existing bound organization session, not client-supplied organization IDs.

**Tech stack:** FastAPI, Pydantic v2, SQLAlchemy 2, Alembic/PostgreSQL, React 19, React Router, Node test runner, pytest.

**Controlling product sources:**

- `docs/Symbol Set Management Spec v0.3.md` — SHA-256 `f9e7a8979f08308763d4047aae17608c05e449df8725c49a8c451eccbd6de656`
- `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` — SHA-256 `fa56ee7685e14103423baf1ed347e2277a0b9e50e9efe89fa892f058cb0af071`
- Programme plan: `docs/2026-08-10-symbol-set-management-implementation-plan.md` — SHA-256 at this planning checkpoint `381dd2f962d8121a672093ad965247ac380426de494351d128b97435ead648e4`; its pre-closure Stage 3 status is historical, while its §10 Stage 4 contract remains controlling except where this repository-grounded plan makes the implementation mechanics explicit.

**Authority note:** This plan authorizes documentation only. It does not authorize a commit, push, real/shared database migration, deployment, service restart, feature activation, publication, withdrawal, destructive cleanup, external messaging, or modification of `.claude/settings.local.json`.

---

## 1. Frozen scope and exclusions

### 1.1 Product Stage 4 delivers

1. Projects as real work/contract/programme contexts, not discipline folders or arbitrary personal workspaces.
2. Every active member of the bound organization can list and select every active Project in that organization, including a Project with zero available Symbol Sets.
3. Organization Administrators can create, update and close Projects.
4. Project code is immutable and organization-scoped; optional short description is plain text and accepts exactly 0–50 Unicode code points.
5. Organization Administrators can create, copy and change the lifecycle of organization-owned Symbol Sets.
6. A Symbol Set can be available to zero, one or many Projects in its owning organization.
7. A Project can have at most one active default set; an organization can have at most one active default set.
8. A user has at most one durable active-set preference per Project and one mutable selected Project per full organization session.
9. Context resolves in this exact order: request-time explicit eligible Set Code; eligible stored user preference; eligible Project default; eligible organization default; no active set. Only the selection PUT response labels the first case `explicit`.
10. Set items reference stable `governed_symbols.id` values and never duplicate symbols or pin revision UUIDs.
11. Set-item add/remove acquires the governed-symbol row lock that Stage 7 public-to-private eligibility will share.
12. Project, set, availability, default, item and context mutations are actor-attributed and transactionally audited.
13. Admin and user-facing Project/active-set selectors are mounted and accessible in the actual application journey.

### 1.2 Explicit exclusions

- No private organization symbol rows, `owner_organization_id`, visibility policy or organization review; those belong to Product Stage 5.
- No effective-palette union, organization-wide symbols, builder search, drag/drop, bulk Catalog browsing or palette badges; those belong to Product Stage 6.
- No publication, withdrawal or public-to-private transition; only the future-compatible governed-symbol lock protocol is introduced.
- No usage analytics or claim that a symbol was used. Product usage events and historical used-revision evidence belong to Product Stage 9. Stage 4 audit may record the revision observed during a set-item mutation, but it must not mislabel that as usage.
- No immutable Symbol Set release tables or UI.
- No per-user Project assignment, invitations, project teams, service credentials, offline packages, billing or generic organization-management expansion.
- No step-up requirement for ordinary Project/Set administration; accepted decision I-25 does not list these mutations. Existing authentication, CSRF and bound-session checks still apply.
- No production activation of `SYMGOV_SYMBOL_SETS_ENABLED`.

### 1.3 Correction to the carried-forward Stage 4 test wording

The programme plan's Stage 4 matrix mentions “rolling item current-approved-revision resolution and historical used-revision evidence.” Stage 4 must prove that a set item stores only the stable governed-symbol UUID and that changing `current_revision_id` does not rewrite the item. It may return the currently eligible public revision when listing items. It must not fabricate usage history; actual effective-palette resolution is Stage 6 and actual used-revision telemetry is Stage 9.

---

## 2. Repository baseline and current-state evidence

Baseline captured on 2026-08-22:

- Repository: `/docker/openclaw-hz0t/data/symgov`
- Branch: `main`
- `HEAD` and `origin/main`: `9fa9fd10130de7aed50a05df1e14fda06308e09d`
- Alembic sole head: `20260821_0029`
- Tracked tree: clean
- Preserved unrelated untracked path: `.claude/settings.local.json`

Verified seams:

- `backend/symgov_backend/models/schema.py:43-70` — full sessions already carry immutable `active_organization_id`; Stage 4 must not make this mutable.
- `backend/symgov_backend/models/schema.py:73-109` — Organization exists; Stage 4 may add a nullable organization-default-set reference only after `symbol_sets` exists.
- `backend/symgov_backend/models/schema.py:563-575` — `GovernedSymbol` provides the stable UUID and rolling `current_revision_id` required by I-23.
- `backend/symgov_backend/models/schema.py:633-642` and migration `20260821_0029` — append-only `AuditEvent` is the authoritative governed-change history.
- `backend/symgov_backend/organization_authorization.py:23-75` — active membership, organization status, pilot allowlist and bound context are already server-resolved.
- `backend/symgov_backend/settings.py:145-187` — organizations, admin and `symbol_sets_enabled` are default-off controls.
- `backend/symgov_backend/routes/auth.py:96-103` — `/auth/me` already exposes `symbolSetsEnabled` as a server-derived capability.
- `backend/symgov_backend/routes/organizations.py:51-89` — current organization APIs use `/org/me`, not client-supplied organization authority.
- `backend/symgov_backend/app.py:83-107` — routers receive global CSRF/session dependencies here.
- `backend/symgov_backend/published_catalog.py:31-38` — current public eligibility joins published page, pack, entry, revision and governed symbol; Stage 4 item validation must reuse or extract this predicate rather than inventing a weaker “revision says published” check.
- `frontend/src/App.jsx:466-500` and `frontend/src/adminRoutes.js:8-37` — mounted routing and admin extension points.
- `frontend/src/App.jsx:697-728` — live navigation currently exposes Catalog and organization administration but no Project/Set context.
- `frontend/src/organizationSession.js:15-54` — client session mode is normalized but carries no Project/Set authority.
- `scripts/test-backend.sh:39-75` and `scripts/test-frontend.sh:21-23` — wrappers are broad gates, not focused iteration commands.

Current gap classification:

| Capability | Status at baseline |
|---|---|
| Bound organization session and admin/user roles | Implemented and verified by Stages 2–3 |
| Default-off Symbol Set capability | Implemented; runtime routes absent |
| Project/Set ORM models and migration | Absent |
| Project/Set services and routes | Absent |
| Project/Set frontend journeys | Absent |
| Private symbols/effective palette/usage telemetry | Deliberately deferred |

Planning-time baseline verification on these exact pre-Stage-4 bytes:

- Focused backend command from WP0: `86 passed` in 38.66 seconds. Existing FastAPI `on_event` and Alembic configuration deprecation warnings were reported; no test failed.
- Focused frontend command from WP0: `47 passed` in 6.80 seconds. Existing `react-test-renderer` deprecation warnings were reported; no test failed.
- These are focused baseline results, not the broad backend/frontend release gates.

---

## 3. Frozen Stage 4 domain contract

### 3.1 Tables and columns

Work Package 1 creates migration `20260822_0030_project_symbol_sets.py` (new) from sole head `20260821_0029` and matching ORM models.

#### `projects`

- `id` UUID primary key.
- `organization_id` required FK to `organizations.id`, `RESTRICT` delete.
- `code` immutable display code and `normalized_code` immutable lowercase code.
- Project/Set display code grammar: `^[A-Z0-9][A-Z0-9-]{0,31}$`; normalized form is lowercase; unique `(organization_id, normalized_code)`.
- `name` required 1–200 character text after NFKC normalization and outer trimming; preserve internal whitespace and reject blank values.
- `short_description` nullable plain text; the shared metric is Unicode code points, not UTF-16 code units or grapheme clusters. Enforce at most 50 with PostgreSQL `char_length`, Python `len`, and JavaScript `Array.from(value).length`. Application trims neither meaningful internal nor edge spaces silently; empty input normalizes to null.
- `status`: `active | closed`.
- `external_reference` nullable 1–200 character trimmed display text and nullable `normalized_external_reference`; normalize the latter as `NFKC -> trim -> casefold`, and enforce partial uniqueness on `(organization_id, normalized_external_reference)` when non-null.
- `metadata_json` JSONB object default `{}` for trusted-import provenance only; it carries no authority. Validate before PostgreSQL JSONB normalization by serializing with UTF-8 JSON using sorted keys, separators `(',', ':')`, `ensure_ascii=false`, and no NaN/Infinity; size is the resulting UTF-8 byte count and must be at most 16,384. The root object is depth one; each nested object/array adds one; maximum depth is four. Keys are 1–64 Unicode code points; values are JSON objects/arrays, strings, booleans, null or finite numbers only.
- `created_by_user_id`, `created_at`, `updated_at`, `closed_at`.
- Project row and code/organization identity are never physically deleted or rewritten.

#### `symbol_sets`

- `id` UUID primary key.
- `owner_organization_id` required FK to `organizations.id`, `RESTRICT` delete.
- immutable `code` and lowercase `normalized_code`; unique `(owner_organization_id, normalized_code)` using the accepted Project/Set grammar.
- `name` required 1–200 character NFKC-normalized, outer-trimmed non-blank text; nullable `description` is at most 2,000 characters and outer-trims to null when blank.
- `disciplines_json` and `use_cases_json` JSONB arrays default `[]`. Each contains at most 32 outer-trimmed NFKC strings of 1–100 characters; deduplicate by casefolded value while preserving first display spelling and input order. These labels are descriptive and confer no permission or taxonomy authority.
- `status`: `draft | active | superseded | archived`.
- nullable `copied_from_symbol_set_id` self-FK, never self-referential and required to resolve to the same owner organization.
- `created_by_user_id`, `created_at`, `updated_at`, nullable `superseded_at`, nullable `archived_at`.
- Set row and code/owner identity are never physically deleted or rewritten.

#### `organizations.default_symbol_set_id`

- Additive nullable FK to `symbol_sets.id`, `ON DELETE SET NULL`.
- A deferred database invariant requires same owner organization and an `active` set at transaction commit.
- An organization default is only eligible for a Project when an active `project_symbol_sets` availability row also connects that set to the selected Project.

#### `project_symbol_sets`

- `id` UUID primary key.
- required `project_id` and `symbol_set_id` FKs.
- `status`: `active | inactive`.
- `is_default` boolean default false.
- `created_by_user_id`, `created_at`, `updated_at`.
- unique `(project_id, symbol_set_id)`.
- partial unique index permits at most one row with `status='active' AND is_default=true` per Project.
- deferred database invariant requires Project and Set to have the same organization and both to be active for an active availability/default.
- Removing availability deletes this membership row only; it never deletes Project, Set or symbol records.

#### `symbol_set_items`

- `id` UUID primary key.
- required `symbol_set_id` and stable `governed_symbol_id` FKs, both `RESTRICT` delete.
- non-negative `sort_order`; nullable `group_name`, `display_label`, `notes`, `preferred_format`.
- `provenance_json` JSONB object default `{}` with the same 16 KiB/depth/key bounds as Project metadata; it is descriptive only and confers no authority.
- `availability_status`: `active | unavailable`; nullable `availability_reason` at most 500 characters; `created_at`, `updated_at`, nullable `last_resolved_at`.
- unique `(symbol_set_id, governed_symbol_id)`.
- No revision FK. A rolling revision change must leave this row byte-for-byte unchanged except an explicitly performed availability refresh.

#### `user_project_set_selections`

- composite primary/unique identity `(user_id, project_id)`.
- required `active_symbol_set_id` plus `selected_at`, `updated_at`.
- Absence of a row means no stored preference. Clearing preference deletes the row and immediately re-runs Project-default -> organization-default -> none resolution; Stage 4 has no durable “override defaults with none” state.
- deferred database invariant requires Project and Set to share an organization and the selected set to have active availability to that Project.
- Parent-Project locking serializes concurrent create/update when no selection row exists yet.

#### `user_session_project_contexts`

- primary key `user_session_id`, FK to `user_sessions.id` with cascade only for session disposal.
- required `project_id`, `selected_at`, `updated_at`.
- deferred database invariant requires a full unrevoked organization session and an active Project in that session's immutable active organization.
- An `AFTER UPDATE OF revoked_at OR DELETE` trigger on `user_sessions` deletes the context row when `NEW.revoked_at IS NOT NULL` or the parent is deleted. This is deliberately parent-side and old-writer-compatible: current logout, bulk revocation and membership-deactivation writers need no coordinated code change, and raw-SQL tests must prove their commits remain valid. Natural expiry is enforced by authorization on every read/mutation; it does not require a clock-driven database delete.
- Clearing context deletes this transient row; durable audit records the change.
- Project selection never changes `user_sessions.active_organization_id`.

### 3.2 Database and transaction invariants

1. Migration and ORM metadata express matching checks, FKs, indexes and uniqueness where `Base.metadata.create_all()` can support them.
2. PostgreSQL deferred constraint triggers prove cross-table owner/availability invariants at commit; source-string assertions alone are insufficient.
3. Database triggers prevent physical deletion or identity/code/owner rewrites of Project and Symbol Set rows.
4. Project default mutation locks the Project row; organization default mutation locks the Organization row.
5. Set-item add/remove locks governed-symbol rows in deterministic UUID order before eligibility checks or membership mutation. Stage 7 must reuse this helper/boundary.
6. A Project closure, Set archive/supersession or availability removal performs cleanup in the same transaction before deferred constraints run.
7. Runtime grants permit `symgov_app` only the operations needed by these services. Project/Set history tables do not grant physical delete; transient/join membership rows may be deleted where the contract requires it.
8. Downgrade refuses while Stage 4 rows/default references exist; production downgrade/data loss is never implicit.

Supporting non-unique indexes are frozen as: `projects(organization_id, status, normalized_code, id)`; `symbol_sets(owner_organization_id, status, normalized_code, id)`; `project_symbol_sets(project_id, status, symbol_set_id)` and `(symbol_set_id, status, project_id)`; `symbol_set_items(symbol_set_id, sort_order, governed_symbol_id)` and `(governed_symbol_id, symbol_set_id)`; `user_project_set_selections(active_symbol_set_id, project_id, user_id)` and `(project_id, user_id)`; and `user_session_project_contexts(project_id, user_session_id)`. Partial uniqueness indexes remain additional to these. Project closure uses the Project-leading preference index; Set cleanup uses the active-Set-leading index; organization-default cleanup uses the already-locked owner Organization primary key rather than reverse-scanning organizations. List queries use these leading columns and never fetch an unbounded tenant collection.

Technical request-safety bounds are not commercial entitlements or product-count limits: Project/Set names are 1–200 characters; Set descriptions and item notes are at most 2,000 characters; item group/display labels and preferred-format values are at most 200 characters; page size defaults to 50 and is capped at 200; one availability replacement accepts at most 500 Project IDs; one item replacement accepts at most 1,000 entries. A future need above a batch bound uses another reviewed API shape rather than silently truncating input.

Canonical transaction lock order is: active User; bound Organization; active OrganizationMembership; active base-role assignment; application UserSession; Project rows in UUID order; Symbol Set rows in UUID order; governed-symbol rows in UUID order; then join/context/selection rows. Authority rows use PostgreSQL `FOR SHARE` in this order so concurrent Stage 4 operations need not serialize each other, while suspension/deactivation/revocation writers requiring `FOR UPDATE` serialize before or after the mutation. A service that does not need a later domain anchor stops after authority validation.

Every Stage 4 route calls one shared transaction-local principal helper inside the mutation/read transaction; the pre-endpoint `AuthenticatedUser` object is only a user-ID hint and carries no session-row identity. The route passes the trusted `Request`; the helper reads the existing session cookie, hashes it with the existing auth helper, performs a non-authoritative session probe only to discover row/organization IDs, then locks/reloads User -> Organization -> membership -> role -> that exact token-hash UserSession in canonical order and rechecks all probe values. It proves: matching active non-deleted User; unrevoked, unexpired full application UserSession; immutable organization-mode binding; active/entitled pilot Organization; active membership and active base role; current pilot allowlist; and effective Organization/Symbol-Set flags. Missing/changed probe rows fail before domain access. Admin services additionally require the locked base role `admin`. After waiting for any later domain lock, recheck lifecycle/eligibility before writing. Project/Set lifecycle, availability/default replacement and context selection use this same order. Concurrent preference/default requests are serialized by the stable parent anchor; the last request to acquire the anchor and commit wins, and each actual change is audited.

One shared pilot-aware predicate powers both the route guard and `/auth/me.capabilities.symbolSetsEnabled`. It is false for personal/credential-limited/revoked/expired sessions, non-pilot/suspended organizations, inactive membership, or either disabled feature flag; it is true for any eligible organization role, not only admins. Resolve settings/pilot flags inside the helper for the request rather than trusting values embedded in the earlier principal. A database role/session/Organization change committed before authority locks is observed and fails before domain writes; a concurrent database authority change waits or the Stage 4 transaction waits according to the authority-row lock order.

### 3.3 Lifecycle and cleanup rules

Lifecycle transitions are exact and closed-world:

| Entity | From | Allowed target | Timestamp and cleanup |
|---|---|---|---|
| Project | create | `active` | `closed_at=null`; code/organization immutable. |
| Project | `active` | `closed` | Set `closed_at=now`; delete active session-context rows; delete that Project's per-user Set-preference rows; mark availability rows inactive; clear Project-default flags; preserve all identity/history rows. |
| Project | `closed` | none | Terminal in Stage 4. Reopening needs a later accepted product decision. |
| Symbol Set | create/copy | `draft` | Lifecycle timestamps null; code/owner immutable. |
| Symbol Set | `draft` | `active` or `archived` | Activation permits an empty Set when metadata validates. Archive sets `archived_at=now` and performs ineligibility cleanup. |
| Symbol Set | `active` | `superseded` or `archived` | Supersede sets `superseded_at=now`; archive sets `archived_at=now`; both perform ineligibility cleanup. |
| Symbol Set | `superseded` | `archived` | Preserve `superseded_at`; set `archived_at=now`; cleanup is idempotent. |
| Symbol Set | `archived` | none | Terminal in Stage 4. No return to draft/active. |

Forbidden status transitions return 409. Same-status requests are idempotent no-ops and do not change timestamps or emit audit. Lifecycle timestamps, once set, are never reset. Set ineligibility cleanup marks all Project availability rows inactive, clears organization/Project defaults, deletes affected per-user Set-preference rows, and lets subsequent context resolution fall through; it preserves Set, availability, item and audit history.

Copy is allowed from any same-organization lifecycle state. It creates a new draft UUID/code/name, copies description, disciplines, use cases, item references/metadata/order and records `copied_from_symbol_set_id`; it never copies Projects, defaults, selections, audit rows or symbol records. Under canonical Set/symbol locks, re-resolve every source item. If any is currently publicly ineligible, return 409 and write nothing.

Explicit availability removal deletes only the `project_symbol_sets` row, its default and affected per-user Set-preference rows; it never deletes the Set or items. Repeating any request that already matches current state is an idempotent no-op: no duplicate row, misleading audit event or artificial `updated_at` change.

Lifecycle permissions are closed-world:

| Resource state | Ordinary-member read | Admin metadata mutation | Admin item/availability mutation |
|---|---|---|---|
| Active Project | Project summary/detail and selection allowed. | Name/description/external-reference/metadata update or close allowed. | Project default/availability changes allowed. |
| Closed Project | 404 to ordinary members; admin read allowed. | No mutation; same-state close is a no-op, all other writes 409. | No mutation; 409. |
| Draft Set | 404 to ordinary members; admin read allowed. | Metadata update, activate, archive and copy allowed. | Items may be built/changed. Project availability and defaults require an active Set and return 409 while draft. |
| Active Set | Ordinary-member detail/items/Project-availability read allowed. | Metadata update, supersede, archive and copy allowed. | Items, availability and defaults may change. |
| Superseded Set | 404 to ordinary members; admin read allowed. | Archive or copy only; other mutation 409. | Historical items/availability/default state is immutable; 409. |
| Archived Set | 404 to ordinary members; admin read allowed. | Copy only; other mutation 409. | Historical items/availability/default state is immutable; 409. |

All cross-organization/unknown IDs remain 404. Lifecycle-ineligible same-organization mutations return 409. `GET /symbol-sets/{setId}/projects` follows the same rule as Set detail/items: ordinary members only for active Sets; admins for every same-organization lifecycle state.

### 3.4 Public-symbol eligibility before Stage 5

Stage 4 can add only a currently eligible Public Catalog symbol. Extract one reusable public-eligibility query from the existing published Catalog predicate: published pack, public audience, matching pack entry/page/current revision, published revision and governed symbol. A bare `SymbolRevision.lifecycle_state == 'published'` check is insufficient. Private/tenant-owned semantics must not be anticipated before Stage 5.

Item listing returns stable symbol identity and always includes `currentRevisionId`, nullable. It is the currently resolved eligible public revision UUID when available and null otherwise. If the current public projection is no longer eligible, retain the item row and report it as unavailable; do not silently delete it.

GET/list resolution is read-only: it derives current availability and does not update `availability_status`, `availability_reason` or `last_resolved_at` as a side effect. Those persisted fields change only through an explicit governed mutation/refresh transaction introduced by this or a later stage.

### 3.5 API and error contract

All routes are under `/api/v1`, use existing session/CSRF middleware, derive organization from the authenticated principal, and are hidden with 404 while `SYMGOV_SYMBOL_SETS_ENABLED` is off. JSON uses camelCase and ISO-8601 UTC timestamps. Request models use `extra='forbid'`; omitted PATCH fields mean unchanged, explicit null clears only nullable fields, and every PATCH requires at least one field. Actor/owner/session IDs, lifecycle timestamps and audit source are never body fields.

#### Exact reusable wire models

- `ProjectSummary`: `{id, code, name, shortDescription, status}`.
- `ProjectResponse`: `ProjectSummary` plus `{externalReference, metadata, createdAt, updatedAt, closedAt}`.
- `ProjectCreateRequest`: required `{code, name}` plus optional `{shortDescription, externalReference, metadata}`; create is always active.
- `ProjectPatchRequest`: any of `{name, shortDescription, externalReference, metadata, status}`; `status` accepts only the legal transition target in §3.3; code is forbidden.
- `SymbolSetSummary`: `{id, code, name, description, disciplines, useCases, status}`.
- `SymbolSetResponse`: `SymbolSetSummary` plus `{copiedFromSymbolSetId, createdAt, updatedAt, supersededAt, archivedAt}`.
- `SymbolSetCreateRequest`: required `{code, name}` plus optional `{description, disciplines, useCases}`; create is always draft.
- `SymbolSetPatchRequest`: any of `{name, description, disciplines, useCases, status}`; status follows §3.3; code is forbidden.
- `SymbolSetCopyRequest`: required `{code, name}` only. Description, disciplines, use cases and item metadata come from the source.
- `SymbolSetItemInput`: required `{governedSymbolId, sortOrder}` plus nullable `{groupName, displayLabel, notes, preferredFormat}` and optional `provenance` object. Null clears a nullable field. `provenance` follows the Project metadata JSON size/depth/key rules.
- `SymbolSetItemResponse`: all keys are always present: `{id, governedSymbolId, sortOrder, groupName, displayLabel, notes, preferredFormat, provenance, currentRevisionId, availabilityStatus, availabilityReason, createdAt, updatedAt}`. `groupName`, `displayLabel`, `notes`, `preferredFormat`, `currentRevisionId` and `availabilityReason` are string-or-null; `provenance` is always an object, default `{}`. Derived current availability overrides stale persisted availability fields without mutating on GET.
- `SymbolSetItemsResponse`: `{items, page, pageSize, total}`, ordered by `(sortOrder, governedSymbolId)`.
- `SymbolSetProjectInput`: `{projectId, isDefault}`. `status` is not accepted; presence in a replacement means active availability.
- `SymbolSetProjectsResponse`: `{items: [{project: ProjectSummary, isDefault}], page, pageSize, total}` ordered by Project `(normalizedCode, id)` internally and exposed as code order.
- `ProjectSelectionRequest`: exactly `{projectId: UUID}`.
- `ActiveSetSelectionRequest`: exactly `{setCode: string}` using the accepted display-code grammar; the server normalizes and resolves it inside the selected Project/session organization.
- `SymbolContextResponse`: `{selectedProject: ProjectSummary|null, activeSet: SymbolSetSummary|null, reason}`. Project and available-Set options are deliberately not embedded: clients page `GET /projects` and `GET /symbol-sets?projectId=...`, avoiding unbounded context responses.
- Every list uses `{items, page, pageSize, total}` with one-based `page`, default `pageSize=50`, maximum 200. Project and Set lists order by `(normalizedCode, id)`; item and availability orders are defined above. No endpoint silently truncates a replacement body.

#### Routes, normal status and response

| Route | Authority and request | Success |
|---|---|---|
| `GET /org/me/projects` | Any active member; `page`, `pageSize`; active by default; admin-only `includeClosed=true`. | 200 paged `ProjectResponse`. |
| `POST /org/me/projects` | Admin; `ProjectCreateRequest`. | 201 `ProjectResponse`. |
| `GET /org/me/projects/{projectId}` | Member for active Project; admin may read closed same-organization Project. | 200 `ProjectResponse`. |
| `PATCH /org/me/projects/{projectId}` | Admin; `ProjectPatchRequest`. | 200 `ProjectResponse`; same-state no-op returns unchanged 200. |
| `GET /org/me/symbol-sets` | Member sees active only; `page`, `pageSize`, optional same-org `projectId`; admin may add one `status` filter. | 200 paged `SymbolSetResponse`. |
| `POST /org/me/symbol-sets` | Admin; `SymbolSetCreateRequest`. | 201 `SymbolSetResponse`. |
| `GET /org/me/symbol-sets/{setId}` | Member for active Set; admin for any same-org lifecycle. | 200 `SymbolSetResponse`. |
| `PATCH /org/me/symbol-sets/{setId}` | Admin; `SymbolSetPatchRequest`. | 200 `SymbolSetResponse`; same-state no-op returns unchanged 200. |
| `POST /org/me/symbol-sets/{setId}/copy` | Admin; `SymbolSetCopyRequest`. Implemented only in WP3 after eligibility/locks exist. | 201 `SymbolSetResponse`. |
| `GET /org/me/symbol-sets/{setId}/items` | Member for active Set; admin for any same-org Set; paged. | 200 `SymbolSetItemsResponse`. |
| `PUT /org/me/symbol-sets/{setId}/items` | Admin; `{items: SymbolSetItemInput[]}` complete replacement. | 200 `SymbolSetItemsResponse` (first page plus exact total). |
| `GET /org/me/symbol-sets/{setId}/projects` | Member for active Set; admin for any same-organization lifecycle; paged. | 200 `SymbolSetProjectsResponse`. |
| `PUT /org/me/symbol-sets/{setId}/projects` | Admin; `{projects: SymbolSetProjectInput[]}` complete active replacement; absent links are deleted, lifecycle-inactivated rows not in the body remain inactive history. Zero or many inputs may nominate this Set as default because cardinality is one default per Project, not per Set. Lock every affected Project in UUID order and atomically clear any competing default for each nominated Project. Duplicate Project IDs are 422. | 200 `SymbolSetProjectsResponse` (first page plus exact total). |
| `PUT /org/me/default-symbol-set` | Admin; exactly `{setId: UUID}`. | 200 `{defaultSymbolSetId}`. |
| `DELETE /org/me/default-symbol-set` | Admin; no body; clear idempotently. | 204 empty body. |
| `GET /org/me/symbol-context` | Active organization member; no body/query. | 200 `SymbolContextResponse`; GET never reports `explicit`. |
| `PUT /org/me/symbol-context/project` | Active member; `ProjectSelectionRequest`; replace the one session-context row, retaining preferences for other Projects. | 200 `SymbolContextResponse` after normal preference/default resolution. |
| `DELETE /org/me/symbol-context/project` | Active member; no body; delete only session Project context. | 204 empty body; durable per-Project preferences remain. |
| `PUT /org/me/symbol-context/active-set` | Active member with selected Project; `ActiveSetSelectionRequest`; persist resolved Set UUID. | 200 `SymbolContextResponse` with `reason='explicit'`. |
| `DELETE /org/me/symbol-context/active-set` | Active member with selected Project; no body; delete preference row and re-resolve defaults. | 200 `SymbolContextResponse` with fallback/none reason. |

Context reasons are exactly `explicit`, `user_preference`, `project_default`, `organization_default`, or `none`. `explicit` is response-local to a successful active-set PUT; a later GET reports the same stored Set as `user_preference`. Without a stored eligible preference, resolution falls through to Project default, organization default, then none. A zero-Set Project returns 200 with `activeSet:null` and `reason:'none'`; its empty available-Set page comes from `GET /symbol-sets?projectId=...`.

Error semantics use the live application handlers exactly. HTTP errors 401/403/404/409 are `{error, detail}`; `error='not_found'` only for 404 and `error='request_error'` otherwise. Validation 422 is `{error:'validation_error', detail:'Request validation failed.', issues:[...]}`. Every error key is mandatory; `issues` appears only on validation errors. Unknown/cross-tenant resources use the same generic 404 `{error:'not_found', detail:'Not found.'}` body. Generated `app.openapi()` must document these exact schemas/statuses even though public `/openapi.json` remains disabled. Optimistic version fields are not introduced; parent-row locks and revalidation define concurrent last-lock-winner behavior.

For item replacement, an already-present item that has since become publicly unavailable may be retained or removed; it may not be introduced into a different Set as a new item. Every genuinely new item must pass the complete current public-eligibility predicate.

### 3.6 Audit vocabulary and minimum payload

Use existing dotted action naming and the authenticated session actor. Stage 4 action names are frozen as:

- `project.created`, `project.updated`, `project.closed`, `project.selected`, `project.selection_cleared`;
- `symbol_set.created`, `symbol_set.updated`, `symbol_set.copied`, `symbol_set.activated`, `symbol_set.superseded`, `symbol_set.archived`;
- `symbol_set.project_availability_replaced`, `symbol_set.project_default_changed`, `organization.symbol_set_default_changed`;
- `symbol_set.items_replaced`, `symbol_set.selected`, `symbol_set.selection_cleared`.

Every payload includes `source`, `organizationId` and the relevant Project/Set IDs. Mutation events include old/new lifecycle or default IDs, changed-field names, affected IDs and before/after counts as applicable. Copy records source and target Set IDs. Context events record Project/Set IDs and resolution reason. Do not copy Project descriptions, Set descriptions, item notes, provenance blobs, client actor fields or raw request bodies into audit payloads. No-op idempotent requests emit no event.

---

## 4. Luna-max execution method and token controls

1. Use a fresh Luna (max) context for each implementation package and each independent review. Do not ask one context to implement all of Stage 4.
2. At package start, read only this plan, the named source ranges and the exact neighboring files listed for that package. Avoid rediscovering the entire repository.
3. Refresh `HEAD`, `origin/main`, Alembic head and dirty paths before each package. Preserve `.claude/settings.local.json`; never reset, clean, stash or rewrite unrelated work.
4. Use one batched behavioral RED for each coherent slice, then implement the whole slice, run focused GREEN once, and run its adjacent regression once. Do not spend tokens narrating repetitive micro-cycles.
5. Do not run the broad wrappers during unstable implementation. Run them once at the package/batch gate named below.
6. Keep tool output bounded: exact pytest files/node IDs, targeted searches, paged reads and concise diffs. Do not paste large source files into card comments or model responses.
7. Record one compact evidence block per accepted package: baseline commit, changed paths, RED cause, GREEN command/result, adjacent command/result, review verdict/hash if applicable, and side effects not performed.
8. If a requirement contradicts live code, stop with exact evidence. Do not silently redesign the accepted product contract.
9. No card may commit/push/migrate/deploy/restart merely because this plan mentions a checkpoint. Use hashes and test evidence unless Chris gives fresh explicit authority.
10. Stop at the package boundary. The controller, not dependency automation, authorizes the next writer or reviewer.

---

## 5. Work packages

### WP0 — Preflight and immutable inputs (controller; no product edits)

**Objective:** prove the handoff still matches the live repository before spending a Luna-max implementation context.

**Read:** the three controlling sources, this plan and the resume named in §8.

**Actions:**

1. Load `symgov-feature-implementation`, `test-driven-development`, and `symgov-programme-planning`.
2. Recompute all controlling hashes; stop on mismatch.
3. Verify branch/HEAD/origin, exact untracked paths and sole Alembic head.
4. Run the focused Stage 3/organization baseline once:

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

5. Run existing frontend organization/admin tests once:

```bash
node --test \
  frontend/src/organizationSession.test.js \
  frontend/src/organizationAdmin.test.js \
  frontend/src/adminJourneys.test.js \
  frontend/src/adminMountedJourneys.test.js
```

**Stop condition:** exact inputs match and baseline is GREEN, or a precise blocker is recorded. Do not start a migration on a failing or drifting baseline.

### WP1 — Additive schema, ORM parity and live PostgreSQL invariants (L3)

**Objective:** create future-capable Stage 4 persistence with no runtime routes.

**Files:**

- Create: `backend/alembic/versions/20260822_0030_project_symbol_sets.py`
- Modify: `backend/symgov_backend/models/schema.py`
- Modify: `backend/symgov_backend/models/__init__.py`
- Create: `tests/test_project_symbol_set_migration.py`
- Create: `tests/test_project_symbol_set_postgresql.py`
- Modify only if head assertions require it: `tests/test_auth_security_migration.py`, `tests/test_organization_migration.py`, `tests/test_llm_usage_migration.py`, `tests/test_organization_postgresql_migration.py`

**RED contract:** missing revision/models/tables; code grammar, 50-code-point DB boundary including astral values, JSON object/array types, dedicated disciplines/use-cases arrays, external-reference normalization/partial uniqueness, all frozen supporting indexes, same-owner availability, one-default race, non-null selection availability, immutable Project/Set identity, session-organization match, governed-symbol item identity, grants and downgrade refusal. Raw existing-style `user_sessions.revoked_at` update/delete must automatically remove context without changing existing writers or breaking commit.

**Implementation:** implement §3.1–3.2 exactly. Keep all new runtime behavior unreachable because no router exists.

**Focused GREEN:** exact new migration tests. **Adjacent:** existing organization/auth migration tests. **Live gate:** disposable PostgreSQL upgrade from `0029` to `0030`, index inspection, raw old-writer revocation cleanup, constraint/trigger/race probes, guarded downgrade on empty schema, upgrade again. No real/shared database.

**Review gate:** fresh Contract Review, then fresh Security Review on identical bytes and exact hashes. Any migration/model correction invalidates both verdicts.

**Checkpoint:** sole Alembic head `20260822_0030`; no routes/UI; broad portable backend wrapper once after review corrections stabilize.

### WP2 — Project and Symbol Set core lifecycle APIs (L2 batch)

**Objective:** add tenant-scoped Project CRUD and Symbol Set create/read/update/lifecycle behavior without copy, availability, items or active context.

**Files:**

- Create: `backend/symgov_backend/project_service.py`
- Create: `backend/symgov_backend/symbol_set_service.py`
- Create: `backend/symgov_backend/stage4_authorization.py`
- Create: `backend/symgov_backend/routes/projects.py`
- Create: `backend/symgov_backend/routes/symbol_sets.py`
- Modify: `backend/symgov_backend/schemas.py`
- Modify: `backend/symgov_backend/app.py`
- Modify: `backend/symgov_backend/routes/auth.py`
- Create: `tests/test_projects_api.py`
- Create: `tests/test_symbol_sets_api.py`
- Create and extend through WP4: `tests/test_stage4_route_contract.py` (registered route inventory plus generated `app.openapi()` contract)
- Modify: `tests/test_organization_auth_context.py`
- Adjacent regression: `tests/test_organization_authorization.py`, `tests/test_organization_admin_api.py`, `tests/test_route_auth_enforcement.py`, `tests/test_csrf_policy.py`

**Batched RED:** disabled flag and pilot-aware `/auth/me`; unauthenticated/personal/credential-limited/revoked/expired/non-pilot/suspended/member/admin matrix; transaction-local authority change before write; cross-organization 404; all active Projects visible to every active member through stable pages; zero-set Project validity; 0/50/51 code-point descriptions including astral characters; immutable/case-folded codes; external-reference normalization/uniqueness; exact JSON serialization/16,384-byte/depth/finite-number boundaries; exact request/response/error/status/PATCH-clear contracts; disciplines/use-cases normalization; stable pagination; complete terminal lifecycle/permission matrix; and route inventory/OpenAPI coverage for every route introduced so far.

**Implementation notes:**

- Reuse `require_organization_session` and `require_organization_admin`; service methods independently receive and verify the expected organization UUID.
- Treat route dependencies as coarse rejection only; every service uses §3.2's locked transaction-local principal helper before reading/writing tenant data.
- Do not accept `orgId` or actor IDs in requests.
- Reuse a shared Project/Set code validator implementing I-24.
- Emit append-only audit rows in the same transaction; commit only in routes after service success.

**Focused GREEN:** the two new API files. **Adjacent:** organization authorization/admin tests. Do not run PostgreSQL again unless a database invariant changes.

**Checkpoint:** core CRUD/lifecycle API works behind the default-off flag; the copy, availability, item and context routes do not exist yet. Do not spend a separate review on WP2; proceed only to WP3, whose mandatory L3 gate reviews the assembled WP1–WP3 backend bytes before WP4.

### WP3 — Project availability, defaults and stable set items (L3)

**Objective:** implement many-to-many Project availability, defaults and public-only stable set membership under future-compatible locks.

**Files:**

- Modify: `backend/symgov_backend/symbol_set_service.py`
- Modify: `backend/symgov_backend/routes/symbol_sets.py`
- Modify: `backend/symgov_backend/schemas.py`
- Create: `backend/symgov_backend/public_symbol_eligibility.py` (extract the existing complete public predicate; Stage 5 may later replace its internals with the visibility projection)
- Create: `tests/test_symbol_set_availability.py`
- Create: `tests/test_symbol_set_items.py`
- Create: `tests/test_symbol_set_copy.py`
- Extend: `tests/test_project_symbol_set_postgresql.py`
- Extend: `tests/test_stage4_route_contract.py`
- Adjacent regression: published Catalog search/detail tests that prove extraction did not broaden or narrow public results, plus `tests/test_route_auth_enforcement.py` and `tests/test_csrf_policy.py`.

**Batched RED:** one Set available/default in several Projects; zero or many default nominations in one Set-oriented replacement while each Project retains at most one default under concurrent writers; cross-owner pair rejected at service and commit; organization default eligible only with Project availability; inactive Project/Set rejection; exact lifecycle read/mutation permissions; duplicate item prevention; idempotent complete replacement; deterministic ordering; exact provenance JSON serialization/byte/depth/finite-number boundaries; published predicate; mandatory nullable response keys; rolling current revision without item rewrite; item removal does not delete GovernedSymbol; archive/supersede/availability cleanup; actor audit; governed-symbol lock ordering; copy from empty and non-empty source; copy lineage/no symbol duplication; and atomic 409/no writes when any source item is currently unavailable.

**Implementation notes:**

- Lock Set once, then governed-symbol UUIDs in ascending order for batch replacement.
- Validate all requested symbols before any membership write; fail the whole request atomically.
- Repeating the same normalized list is a no-op.
- Default and lifecycle cleanup happens in the mutation transaction, then deferred constraints validate final state.
- Retain unavailable rows when public eligibility later disappears; explicit admin removal alone deletes membership.
- Add the copy endpoint only after the shared eligibility helper and governed-symbol locking behavior are green; copy calls those same helpers rather than recreating their predicate.

**Focused GREEN:** new availability/item/copy files, route contract and live PostgreSQL default/owner/lock cases. **Adjacent:** published Catalog eligibility and route-auth/CSRF regressions.

**Checkpoint:** Project availability, public-only Set membership and full safe copy are complete; no context selection or frontend yet.

**Mandatory WP3 L3 review gate:** freeze WP1–WP3 backend bytes immediately. Run a fresh Contract Review and then Security Review on identical hashes before WP4 may edit the tree. Corrections invalidate both verdicts and require fresh affected reviews. Inspect default cardinality/races, owner invariants, lifecycle permissions, item eligibility, copy atomicity, governed-symbol locks, route/OpenAPI contracts and transaction-local authority.

### WP4 — Server-authoritative Project and active-set context (L3)

**Objective:** select one session Project and resolve/persist at most one active Set per user/Project.

**Files:**

- Create: `backend/symgov_backend/symbol_context_service.py`
- Create: `backend/symgov_backend/routes/symbol_context.py`
- Modify: `backend/symgov_backend/schemas.py`
- Modify: `backend/symgov_backend/app.py`
- Create: `tests/test_user_project_set_selection.py`
- Create: `tests/test_symbol_context_api.py`
- Extend: `tests/test_project_symbol_set_postgresql.py`
- Create: `tests/test_symbol_context_performance.py`
- Extend: `tests/test_stage4_route_contract.py`
- Adjacent regression: `tests/test_organization_auth_context.py`, `tests/test_organization_authorization.py`, `tests/test_route_auth_enforcement.py`, `tests/test_csrf_policy.py`

**Batched RED:** personal rejection; cross-tenant privacy; every active Project reachable through stable paged `/projects` results including zero-set Projects; selected Project scoped to immutable session organization; paged available active Sets only; exact PUT/DELETE wire contracts; Set Code resolution; exact reason semantics; clear deletes preference then falls back; concurrent preference updates; closed Project/archived Set/removed availability recovery; old-writer session revocation cleanup; no mutation of `active_organization_id`; transactional `project.selected`, `project.selection_cleared`, `symbol_set.selected` and `symbol_set.selection_cleared` audit; bounded query counts and indexed PostgreSQL plans.

**Implementation notes:**

- Project PUT writes/replaces only `user_session_project_contexts` plus audit; Project DELETE clears only that row and retains durable preferences.
- Active-Set PUT requires a selected Project, accepts Set Code, resolves/persists the Set UUID under Project lock and returns `explicit` only for that response. Active-Set DELETE removes the preference row; later GET/DELETE responses use fallback reasons.
- Context resolution is a pure service over committed/current transaction state. Browser storage is never a fallback authority.
- Context responses contain current selections only; selector options come from the two paginated list routes.
- Return no effective palette and no usage event.

**Focused GREEN:** two new context test files, completed route/OpenAPI contract, performance evidence and PostgreSQL selection races. **Adjacent:** organization auth/authorization and route-auth/CSRF tests.

**WP4 review gate:** after the approved WP3 baseline, freeze all Stage 4 backend paths and run a fresh Contract Review and then Security Review on identical bytes for WP4's context/authority additions. Inspect actual predicates, authority/domain lock order and commit boundaries; run the named focused, adjacent, route/OpenAPI and live PostgreSQL tests. After corrections, rerun only invalidated gates and obtain fresh matching-hash verdicts.

**Checkpoint:** complete backend Stage 4 contract; broad portable backend wrapper once; no deployment or flag activation.

### WP5 — Mounted Project/Set administration and context selectors (L1/L2 frontend)

**Objective:** make the backend capability reachable and usable without implementing the Stage 6 builder/palette.

**Files:**

- Create: `frontend/src/projectContext.js`
- Create: `frontend/src/ProjectContextBar.js`
- Create: `frontend/src/OrganizationProjectsPanel.js`
- Create: `frontend/src/OrganizationSymbolSetsPanel.js`
- Modify: `frontend/src/OrganizationAdminPage.js`
- Keep `frontend/src/adminRoutes.js` unchanged: integrate both panels into the existing mounted `/organization/admin` journey through `OrganizationAdminPage`. A new route requires a separately accepted plan amendment.
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/styles.css`
- Create: `frontend/src/projectContext.test.js`
- Create: `frontend/src/symbolSetAdmin.test.js`
- Extend: `frontend/src/adminMountedJourneys.test.js`

**Batched RED:** pilot-aware server-derived capability hides UI for every ineligible context; organization users page Project and Project-filtered active-Set selectors; Project description shown; zero-set Project is valid; accessible Unicode-code-point counter accepts 50/rejects 51 ASCII and astral characters using `Array.from`; disciplines/use-cases create/edit normalization and clear behavior; admin-only create/edit/close/set/default/copy actions; loading/empty/error/stale recovery; archived selection refresh; exact Set Code selection/clear responses; integration into existing `/organization/admin` with no new alias; keyboard labels and status/alert behavior.

**Implementation notes:**

- Mount `ProjectContextBar` only for an authenticated full organization session with `symbolSetsEnabled`.
- Refresh context from the server after every selection/mutation; local state is presentation only.
- Show `No Symbol Set` only when resolution reaches `none`; clearing a preference may immediately show a configured Project/organization default.
- Keep Set item management to metadata/listing supported by Stage 4; no Catalog search builder, drag/drop or effective palette.
- Use American-English copy: Project, Organization, Symbol Set, Catalog, Favorite.

**Focused GREEN:** exact two new frontend tests and mounted journey test. **Batch gate:** `./scripts/test-frontend.sh` once and `npm run build:isolated` once after bytes stabilize.

**Review gate:** one fresh Contract/UX review of mounted behavior and unchanged backend contract. A fresh Security Review is required only if WP5 changes backend/security-sensitive paths.

**Checkpoint:** complete source implementation of Product Stage 4, default-off.

### WP6 — Stage closure, evidence and release boundary (controller/reviewer; no speculative features)

**Objective:** prove Stage 4 acceptance on unchanged bytes and stop before Stage 5 or production effects.

**Required deterministic evidence:**

1. New migration/model static contract GREEN.
2. Disposable PostgreSQL `0029 -> 0030`, invariants/races, guarded empty downgrade and re-upgrade GREEN.
3. Project, Set, availability, items and context focused backend tests GREEN.
4. Existing organization/auth and published-Catalog adjacent regressions GREEN.
5. Portable backend wrapper GREEN.
6. Frontend focused tests, broad frontend wrapper and isolated build GREEN.
7. `git diff --check` GREEN; untracked new planning/source files checked separately before staging or review.
8. Exact changed-path manifest and hashes; `.claude/settings.local.json` unchanged.
9. Matching-hash Contract/Security verdicts at both mandatory L3 boundaries (WP3 and WP4), plus matching-hash Contract/UX verdict for final mounted frontend.
10. No migration against shared/real data, deployment, restart, flag activation, push or Stage 5 work.
11. On disposable PostgreSQL with representative tenant cardinality, `EXPLAIN (FORMAT JSON)` evidence uses the frozen indexes for Project/Set lists, availability, Set items, active-context/default resolution and governed-symbol cross-reference; bounded query-count tests show no per-row/N+1 expansion. Provisional wall-clock P95 targets remain observational, not timing-flaky CI assertions.

**Acceptance checklist:**

- AC-10: all active organization members can list/select every active same-organization Project, including a zero-set Project.
- AC-11: 0/50 accepted and 51 rejected at database/application/UI; description appears in selector.
- Stage 4 part of AC-12: Set items reference stable public symbols without copying; private symbols remain deferred.
- AC-13: exactly one eligible active Set per user/Project, rapid switching without reauthentication, deterministic fallback.
- AC-19 Stage 4 edge: cross-organization Project/Set IDs disclose no private tenant data and fail safely.
- Many-to-many availability, Project/organization defaults, copy lineage, immutable codes, lifecycle cleanup and audit are proven.
- Future releases remain possible because ordinary items store stable symbol UUID only and no release schema is prematurely added.

**Stop:** record Product Stage 4 source completion. Do not start Product Stage 5, apply migration `0030` to a real/shared database, or enable `SYMGOV_SYMBOL_SETS_ENABLED` without separate explicit authority.

---

## 6. Review checklists

### Contract Review

- Trace FR-PRJ-001–008, FR-SET-001–014 where Stage 4 applies, FR-CTX-001–008 where Stage 4 applies, AC-10–13 and AC-19.
- Verify Project semantics are real work contexts and Project eligibility is never filtered by Set availability.
- Verify APIs use bound organization authority and expose no client actor/organization authority.
- Verify every exact request/response/status/PATCH-null/DELETE contract, including Project UUID selection, Set Code selection and response-local `explicit` reason.
- Verify all 20 route method/templates, no unintended aliases, generated `app.openapi()` success/error schemas, mandatory nullable keys, and live `{error,detail[,issues]}` envelopes.
- Verify one Set may be default for several Projects while each Project has at most one default.
- Verify the closed-world lifecycle matrix, exact fallback order, preference-row deletion semantics and valid no-set context.
- Verify exclusions are not silently implemented.
- Inspect mounted routes and rendered behavior, not only helper/source-string tests.

### Security Review

- Verify feature-off 404, pilot-aware `/auth/me`, authentication/CSRF, personal/member/admin and cross-tenant matrices.
- Verify the transaction-local principal helper locks/revalidates user, session, Organization, membership and role before tenant data; stale route principals confer no authority.
- Inspect service-side organization predicates in addition to route guards.
- Verify deferred database owner/availability constraints and live PostgreSQL evidence.
- Verify the parent-side revocation cleanup trigger preserves all existing logout/bulk-revocation writers and stale context cannot authorize access.
- Verify authority/domain lock order, no partial batch writes and per-Project-default/concurrent-selection races.
- Verify lifecycle cleanup cannot leave an ineligible active selection/default.
- Verify item eligibility uses the full current public projection and no direct weak predicate.
- Verify actor/session/owner/timestamps cannot be supplied by clients and audit commits atomically.
- Verify bounded stable list ordering, required indexes, query plans and no N+1 expansion.
- Verify no private-content or publication authority is introduced.

### UX Review

- Project name and optional description are legible in selectors.
- Zero Projects, zero available Sets and no active Set are distinct valid states.
- 50-code-point counter is programmatically associated and announced appropriately; 50/51 ASCII and astral cases match PostgreSQL/Pydantic/JavaScript.
- Loading, error, stale context and archived/closed recovery are visible and keyboard accessible.
- Admin controls are not shown to ordinary members; backend denial remains authoritative.
- Actual app routing/navigation mounts all accepted journeys.

---

## 7. Suggested durable card graph

Create only after WP0 passes and Chris authorizes implementation. Keep every card initially blocked except the first authorized writer.

```text
S4-WP1 migration/models (Luna max writer)
  -> S4-WP1 Contract Review (fresh Luna max)
  -> S4-WP1 Security Review (fresh Luna max)
  -> S4-WP2 core Project/Set APIs (fresh Luna max writer)
  -> S4-WP3 availability/defaults/items/copy (fresh Luna max writer)
  -> S4-WP3 Contract Review (fresh Luna max)
  -> S4-WP3 Security Review (fresh Luna max)
  -> S4-WP4 active context (fresh Luna max writer)
  -> S4-WP4 Contract Review (fresh Luna max)
  -> S4-WP4 Security Review (fresh Luna max)
  -> S4-WP5 frontend (fresh Luna max writer)
  -> S4 final Contract/UX Review (fresh Luna max)
  -> S4-WP6 controller closure
```

Do not let automatic dependency promotion start a downstream writer/reviewer without a fresh status/queue check. A failed review remains historical; correction requires a bounded correction card and fresh affected review(s). Do not create separate RED, GREEN, checksum or duplicate final-verification cards.

---

## 8. Handoff

The companion resume is:

`docs/plans/2026-08-22-symbol-set-management-stage4-luna-resume.md`

Its plan hash must be filled from the final bytes of this document and recomputed before every implementation context. The first implementation action is WP0 only. Product Stage 5 and every production side effect remain separately gated.
