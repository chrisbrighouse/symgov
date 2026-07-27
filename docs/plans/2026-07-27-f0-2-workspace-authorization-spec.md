# F0.2 — Split Workspace Authorization by Operation

> **For Hermes:** Implement F0.2 only through the durable Symgov Kanban/Cody lane. Use the route inventory below as an executable contract, obtain fresh Stage 1 and Stage 2 reviews, and do not begin F0.3 until the completion gate passes.

**Parent backlog:** `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`

**Baseline:** clean `main` at `972f2b89ff6a534b6daa0572df644b041a770779` (`test: codify trustworthy F0.1 baseline`)

**Goal:** Prevent reviewers from reaching workspace agent operations, controls, scans, source configuration, worker health, or operational queues while retaining reviewer access to the review, rights, property, preview, and review-supporting asset operations they need.

**Architecture:** Replace the broad workspace-router `reviewer|admin` dependency with one route-aware, fail-closed workspace authorization dependency shared by the v1 and legacy registrations. Keep a checked-in normalized operation inventory as the single policy source: explicitly allowlisted review operations accept `reviewer|admin`; every other current, new, or unclassified workspace operation requires `admin` at runtime. Tests expand the inventory over its declared surfaces and compare it with the routes registered by the real FastAPI application.

**Tech stack:** FastAPI dependency injection and route metadata, `AuthenticatedUser`, pytest, Starlette TestClient.

---

## 1. Purpose and operational outcome

Today a user with only the global `reviewer` role receives the same workspace-router authorization as an administrator. The frontend hides `/workspace` and its administration rail from reviewers, but a reviewer can still call protected operational URLs directly. F0.2 moves that distinction into the backend security boundary.

Observable outcome:

- unauthenticated requests to every workspace operation receive HTTP 401;
- reviewers can use only the explicit review allowlist in section 4;
- reviewers receive HTTP 403 before endpoint logic for every admin operation;
- administrators retain access to every current workspace operation;
- adding a route without adding an inventory entry is admin-only at runtime and fails the inventory test;
- shared legacy and v1 operations always receive the same policy.

---

## 2. Current-state evidence

1. `backend/symgov_backend/app.py:77-81` includes the v1 `workspace_router` beneath `settings.api_prefix` (currently `/api/v1`) with `Depends(require_any_role({"admin", "reviewer"}))`.
2. `backend/symgov_backend/app.py:91-95` independently includes `legacy_workspace_router` beneath `/api` with the same broad dependency.
3. `backend/symgov_backend/routes/workspace.py:124-125` defines `router = APIRouter(prefix="/workspace")` and a separate `legacy_router`.
4. `backend/symgov_backend/routes/workspace.py:2057-4747` registers 28 v1 method/template operations and 21 legacy operations. There are 49 concrete method/template/surface entries and 28 normalized operations. Every current legacy operation has a v1 counterpart; seven review operations are deliberately v1-only in the current API.
5. The 21 shared handlers use stacked v1 and legacy decorators, so their behavior is shared. The seven v1-only operations are symbol-property mutation/options, rights and general decisions, split decisions, and two preview reads.
6. `backend/symgov_backend/dependencies.py:40-43` returns 401 with detail `Authentication required.` when there is no current user.
7. `backend/symgov_backend/dependencies.py:50-58` implements additive-role authorization and returns 403 with detail `Insufficient role for this operation.` when none of the required roles match.
8. `tests/test_auth_dependencies.py:18-54` covers the existing 401/403 dependency behavior, but not route-aware workspace policy.
9. `tests/test_route_auth_enforcement.py:24-30` checks only one unauthenticated v1 workspace request (`GET /api/v1/workspace/review-cases`). It does not enumerate either surface, distinguish reviewer from admin, or enforce parity.
10. `frontend/src/App.jsx:446-451` hides `/workspace` behind admin and exposes `/reviews` and `/rights` to `admin|reviewer`; `frontend/src/App.jsx:693-711` similarly hides the admin rail. These are useful UX controls only, not authorization controls.
11. `frontend/src/api.js:367-1127` shows that reviewer journeys consume review cases, rights cases, Daisy reports, decision routes, split decisions, symbol properties/options, and preview URLs. Daisy reports are review-coordination evidence, so their read route belongs in the reviewer allowlist. Agent queues, Tracy status, worker health, Reggie controls, Scott, Hannah, and Whitney operations do not.
12. Several workspace handlers have material operational side effects, including process start/stop and source configuration. `backend/symgov_backend/routes/workspace.py:2305-3599` contains Scott/Hannah/Whitney operational endpoints, and `backend/symgov_backend/routes/workspace.py:2964-3007` includes Hannah cleanup that can delete or mutate published records. Backend denial must occur before these handlers execute.

### Ambiguities resolved from code

- **Daisy reports:** `GET /daisy/reports` is reviewer-visible because `frontend/src/api.js:967` loads it for the review journey and the handler returns review-coordination reports. It is read-only review evidence, not an agent control.
- **Tracy status:** despite containing rights information, `GET /tracy/status` is operational agent status and remains admin-only. Reviewers receive rights evidence through the rights-review routes.
- **Hannah photo candidates and Whitney demand signals:** both are agent curation/intelligence outputs on the admin workspace, not assets required to decide a review case; they remain admin-only.
- **Reggie queue controls:** the current handler is observational/dry-run, but it exposes operational queue reconciliation and remains admin-only.
- **Seven v1-only operations:** F0.2 does not add new legacy aliases. Policy equivalence means every operation that exists on both surfaces has the same policy. Surface presence remains explicit in the inventory and is tested; expanding the legacy API would be unrelated product/API work.
- **Preview reads:** child and source previews are required review assets and are reviewer-visible. Authorization is still only coarse role authorization in F0.2; discipline/case-scope enforcement belongs to F1.3.
- **Generic review decisions that can trigger handoff:** they remain reviewer-visible because recording a review decision is the reviewer’s core operation. F0.2 does not redesign attribution, lifecycle, publication authority, or handoff behavior; F0.3, F0.4, and F2 own those controls.

---

## 3. Scope

### In scope

- One backend-enforced workspace authorization policy shared by both router registrations.
- Exact method plus route-template classification; query strings and concrete path IDs do not alter policy.
- An explicit reviewer allowlist.
- Admin-by-default behavior for unclassified/new routes.
- A complete executable inventory of all current v1 and legacy workspace operations.
- Route-level 401/403 tests using real FastAPI guards.
- Tests proving reviewer denials happen before DB, process, storage, or mutation code.
- Preservation of all current admin behavior and endpoint contracts.

### Explicitly out of scope

- Frontend changes or relying on frontend hiding for security.
- New legacy aliases for the seven currently v1-only routes.
- Reviewer discipline assignments or per-case scope filtering (F1.1-F1.3).
- Session-authoritative reviewer attribution (F0.3).
- Review-without-unpublication correction (F0.4).
- Changes to review decisions, publication handoff, queue state, source configuration, worker behavior, or agent runtime.
- Role schema, subscription behavior, database migrations, deployment, or live gateway/service changes.

---

## 4. Authoritative route-policy inventory

### 4.1 Inventory semantics

The JSON block below is the implementation contract and must remain valid JSON.

- `method` and `template` form the normalized operation key.
- `surfaces` expands to concrete prefixes: `v1` means `/api/v1/workspace`; `legacy` means `/api/workspace`.
- `policy: "reviewer_admin"` means an effective `reviewer` or `admin` role is accepted.
- `policy: "admin"` means only an effective `admin` role is accepted.
- The expansion must equal the real app’s workspace route inventory exactly.
- Counts at this baseline: 28 normalized operations; 49 concrete entries; 28 v1; 21 legacy; 10 normalized reviewer operations; 18 normalized admin operations; 13 concrete reviewer entries; 36 concrete admin entries.

```json
[
  {"method":"GET","template":"/agent-queue-items","surfaces":["v1","legacy"],"policy":"admin","class":"operational_queue"},
  {"method":"GET","template":"/tracy/status","surfaces":["v1","legacy"],"policy":"admin","class":"agent_status"},
  {"method":"GET","template":"/agent-worker-health","surfaces":["v1","legacy"],"policy":"admin","class":"worker_health"},
  {"method":"GET","template":"/reggie/queue-controls","surfaces":["v1","legacy"],"policy":"admin","class":"queue_control"},
  {"method":"POST","template":"/scott/source-searches","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"POST","template":"/scott/source-searches/{queue_item_id}/stop","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"GET","template":"/scott/source-sites","surfaces":["v1","legacy"],"policy":"admin","class":"source_config"},
  {"method":"PATCH","template":"/scott/source-sites/{source_site_id}/prompt","surfaces":["v1","legacy"],"policy":"admin","class":"source_config"},
  {"method":"PATCH","template":"/scott/source-sites/{source_site_id}/include-next-run","surfaces":["v1","legacy"],"policy":"admin","class":"source_config"},
  {"method":"PATCH","template":"/scott/source-sites/{source_site_id}/status","surfaces":["v1","legacy"],"policy":"admin","class":"source_config"},
  {"method":"PATCH","template":"/scott/source-sites/{source_site_id}/auth","surfaces":["v1","legacy"],"policy":"admin","class":"source_config"},
  {"method":"POST","template":"/hannah/cleanup-actions","surfaces":["v1","legacy"],"policy":"admin","class":"agent_control"},
  {"method":"POST","template":"/hannah/curation-searches","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"POST","template":"/hannah/curation-searches/{queue_item_id}/stop","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"GET","template":"/hannah/photo-candidates","surfaces":["v1","legacy"],"policy":"admin","class":"agent_operational"},
  {"method":"POST","template":"/whitney/demand-scans","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"POST","template":"/whitney/demand-scans/{queue_item_id}/stop","surfaces":["v1","legacy"],"policy":"admin","class":"scan_control"},
  {"method":"GET","template":"/whitney/demand-signals","surfaces":["v1","legacy"],"policy":"admin","class":"agent_operational"},
  {"method":"GET","template":"/review-cases","surfaces":["v1","legacy"],"policy":"reviewer_admin","class":"review"},
  {"method":"GET","template":"/rights-review-cases","surfaces":["v1","legacy"],"policy":"reviewer_admin","class":"rights"},
  {"method":"PATCH","template":"/review-cases/{review_case_id}/symbol-properties","surfaces":["v1"],"policy":"reviewer_admin","class":"property"},
  {"method":"GET","template":"/review-symbol-property-options","surfaces":["v1"],"policy":"reviewer_admin","class":"property"},
  {"method":"POST","template":"/rights-review-cases/{review_case_id}/decisions","surfaces":["v1"],"policy":"reviewer_admin","class":"rights"},
  {"method":"POST","template":"/review-cases/{review_case_id}/decisions","surfaces":["v1"],"policy":"reviewer_admin","class":"review"},
  {"method":"POST","template":"/review-cases/{review_case_id}/split-items/process-decisions","surfaces":["v1"],"policy":"reviewer_admin","class":"review"},
  {"method":"GET","template":"/daisy/reports","surfaces":["v1","legacy"],"policy":"reviewer_admin","class":"review_asset"},
  {"method":"GET","template":"/review-cases/{review_case_id}/children/preview","surfaces":["v1"],"policy":"reviewer_admin","class":"preview"},
  {"method":"GET","template":"/review-cases/{review_case_id}/source/preview","surfaces":["v1"],"policy":"reviewer_admin","class":"preview"}
]
```

### 4.2 Explicit reviewer allowlist

Only the ten normalized operations marked `reviewer_admin` above may accept a reviewer. Naming conventions are not policy: a future route containing `review`, `rights`, `property`, or `preview` is still admin-only until explicitly added to the inventory and allowlist after review.

Every queue, control, scan, source configuration, agent-operational, worker-health, and unclassified workspace operation is admin-only, including read-only operational views.

---

## 5. Required implementation mechanism

### 5.1 Policy registry and classifier

Implement the normalized inventory as immutable checked-in data in `backend/symgov_backend/dependencies.py`, or in a narrowly named module imported by it if separation materially improves clarity. Do not duplicate policy literals between `app.py`, route decorators, and tests.

The implementation must expose pure helpers equivalent to:

1. identify the workspace surface and normalized template from a registered FastAPI route template;
2. look up `(method, normalized_template)` in the inventory;
3. return the declared policy when the surface is declared for that operation;
4. return `admin` for an unknown method/template, unknown surface, malformed route scope, or missing route metadata.

Use the matched route template from `request.scope["route"].path`, not `request.url.path`, so concrete UUIDs cannot affect classification. Normalize only the two exact prefixes `/api/v1/workspace` and `/api/workspace`; do not use substring matching or broad `startswith("/api")` policy decisions.

### 5.2 One real dependency on both routers

Add a workspace-specific dependency that:

1. depends on `require_user`, preserving the existing 401 contract;
2. reads the request method and matched route template;
3. obtains the required policy from the classifier;
4. accepts admin for every policy;
5. accepts reviewer only for `reviewer_admin`;
6. otherwise raises HTTP 403 with the existing detail `Insufficient role for this operation.`.

In `backend/symgov_backend/app.py`, replace both broad `require_any_role({"admin", "reviewer"})` workspace include dependencies with this same dependency. Both router registrations must call the identical policy code.

Do not layer a global admin dependency with route-level reviewer dependencies: FastAPI dependencies are additive, so that design would still reject reviewers. Do not rely solely on per-route decorators: an omitted dependency would reopen the original fail-open defect.

### 5.3 Fail-closed invariant

Two independent controls are mandatory:

- **Runtime:** an operation absent from the registry resolves to `admin`, never reviewer.
- **Verification:** the route-inventory test compares the expanded registry with the real `create_app().routes` inventory and fails if either has an undeclared route, a stale entry, a changed method/template, a changed surface, or a duplicate key.

Therefore a newly added route is protected as admin immediately, but the suite remains red until its policy and surface are deliberately recorded. Adding an inventory row marked reviewer requires explicit code review; a route name alone can never grant reviewer access.

### 5.4 Legacy/v1 equivalence invariant

For every normalized operation whose `surfaces` contains both values:

- method and normalized template must match after prefix removal;
- policy must come from the same single registry entry;
- the same reviewer/admin outcomes must be observed on both concrete URLs.

The inventory test must also assert the exact seven v1-only operations at this baseline. Their absence from legacy is intentional inventory state, not permission drift. F0.2 must not add aliases merely to make counts equal.

---

## 6. API behavior contract

| Caller | `reviewer_admin` operation | `admin` or unclassified operation |
|---|---|---|
| No authenticated user | 401, `Authentication required.` | 401, `Authentication required.` |
| Authenticated user with reviewer only | proceeds to endpoint | 403, `Insufficient role for this operation.` |
| Authenticated user with admin only | proceeds to endpoint | proceeds to endpoint |
| Authenticated user with admin and reviewer | proceeds to endpoint | proceeds to endpoint |
| Other roles only | 403 | 403 |

Additional rules:

- Authorization executes before endpoint DB access, file/object download, subprocess start/stop, queue mutation, or record mutation.
- Existing 404, 409, 422, and 500 endpoint behavior after successful authorization is unchanged.
- No response may disclose whether a protected operational resource exists to a reviewer denied by policy.
- Query parameters do not change route policy.
- HEAD/OPTIONS are not separately inventoried unless FastAPI explicitly registers them as callable workspace operations; tests enumerate actual registered methods and exclude framework-generated `HEAD`/`OPTIONS` only when they are not present in `route.methods` as product operations.

---

## 7. Test specification

### 7.1 Pure dependency tests

Modify `tests/test_auth_dependencies.py` to cover:

- reviewer accepted for a representative explicit reviewer operation;
- reviewer rejected for a representative explicit admin operation;
- reviewer rejected for an unknown operation;
- admin accepted for reviewer, admin, and unknown operations;
- another role rejected;
- absent route metadata, malformed path, unknown prefix, and method mismatch default to admin;
- concrete path IDs are never used as inventory keys;
- 401 and 403 status/detail contracts remain exact.

Use the production classifier and production dependency; do not reimplement policy logic in the test.

### 7.2 Machine-checkable real-app inventory

Expand `tests/test_route_auth_enforcement.py` with a helper that walks `create_app().routes`, selects only `/api/v1/workspace` and `/api/workspace`, and records `(method, route.path)` for product methods. Assert:

- actual expansion equals the registry expansion exactly;
- normalized count is 28;
- concrete count is 49;
- v1 count is 28 and legacy count is 21;
- normalized reviewer/admin counts are 10/18;
- concrete reviewer/admin counts are 13/36;
- no duplicate surface/method/template entry exists;
- all 21 legacy operations have an equivalent v1 operation and identical policy;
- the exact seven v1-only entries are those declared in section 4;
- there are no legacy-only entries;
- every route has the workspace authorization dependency attached through the real app registration.

The expected inventory must be imported from production policy data and independently compared with the app route graph. Tests may assert the fixed baseline counts and v1-only set, but must not maintain a second full hand-written policy map.

### 7.3 Route authorization matrix

Using TestClient and `app.dependency_overrides[get_current_user]`, exercise both prefixes with production workspace authorization enabled:

1. unauthenticated: every one of the 49 concrete entries returns 401 before endpoint code;
2. reviewer: each of the 36 concrete admin entries returns 403 before endpoint code;
3. reviewer: each of the 13 concrete `reviewer_admin` entries proceeds past authorization and does not return 401/403;
4. admin: every one of the 49 concrete entries proceeds past authorization and does not return 401/403;
5. parity: for each of the 21 normalized operations shared by v1 and legacy, assert identical authorization outcomes on both concrete URLs for unauthenticated, reviewer, and admin callers. This includes all 18 shared admin-only operations and all three shared `reviewer_admin` operations, not a representative subset.

Build the cases from the production inventory expansion so omissions fail visibly and assert the expected authorization status for every caller/route pair rather than treating a non-401/403 response alone as the matrix oracle. For every reviewer-allowed or admin-allowed probe, override DB, storage, object-download, filesystem, queue, signal, and process-facing dependencies with inert fakes before the request. The fake may return a controlled post-authorization 404 or 422 only where the test explicitly asserts that outcome; it must never invoke the real handler dependency chain far enough to start or signal a subprocess, touch `/data` runtime files, download an object, mutate a queue, or read or mutate a real database. Record sentinels on the inert fakes where needed to prove that an allowed request crossed authorization without performing the side effect.

Add sentinel tests for dangerous handlers (at minimum Hannah cleanup and one scan start/stop route) proving a reviewer receives 403 and the fake mutation/process sentinel remains untouched.

### 7.4 Regression tests

Run existing tests that directly cover the changed boundary:

```bash
PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py -q
PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest tests/test_workspace_rights_review_api.py tests/test_workspace_asset_preview.py -q
```

Use these layered clean-environment commands for focused files. The backend wrapper always
selects the complete portable test root before forwarding arguments, so it is the broader
portable gate rather than a focused-file selector.

---

## 8. Implementation tasks

### Task 1: Add the failing inventory contract

**Files:**
- Modify: `tests/test_route_auth_enforcement.py`

**Steps:**
1. Introspect the real app’s workspace routes.
2. Assert the section 4 inventory shape, counts, surface expansion, and shared parity.
3. Add unknown-route fail-closed expectations.
4. Run the focused file and record the expected failure against the broad router dependency/missing policy registry.

### Task 2: Implement the fail-closed policy registry and dependency

**Files:**
- Modify: `backend/symgov_backend/dependencies.py`
- Optionally create: `backend/symgov_backend/workspace_authorization.py` only if used as the single policy source
- Modify: `backend/symgov_backend/app.py`

**Steps:**
1. Add immutable inventory data and pure normalization/classification helpers.
2. Add the workspace authorization dependency with unknown-to-admin behavior.
3. Attach the same dependency to v1 and legacy workspace router registrations.
4. Remove the broad reviewer/admin include dependency from those two registrations only.
5. Run dependency and route-inventory tests.

### Task 3: Complete the role and side-effect matrix

**Files:**
- Modify: `tests/test_auth_dependencies.py`
- Modify: `tests/test_route_auth_enforcement.py`

**Steps:**
1. Add anonymous, reviewer, admin, combined-role, and unrelated-role cases.
2. Exercise all 49 anonymous outcomes, all 49 reviewer outcomes (36 denials and 13 allows), and all 49 admin outcomes.
3. Assert anonymous/reviewer/admin legacy-v1 parity for every one of the 21 shared operations.
4. Use inert dependency overrides for every allowed-route probe and assert they cannot perform external or durable side effects.
5. Add dangerous-handler sentinels proving denial precedes side effects.
6. Run the focused authorization matrix.

### Task 4: Run focused and portable-backend verification

Run from repository root:

```bash
git diff --check
python3 -m py_compile backend/symgov_backend/app.py backend/symgov_backend/dependencies.py tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py -q
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest tests/test_workspace_rights_review_api.py tests/test_workspace_asset_preview.py -q
./scripts/test-backend.sh
```

If a new policy module is created, include it in `py_compile`. F0.2 closes with the focused authorization/workspace regressions above plus the portable backend baseline `./scripts/test-backend.sh`. Record exact commands, outputs, exit codes, and wall durations. Do not weaken or bypass a bounded wrapper to obtain green output.

The external-workspace/full backend partition (`./scripts/test-backend.sh --full`), frontend suite, Langfuse PoC suite, and isolated frontend build are release gates for the next separately authorized broader pre-production testing stage. They do not run inside F0.2 and their absence is not an F0.2 failure.

### Task 5: Independent review and immutable local checkpoint

1. Request fresh Stage 1 specification-compliance review of the unchanged implementation snapshot.
2. Resolve all actionable findings and rerun focused checks.
3. Request fresh Stage 2 security/code-quality review of the unchanged corrected snapshot.
4. Resolve all Critical/Important/drift findings.
5. Rerun the complete F0.2 focused and portable-backend gate after the final edit.
6. Create one local commit containing both the implementation and accepted spec only after both reviews approve the unchanged combined snapshot. Do not push.

---

## 9. Acceptance criteria

F0.2 is accepted only when all are observable:

1. The real app has exactly 49 declared workspace method/template/surface entries at this baseline: 28 v1 and 21 legacy.
2. The normalized inventory has exactly 28 operations: 10 reviewer/admin and 18 admin-only.
3. A reviewer is denied on every one of the 36 concrete admin entries.
4. A reviewer is accepted by authorization on only the 13 concrete allowlisted review entries.
5. Unauthenticated requests return 401 and insufficient roles return 403 with the existing details.
6. Admin is accepted by authorization for every current operation and for an unclassified-operation classifier probe.
7. Every new/unclassified route is admin-only at runtime and makes the inventory test fail until classified.
8. All 21 shared legacy/v1 operations have identical policy and authorization outcomes.
9. The exact seven v1-only operations remain explicit; no new legacy surface is introduced.
10. Reviewer denial occurs before DB, storage, subprocess, filesystem, signal, queue, or record side effects.
11. Frontend role hiding is documented but is not used as evidence of backend security.
12. Focused authorization, rights, and preview regressions pass, and the portable backend baseline passes. External-workspace/full backend, frontend, Langfuse, and isolated-build release gates are deferred to the next separately authorized broader pre-production testing stage.
13. No migration, deployment, service restart, external message, publication/withdrawal, or gateway change occurs.
14. Fresh Stage 1 and Stage 2 reviews approve the final unchanged implementation snapshot.

---

## 10. Migration, deployment, rollback, and risk

### Data migration

None. F0.2 changes request authorization and tests only. No model, schema, Alembic, data backfill, or data repair is permitted.

### Runtime/deployment

No runtime action is authorized by this spec. A later separately authorized deployment will require an application restart/replacement to load the dependency change, but implementation and verification must not restart any service, worker, gateway, or public process.

### Rollback

Code rollback is sufficient: revert the F0.2 commit and redeploy through the normal separately authorized path. There is no database downgrade. Rollback reopens the known reviewer over-permission and therefore is a security regression, not a safe steady state; if rollback becomes necessary, restrict reviewer accounts or remove reviewer workspace access until a corrected build is deployed.

### Residual risks after F0.2

- Reviewer access remains global rather than discipline/case-scoped until F1.3.
- Review/publication actor attribution remains client-influenced until F0.3.
- Review requests can still affect publication state until F0.4.
- Preview authorization is role-level only; per-case eligibility follows in F1.3.
- Existing operational handlers may have their own safety defects; F0.2 only restricts who can reach them.

---

## 11. Completion gate and exact next handoff

Completion requires:

- all 14 acceptance criteria;
- exact final inventory counts and any code-discovered corrections recorded in the completion handoff;
- changed paths and local commit SHA recorded;
- exact focused authorization/workspace and portable backend baseline command results/durations recorded;
- review task IDs/outcomes and resolved findings recorded;
- clean repository status after the local commit;
- explicit confirmation of no migration/runtime/external side effects.

### F0.2 completion evidence (2026-07-27)

**Reviewed identity and inventory:** The final implementation snapshot remained on baseline
HEAD `972f2b89ff6a534b6daa0572df644b041a770779` through both immutable reviews and the
pre-commit gate. It contains 28 normalized operations and 49 concrete routes: 28 v1,
21 legacy, 21 shared, seven v1-only, zero legacy-only, 10 normalized/13 concrete
`reviewer_admin`, and 18 normalized/36 concrete `admin`. All 49 anonymous, reviewer,
and admin outcomes and all three caller outcomes across every shared operation are covered.

**Changed paths:**

- `backend/symgov_backend/app.py`
- `backend/symgov_backend/dependencies.py`
- `tests/test_auth_dependencies.py`
- `tests/test_route_auth_enforcement.py`
- `docs/plans/2026-07-27-f0-2-workspace-authorization-spec.md`

**RED to GREEN:** The implementation task `t_6d9eb1b1` first ran the focused contract
without the production inventory/classifier/dependency and observed the intended two
collection ImportErrors (exit 2, 4.28 seconds). After implementation, the same focused
surface passed 186 tests. Orchestrator acceptance recorded that the RED was caused by the
missing F0.2 behavior rather than an environment or fixture defect.

**Fresh completion-gate results:**

- `python3 -m py_compile backend/symgov_backend/app.py backend/symgov_backend/dependencies.py tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py` — exit 0, no output, 0.03 seconds.
- `PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py -q` — exit 0, 186 passed with 36 existing FastAPI `on_event` deprecation warnings, 4.33 seconds wall time (pytest 3.23 seconds).
- `PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest tests/test_workspace_rights_review_api.py tests/test_workspace_asset_preview.py -q` — exit 0, 20 passed, 1.59 seconds wall time (pytest 0.78 seconds).
- `./scripts/test-backend.sh` — exit 0, 933 passed, 3 deselected, 1152 existing FastAPI `on_event` deprecation warnings, 23.58 seconds wall time (pytest 21.15 seconds).
- `git diff --check` — exit 0, no diagnostics, 0.00 seconds.
- `git diff --no-index --check /dev/null docs/plans/2026-07-27-f0-2-workspace-authorization-spec.md` — expected difference exit 1 with zero whitespace diagnostics.

The separately sequenced `./scripts/test-backend.sh --full`, frontend, Langfuse PoC, and
isolated frontend build gates were deliberately not run in this bounded F0.2 completion task.

**Independent reviews:** Stage 1 task `t_8c833387` returned **PASS** with no actionable
specification-compliance gaps. Stage 2 task `t_b02100b2` returned **APPROVED** with no
Critical, Important, code-quality, security, or specification-drift findings. Per-path
SHA-256 values and status were identical across both reviews and at the start and end of
the fresh pre-commit gate.

**Migration, runtime, product behavior, and side effects:** No schema/model/Alembic change
or data migration exists. No deploy, service/gateway restart, publication/withdrawal,
external message, live database mutation, queue mutation, storage access, filesystem
runtime access, or process start/signal occurred. The product change is limited to backend
workspace authorization: only the ten declared review operations remain available to reviewers, who are
denied every operational/admin operation before handler or database dependencies; admins
retain all 49 routes; unknown or malformed operations fail closed to admin. Existing 401
and 403 details and v1/legacy shared behavior are preserved.

**Residual risks:** Reviewer access remains global until F1.3; attribution remains
client-influenced until F0.3; review requests can affect publication state until F0.4;
preview access is role-level until F1.3; and operational handlers may contain independent
safety defects outside F0.2.

**Commit and handoff:** Immediately before the local commit, branch `main` remained at
baseline HEAD with exactly the five changed paths listed above and no unrelated status.
This completion record is committed atomically with those paths, so its own Git SHA cannot
be self-embedded without changing that SHA; task `t_241010cf` records the exact resulting
local commit identity and clean post-commit status. The immediate next handoff is a
separately authorized broader pre-production run of the full/external-workspace backend,
frontend, Langfuse PoC, and isolated frontend build gates. No production action or F0.3
implementation should begin from this task.

After F0.2 passes, the next backlog goal is **F0.3 — Make review and publication attribution session-authoritative**. Do not implement it in the F0.2 task.

### Copy-ready continuation prompt

```text
Continue the Symgov Trial Readiness programme in:
/docker/openclaw-hz0t/data/symgov

Start F0.2 implementation only from clean main at baseline
972f2b89ff6a534b6daa0572df644b041a770779 plus the accepted F0.2 spec snapshot.
The implementation and spec are committed together only after both reviews approve the
unchanged combined snapshot.
Read:
- docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md
- docs/plans/2026-07-26-f0-1-restart-note.md
- docs/plans/2026-07-27-f0-2-workspace-authorization-spec.md
- backend/symgov_backend/app.py
- backend/symgov_backend/dependencies.py
- backend/symgov_backend/routes/workspace.py
- tests/test_auth_dependencies.py
- tests/test_route_auth_enforcement.py

Implement F0.2 only through the durable Kanban/Cody lane. Add the single fail-closed
workspace policy registry and route-aware dependency, attach it identically to v1 and
legacy workspace routers, and implement the real-app route/role matrix. Preserve the
spec inventory: 28 normalized operations, 49 concrete entries, 28 v1, 21 legacy,
10 normalized reviewer/admin and 18 normalized admin-only operations, expanding to
13 reviewer/admin and 36 admin-only concrete entries. Unknown/new routes must be
admin-only at runtime and fail the inventory test. Do not add legacy aliases.

Run the focused authorization/workspace gates and portable backend baseline in the spec,
then obtain fresh Stage 1 and Stage 2 reviews before one local implementation-plus-spec
commit. Leave the external-workspace/full backend, frontend, Langfuse, and isolated-build
release gates to the next separately authorized broader pre-production testing stage.
Do not push, deploy, migrate, restart services or
gateways, publish/withdraw, send external messages, clean/reset/stash, or begin F0.3.
Finish with exact commands/results/durations, route counts, review outcomes, changed
paths, commit SHA, clean status, residual risks, and a no-side-effects statement.
```
