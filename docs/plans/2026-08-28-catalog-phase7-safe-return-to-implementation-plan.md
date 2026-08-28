# Catalog Phase 7 Safe Return-To Implementation Plan

> **For Hermes:** Use `symgov-feature-implementation` and `test-driven-development` to execute this plan as one serialized L3 frontend slice. Do not begin Phase 8 automatically.

**Goal:** Preserve one validated internal Catalog destination through sign-in, organization selection, mandatory PIN change, and successful continuation without introducing an open redirect, auth loop, or logout bypass.

**Architecture:** Add pure destination-validation and HashRouter route-building helpers in `frontend/src/catalogRoutes.js`. Every authentication hop carries only the validated semantic destination string in React Router state; it never carries an external URL or user authority. Keep navigation in the existing mounted application, but exercise the real `App` through `MemoryRouter` and mocked HTTP responses so the complete multi-hop journey is proven rather than inferred from source shape.

**Tech stack:** React 19, React Router 7 `HashRouter`, Vite SSR module loading, Node's built-in test runner, `react-test-renderer`.

---

## 1. Controlling contract and baseline

### 1.1 Authoritative documents

- Specification: `docs/plans/2026-08-01-catalog-canonical-urls-and-short-links-spec.md`
- Specification SHA-256 at plan creation: `3d31bb6bc61f0c446045d63a53beffe0a704b13197e40e98d6a7aece3ade3570`
- Parent implementation plan: `docs/plans/2026-08-02-catalog-canonical-urls-and-short-links-implementation-plan.md`
- Parent plan SHA-256 at plan creation: `c31f50da868fa859ee84b00872e1d7183925164089a2e11d757f5d8f39fe5528`
- Relevant contract: specification §6 and parent plan Phase 7, Tasks 7.1–7.3.

If either controlling hash changes, stop and reconcile the changed contract before implementation.

### 1.2 Repository baseline

At plan creation:

- repository: `/docker/openclaw-hz0t/data/symgov`
- branch: `main`
- local and remote commit: `7d119ac7018bd750479c109d8775c7d31bfa7d85`
- subject: `Harden split review handoff transactions`
- worktree: clean before this plan was created
- Kanban ready/running/review/blocked/triage queues: empty

### 1.3 Readiness gate before implementation

Phases 5 and 6 are implemented and committed. Fresh Contract Review `t_4158c807`, run `612`, returned literal `CONTRACT APPROVE` for the final direct-correction manifest. The matching corrected snapshot has not yet received a fresh Security Review; the only Security verdict in that chain was the earlier historical `SECURITY REQUEST_CHANGES` that caused the final correction.

Therefore:

1. Planning Phase 7 is safe now.
2. Phase 7 source implementation must not begin until a fresh immutable Security Review returns literal `SECURITY APPROVE` for commit `7d119ac7018bd750479c109d8775c7d31bfa7d85` or byte-identical source.
3. A timeout, provider failure, `NO_VERDICT`, or approval of an earlier manifest does not satisfy this gate.

---

## 2. Current-state findings

The current frontend does not satisfy the Phase 7 contract:

1. `frontend/src/App.jsx:535-543` reads only `location.state.from.pathname`, dropping query and hash.
2. `frontend/src/App.jsx:545-555` computes a second, different post-login target and defaults to `/standards`, creating a navigation race with the login effect.
3. `frontend/src/App.jsx:417-425` carries a complete mutable location object rather than one validated internal destination.
4. `frontend/src/App.jsx:645-670` always navigates to the default route after mandatory PIN change.
5. `frontend/src/OrganizationSelectionPage.js:123-139` does not carry or resume the original destination after a multi-organization challenge.
6. `frontend/src/Header.js:24-29` already has the correct fail-closed logout shape: navigate to `/login` only after successful server revocation and do not attach return state.
7. `frontend/src/catalogRoutes.js` and `frontend/src/catalogRoutes.test.js` do not exist.
8. Existing mounted-App test infrastructure in `frontend/src/adminMountedJourneys.test.js` already demonstrates Vite SSR loading plus `MemoryRouter` and `react-test-renderer`; Phase 7 should reuse that pattern.

### Contract clarification applied by this plan

The parent Phase 7 plan names login and mandatory PIN change, but the live authentication flow also contains an organization-selection challenge. The specification requires the exact destination to survive authentication, so the destination must survive all three possible hops:

`protected route -> login -> optional organization selection -> optional mandatory PIN change -> original destination`

This does not broaden product scope; it closes a live route in the existing authentication state machine.

---

## 3. Scope and exclusions

### In scope

- Pure validation/serialization of a semantic internal route.
- Preservation of pathname, query, and fragment.
- Login continuation.
- Multi-organization selection continuation.
- Mandatory PIN-change continuation.
- Successful logout clearing continuation state.
- Fallback to `/standards` for malformed, absent, or auth-loop destinations.
- Mounted journey tests for the real application router.

### Explicitly excluded

- Phase 8 `/s/:symbolRef` and `/favorites` route mounting.
- Copy-link actions and browser metadata from Phase 9.
- `BrowserRouter`, clean URLs, Nginx/Traefik fallback, or Release B.
- Backend, database, migration, session-cookie, or authorization-policy changes.
- New subscription gates or user/account identifiers in return state.
- Deployment, static publication, service restart, migration execution, or production smoke.

If implementation appears to require a backend change, stop and review scope rather than silently widening this frontend-only slice.

---

## 4. Destination contract

### 4.1 Canonical representation

Carry one semantic route string, for example:

```text
/standards?symbol=S-000001#detail
```

React Router state should use:

```javascript
{ from: '/standards?symbol=S-000001#detail' }
```

Do not preserve or accept an entire browser `Location`, absolute URL, origin, user ID, email, account ID, session token, or arbitrary object as authority.

### 4.2 Required helpers

Create `frontend/src/catalogRoutes.js` with these exports:

```javascript
export function safeInternalDestination(value, fallback = '/standards') {}
export function internalDestinationFromLocation(location, fallback = '/standards') {}
export function destinationFromRouterState(state, fallback = '/standards') {}
export function routeForCatalogSymbol(catalogSymbolId) {}
export function absoluteHashRoute(origin, semanticPath) {}
```

Phase 7 implements and uses the first three. The last two are small pure foundations required by the accepted parent plan and must be test-covered without mounting Phase 8 routes.

### 4.3 Validation rules

A valid destination:

- is a string produced from a router location or validated state;
- starts with exactly one `/`;
- preserves pathname, query, and fragment byte-for-byte when safe;
- contains no control character or backslash;
- has valid percent escapes;
- cannot decode once into a protocol-relative path, external scheme, backslash path, or auth-loop route;
- is not `/login`, `/select-organization`, `/change-pin`, or a nested/query/fragment variant whose pathname is one of those routes;
- never depends on URL-carried user/account authority.

Malformed input returns the supplied fallback. Validation must not throw into rendering.

`routeForCatalogSymbol` accepts only an already valid canonical Catalog ID and returns `/s/<encoded-id>`; it must not fall back to UUID, slug, page code, or package data.

`absoluteHashRoute` accepts a validated same-origin origin plus a validated semantic path and returns the Release A HashRouter form. It must reject malformed origins or semantic paths rather than emitting an external or clean-route URL.

---

## 5. Vertical TDD tasks

### Task 7.0: Satisfy the Phase 5/6 closure gate

**Objective:** Prove the exact Phase 5/6 source is ready to become the immutable baseline for Phase 7.

**Files:** No repository edits.

**Steps:**

1. Confirm branch, local HEAD, `origin/main`, and clean status.
2. Confirm no ready/running/review/blocked/triage Kanban work or diagnostics.
3. Obtain one fresh immutable Security Review of commit `7d119ac7018bd750479c109d8775c7d31bfa7d85` or exact matching hashes.
4. Require literal `SECURITY APPROVE` and independently verify closing source identity.
5. Stop on any mismatch or other verdict.

**Checkpoint:** Phase 5/6 closure is complete; no Phase 7 bytes exist yet.

### Task 7.1: Define safe destination helpers

**Objective:** Establish the pure safety boundary before any mounted route uses it.

**Files:**

- Create: `frontend/src/catalogRoutes.js`
- Create: `frontend/src/catalogRoutes.test.js`

**RED cases:**

1. Preserve `/standards?symbol=S-000001#detail` exactly.
2. Build a destination from `{ pathname, search, hash }` without reading unrelated fields.
3. Fall back for null, arrays, arbitrary objects, numbers, and empty strings.
4. Reject `https://evil.example/x`, `//evil.example/x`, backslashes, control characters, malformed `%`, encoded protocol-relative forms, and encoded backslashes.
5. Reject `/login`, `/select-organization`, and `/change-pin` even when query or fragment is attached.
6. Preserve safe percent-encoded path/query values without double decoding or re-encoding.
7. Generate `/s/S-000001` only from a valid canonical ID.
8. Generate `https://example.test/#/s/S-000001` only from a valid origin and semantic route.

**Run RED:**

```bash
node --test frontend/src/catalogRoutes.test.js
```

Expected: failure because the module or required behavior is absent.

**GREEN:** Implement only the pure helpers and rerun the exact command.

**Focused regression:**

```bash
node --test frontend/src/catalogRoutes.test.js frontend/src/organizationSession.test.js
```

**Checkpoint:** Pure helpers pass; no application navigation changed.

### Task 7.2: Normalize protected-route and login continuation

**Objective:** Make the ordinary single-account sign-in path carry and resume one validated destination with no navigation race.

**Files:**

- Modify: `frontend/src/App.jsx:409-425`
- Modify: `frontend/src/App.jsx:525-556`
- Create: `frontend/src/AuthRoutes.test.js`
- Import from: `frontend/src/catalogRoutes.js`

**RED journeys:**

1. A logged-out request for `/standards?symbol=S-000001#detail` redirects to login with exactly that semantic destination in state.
2. Successful ordinary login resumes exactly that destination.
3. A malformed or external `from` value resumes `/standards`.
4. A direct authenticated visit to `/login` goes to `/standards`, not an auth loop.
5. Login submits and navigates once; the auth-state effect does not race the submit handler to a different route.
6. Failed login remains on login and retains a safe destination for retry without exposing it in error text or logs.

**Implementation rule:** Derive one `destination` through `destinationFromRouterState(location.state)` before the async login call. Both submit handling and any existing-user effect must use the same helper and state shape. Remove the pathname-only branch and the competing default navigation.

**Run RED/GREEN:**

```bash
node --test frontend/src/catalogRoutes.test.js frontend/src/AuthRoutes.test.js
```

**Checkpoint:** Single-account login continuation is green.

### Task 7.3: Preserve continuation through organization selection

**Objective:** Make the existing multi-organization challenge a transparent authentication hop.

**Files:**

- Modify: `frontend/src/App.jsx:535-555`
- Modify: `frontend/src/OrganizationSelectionPage.js:104-150`
- Modify: `frontend/src/OrganizationSelectionScreen.test.js`
- Modify: `frontend/src/AuthRoutes.test.js`

**RED journeys:**

1. Login returning an organization-selection challenge navigates to `/select-organization` with the same validated destination.
2. Successful organization selection resumes the destination exactly.
3. If the selected session still requires PIN change, it forwards to `/change-pin` with the same destination.
4. Retryable selection failure stays on the selection screen with the same safe destination.
5. Terminal challenge loss returns to sign-in without turning the old destination into authentication authority or an automatic bypass.

**Implementation rule:** `OrganizationSelectionPage` may use `useLocation` and `useNavigate`, but it must read continuation only through `destinationFromRouterState`. Navigate only after `auth.selectOrganization` reports success; use the normalized returned session/user rather than waiting for a route-side effect on mutable auth state.

**Run RED/GREEN:**

```bash
node --test \
  frontend/src/catalogRoutes.test.js \
  frontend/src/AuthRoutes.test.js \
  frontend/src/OrganizationSelectionScreen.test.js \
  frontend/src/organizationSession.test.js
```

**Checkpoint:** Single- and multi-organization login paths preserve the same safe destination.

### Task 7.4: Preserve continuation through mandatory PIN change and keep logout fail-closed

**Objective:** Complete the auth state machine without allowing stale return state after logout.

**Files:**

- Modify: `frontend/src/App.jsx:645-670`
- Modify: `frontend/src/AuthRoutes.test.js`
- Regression: `frontend/src/AppShellContext.test.js`
- Modify `frontend/src/Header.js` only if a new regression exposes a real defect.

**RED journeys:**

1. Mandatory PIN login forwards to `/change-pin` with the exact safe destination.
2. Successful PIN change resumes the exact destination.
3. Invalid or missing PIN-page state falls back to `/standards`.
4. Failed PIN change stays on the page and preserves safe retry state.
5. Successful logout navigates to `/login` with no `from` state.
6. Failed logout preserves the authenticated route and current session.

**Implementation rule:** `ChangePinPage` reads and validates the destination before submit and uses it only after a successful PIN response. It must not read user/account identity from the route. Existing logout behavior should remain unchanged unless the mounted regression proves otherwise.

**Run RED/GREEN:**

```bash
node --test \
  frontend/src/catalogRoutes.test.js \
  frontend/src/AuthRoutes.test.js \
  frontend/src/OrganizationSelectionScreen.test.js \
  frontend/src/organizationSession.test.js \
  frontend/src/AppShellContext.test.js
```

**Checkpoint:** The complete login -> optional organization selection -> optional PIN change -> destination journey is green, and logout cannot replay it.

### Task 7.5: Batch verification and immutable review

**Objective:** Close the L3 authentication slice on stable bytes.

**Verification:**

1. Run the exact focused files directly with `node --test`; do not call the repository wrapper and describe it as focused because `scripts/test-frontend.sh` appends every frontend test.
2. Run the complete frontend wrapper once:

```bash
npm run test:frontend
```

3. Run the isolated production build:

```bash
npm run build:isolated
```

4. Run:

```bash
git diff --check
```

5. Inspect the final diff and require only the planned frontend files plus this plan.
6. Freeze the exact changed-path manifest after all corrections.
7. Obtain sequential immutable `CONTRACT APPROVE` then `SECURITY APPROVE` verdicts on identical bytes because this slice changes authentication/session navigation.
8. Any correction after either review invalidates downstream approval and requires fresh review of the changed snapshot.

**Checkpoint:** Phase 7 source is accepted but not committed, pushed, deployed, published, or activated unless Chris separately authorizes those actions.

---

## 6. Acceptance matrix

Phase 7 is complete only when all rows pass:

| Case | Required outcome |
|---|---|
| Normal personal/account login | Exact safe pathname + query + fragment resumed |
| Multi-organization login | Destination survives challenge and selection |
| Mandatory PIN change | Destination survives PIN change |
| Already authenticated user visits login | Safe default; no loop or stale replay |
| Absolute/protocol-relative/encoded external target | Rejected to `/standards` |
| Malformed percent/control/backslash target | Rejected to `/standards`; render does not crash |
| Login/select/PIN failure | Remains on current auth step; safe retry state retained |
| Successful logout | `/login` with no continuation state |
| Failed logout | Session and current route retained |
| User/account override in route state | Never accepted as authority |
| HashRouter production form | Helpers emit `/#/...`, not aspirational clean routes |
| Existing auth/session tests | Remain green |
| Full frontend and isolated build | Pass on final bytes |
| L3 immutable reviews | Contract then Security approve identical bytes |

---

## 7. Authority and stopping boundaries

This plan authorizes documentation only. It does not itself authorize implementation, Kanban card creation, staging, commit, push, deployment, static publication, service restart, shared/real migration, external messaging, or Phase 8.

The implementation should use one serialized writer for Tasks 7.1–7.4, not one card per test. Use strict RED -> GREEN inside that bounded slice, then one immutable Contract Review and one Security Review. Stop after accepted Phase 7 and ask before starting Phase 8 HashRouter symbol/favorites routes.
