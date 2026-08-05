# Catalog Canonical URLs and Short Links Implementation Plan

> **For Hermes:** Use the `subagent-driven-development`, `symgov-feature-implementation`, and `test-driven-development` skills to implement this plan task-by-task. Preserve unrelated dirty work. Do not commit, push, migrate a non-disposable database, publish static assets, restart services, or deploy without Chris's explicit authorization.

**Goal:** Persist globally stable Catalog symbol IDs, resolve canonical and legacy symbol references deterministically, deliver authenticated HashRouter symbol and favorites links with exact return-to behavior, and prepare a separately gated clean-route release.

**Architecture:** Add an additive identifier registry and nullable governed-symbol canonical ID in migration 0026, backfill through audited tooling, assign new `S-000001` IDs transactionally at first publication, then enable publication invariants in migration 0027. Route all published/Catalog callers through one type-aware resolver. Deliver browser routes and copy actions using the deployed HashRouter form first; treat BrowserRouter and production SPA fallback as a separate release that cannot begin until production topology ownership is verified.

**Tech Stack:** PostgreSQL, SQLAlchemy 2, Alembic, FastAPI, React 19, React Router 7, Vite, pytest, Node's built-in test runner.

**Controlling specification:** `docs/plans/2026-08-01-catalog-canonical-urls-and-short-links-spec.md`

---

## Scope and release boundaries

### Release A — canonical identity and HashRouter links

Release A includes migrations 0026/0027, backfill tooling, publication allocation, the shared resolver, API contract changes, exact login/PIN return-to behavior, `/#/s/<catalog-symbol-id>`, `/#/favorites`, compatibility canonicalization, copy actions, states, accessibility, and documentation.

Release A explicitly excludes production migration execution, deployment, static publication, service restart, BrowserRouter, web-server fallback changes, and stored `/go/<code>` links.

### Release B — clean routes

Release B changes generated links to `/s/<catalog-symbol-id>` and `/favorites` only after the production Nginx/Traefik/static-host ownership and fallback behavior are inspected and tested. It has a separate approval, rollback, and operational evidence gate.

## Required implementation discipline

1. Re-run `git status --short --branch` before every checkpoint and preserve all unrelated changes.
2. Follow strict vertical-slice TDD: one failing behavior test, observed RED, minimal implementation, observed GREEN, focused regression.
3. Stop only on a green checkpoint. Do not start a new RED without enough time to make it green and run the focused regression set.
4. Use the repository test wrappers:
   - focused backend: `./scripts/test-backend.sh tests/<file>.py -q`
   - full backend: `./scripts/test-backend.sh`
   - focused frontend: `npm run test:frontend -- frontend/src/<file>.test.js`
   - full frontend: `npm run test:frontend`
   - production build: `npm run build:isolated`
5. Commit only if Chris explicitly authorizes commits. Otherwise record `git diff --check`, focused test output, and `git status` at each checkpoint.
6. Never run the backfill with `--apply`, Alembic against a non-disposable database, `build:publish`, service restarts, or deployment commands without explicit runtime authorization.

---

## Phase 0 — establish the implementation baseline

### Task 0.1: Verify frozen inputs and worktree state

**Objective:** Prove implementation starts from the intended specification and repository baseline without disturbing unrelated work.

**Files:**
- Read: `docs/plans/2026-08-01-catalog-canonical-urls-and-short-links-spec.md`
- Read: `docs/plans/2026-08-02-catalog-canonical-urls-and-short-links-implementation-plan.md`
- Do not modify application files.

**Steps:**

1. Run:
   ```bash
   git status --short --branch
   git log -3 --oneline
   sha256sum docs/plans/2026-08-01-catalog-canonical-urls-and-short-links-spec.md \
     docs/plans/2026-08-02-catalog-canonical-urls-and-short-links-implementation-plan.md
   ```
2. Confirm the branch is `main`, HEAD is the expected handoff commit unless Chris has intentionally advanced it, and unrelated modified/untracked paths remain present.
3. If either plan hash differs from the resume prompt, inspect the diff before proceeding; do not silently implement against a moving specification.
4. Run baseline focused suites:
   ```bash
   ./scripts/test-backend.sh tests/test_catalog_favourites_api.py tests/test_catalog_symbol_detail.py -q
   npm run test:frontend -- frontend/src/catalogFavourites.test.js frontend/src/catalogWorkbench.test.js
   ```
5. Record any pre-existing failure separately. Do not classify it as a feature regression until reproduced from the established environment.

**Checkpoint:** Baseline recorded; no files changed.

---

## Phase 1 — additive canonical-identifier storage

### Task 1.1: Add failing model and migration contract tests

**Objective:** Define the additive 0026 schema before production code exists.

**Files:**
- Create: `tests/test_catalog_symbol_identifier_migration.py`
- Later create: `backend/alembic/versions/20260802_0026_catalog_symbol_identifiers.py`
- Later modify: `backend/symgov_backend/models/schema.py`
- Later modify: `backend/symgov_backend/models/__init__.py`

**Required schema contract:**

`catalog_symbol_identifiers`:
- `identifier TEXT PRIMARY KEY`;
- `role TEXT NOT NULL CHECK role IN ('canonical', 'historical_alias', 'tombstone')`;
- `governed_symbol_id UUID NULL REFERENCES governed_symbols(id) ON DELETE SET NULL`;
- `allocation_source TEXT NOT NULL CHECK allocation_source IN ('legacy_backfill', 'global_sequence', 'reviewed_correction')`;
- `allocated_at TIMESTAMPTZ NOT NULL`;
- `changed_at TIMESTAMPTZ NULL`;
- `changed_by UUID NULL REFERENCES users(id) ON DELETE SET NULL`;
- `change_reason TEXT NULL`;
- check: tombstones have no target; canonical/alias rows have a target;
- partial unique index: one active canonical row per governed symbol;
- check: identifier is already uppercase and matches `^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$`.

`governed_symbols`:
- nullable `catalog_symbol_id TEXT`;
- unique index on `catalog_symbol_id`;
- FK to `catalog_symbol_identifiers.identifier` with `ON DELETE RESTRICT`.

Allocator:
- PostgreSQL sequence `catalog_symbol_id_seq START 1 NO CYCLE`.

Cross-table consistency:
- a deferred constraint trigger verifies at transaction commit that a non-null `governed_symbols.catalog_symbol_id` points to a `canonical` registry row targeting the same governed symbol;
- a canonical registry row must be referenced by the same governed symbol;
- the trigger permits both rows to be created/changed within one transaction.

**Steps:**

1. Write tests asserting the migration revision/down-revision, table, sequence, constraints, indexes, trigger creation, safe downgrade policy, ORM class, field, and exports.
2. Run:
   ```bash
   ./scripts/test-backend.sh tests/test_catalog_symbol_identifier_migration.py -q
   ```
   Expected: RED because migration/model/export do not exist.
3. Do not add implementation in this task.

### Task 1.2: Implement additive migration 0026 and ORM mapping

**Objective:** Make the additive storage contract green without backfilling or enforcing publication completeness yet.

**Files:**
- Create: `backend/alembic/versions/20260802_0026_catalog_symbol_identifiers.py`
- Modify: `backend/symgov_backend/models/schema.py:305-316`
- Modify: `backend/symgov_backend/models/__init__.py`
- Test: `tests/test_catalog_symbol_identifier_migration.py`

**Implementation notes:**

- Name the ORM model `CatalogSymbolIdentifier`.
- Map `GovernedSymbol.catalog_symbol_id` as `Mapped[str | None]`.
- Do not assign default IDs in the ORM constructor.
- Keep the migration additive: existing rows remain nullable.
- Downgrade policy: drop the trigger/FK/column/sequence/table only when the registry and governed-symbol canonical column are empty. Refuse downgrade with a clear error when issued IDs exist; this prevents silent identity loss and later reuse.

**Steps:**

1. Implement the migration and ORM mapping only.
2. Run the exact RED test until GREEN:
   ```bash
   ./scripts/test-backend.sh tests/test_catalog_symbol_identifier_migration.py -q
   ```
3. Run model regressions:
   ```bash
   ./scripts/test-backend.sh tests/test_catalog_favourites_migration.py tests/test_catalog_feedback_model.py -q
   ```
4. Run `git diff --check`.

**Checkpoint A:** Additive schema represented in source; focused tests green; no database migrated.

---

## Phase 2 — normalization, allocation, and correction rules

### Task 2.1: Define identifier normalization with failing tests

**Objective:** Establish one strict canonical/alias grammar independent of UUID and slug parsing.

**Files:**
- Create: `tests/test_catalog_symbol_ids.py`
- Later create: `backend/symgov_backend/catalog_symbol_ids.py`

**Required API:**

```python
CATALOG_SYMBOL_ID_PATTERN = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$")

def normalize_catalog_symbol_id(value: object) -> str:
    """Return uppercase canonical form or raise ValueError for malformed input."""

def format_allocated_catalog_symbol_id(sequence_value: int) -> str:
    """Return S- plus a minimum six-digit decimal sequence."""
```

**Test cases:**
- `0003-12` remains `0003-12`;
- `s-000001` normalizes to `S-000001`;
- sequence 1 formats as `S-000001` and sequence 1000000 formats without truncation;
- reject leading/trailing whitespace, slash, backslash, `%`, `?`, `#`, control characters, Unicode lookalikes, leading/trailing hyphen, one character, and over 32 characters;
- booleans, `None`, numbers, and non-strings are rejected rather than stringified.

**Steps:** Write one test at a time, observe RED, implement minimally, observe GREEN, then run the whole file.

### Task 2.2: Allocate idempotently and concurrently

**Objective:** Assign one permanent `S-` ID to a governed symbol and preserve it across retries/republishing.

**Files:**
- Modify: `backend/symgov_backend/catalog_symbol_ids.py`
- Modify: `tests/test_catalog_symbol_ids.py`

**Required API:**

```python
def ensure_catalog_symbol_id(
    session: Session,
    symbol_id: uuid.UUID,
    *,
    allocated_at: datetime,
    allocation_source: str = "global_sequence",
) -> str:
    """Lock the governed symbol, return an existing ID, or atomically allocate one."""
```

**Behavior:**
- lock the governed-symbol row with `FOR UPDATE`;
- return an existing valid canonical ID unchanged;
- otherwise call `nextval('catalog_symbol_id_seq')`, format it, create the canonical registry row, and set `governed_symbols.catalog_symbol_id` in the same transaction;
- retry only a bounded unique-conflict race; never derive from package/page/slug;
- sequence gaps are accepted;
- no commit inside the service.

**Tests:** existing ID is unchanged; first allocation uses `S-000001`; two callers cannot assign different IDs; a failed transaction exposes no published row; sequence gaps are not reused.

**Focused command:**
```bash
./scripts/test-backend.sh tests/test_catalog_symbol_ids.py -q
```

### Task 2.3: Implement reviewed corrections and tombstones

**Objective:** Preserve old identifiers safely when correcting or retiring canonical identity.

**Files:**
- Modify: `backend/symgov_backend/catalog_symbol_ids.py`
- Modify: `tests/test_catalog_symbol_ids.py`

**Required API:**

```python
def correct_catalog_symbol_id(
    session: Session,
    symbol_id: uuid.UUID,
    new_identifier: str,
    *,
    actor_id: uuid.UUID,
    reason: str,
    preserve_old_link: bool,
    changed_at: datetime,
) -> str:
    """Replace canonical identity under lock; old value becomes same-symbol alias or tombstone."""
```

**Behavior:** require non-empty bounded reason and actor; reject collision with any registry value; old canonical becomes `historical_alias` to the same symbol when preserving links or `tombstone` with no target otherwise; never retarget an old ID to another symbol.

**Checkpoint B:** Pure identifier service green; no publication or routes changed.

---

## Phase 3 — audited backfill and migration rehearsal tooling

### Task 3.1: Define backfill inventory and collision reporting

**Objective:** Produce a deterministic report without changing data.

**Files:**
- Create: `backend/symgov_backend/catalog_symbol_backfill.py`
- Create: `tests/test_catalog_symbol_backfill.py`

**Candidate policy:**
- inspect every governed symbol and every currently published row;
- accept candidates only from explicit short-ID metadata: payload `package_display_id` + positive `package_symbol_sequence`, or explicit `symbol_display_id`/`workspace_display_name`/`display_name` values that pass the canonical grammar;
- do not use slug, page sort order, or pack fallback as an automatic candidate;
- normalize and group by governed-symbol UUID;
- classify `retain`, `missing`, `malformed`, `duplicate_across_symbols`, and `multiple_candidates_for_symbol`;
- no suffix repair and no automatic assignment for blocked rows.

**Report contract:**

```json
{
  "schemaVersion": 1,
  "summary": {},
  "symbols": [
    {
      "symbolId": "uuid",
      "published": true,
      "candidates": ["0003-12"],
      "classification": "retain"
    }
  ]
}
```

The report contains no prompts, credentials, personal data, or raw revision payloads.

**TDD command:**
```bash
./scripts/test-backend.sh tests/test_catalog_symbol_backfill.py -q
```

### Task 3.2: Define and validate reviewed mapping input

**Objective:** Require an explicit human-reviewed decision for every blocked published symbol.

**Files:**
- Modify: `backend/symgov_backend/catalog_symbol_backfill.py`
- Modify: `tests/test_catalog_symbol_backfill.py`

**Mapping contract:**

```json
{
  "schemaVersion": 1,
  "assignments": [
    {"symbolId": "uuid", "catalogSymbolId": "0003-12", "decision": "retain", "reason": "Unique existing ID"},
    {"symbolId": "uuid", "allocateNew": true, "decision": "allocate", "reason": "No safe legacy candidate"}
  ]
}
```

Validation rejects unknown/duplicate symbols, missing published symbols, malformed IDs, collisions, unexplained decisions, stale inventory fingerprints, and assignments inconsistent with unblocked retained candidates.

### Task 3.3: Add a dry-run-first management CLI

**Objective:** Make audit and application reproducible without making mutation the default.

**Files:**
- Create: `scripts/manage_catalog_symbol_ids.py`
- Create: `tests/test_manage_catalog_symbol_ids.py`
- Modify if needed: `backend/README.md`

**CLI:**

```text
python3 scripts/manage_catalog_symbol_ids.py audit --output <report.json> [--migration-db]
python3 scripts/manage_catalog_symbol_ids.py apply --mapping <mapping.json> --expected-inventory-sha256 <hash> [--migration-db]
```

**Safety:**
- `audit` is read-only and default-safe;
- `apply` requires explicit subcommand, mapping, and expected inventory hash;
- apply runs one transaction, takes an advisory lock, re-inventories under lock, aborts on drift, inserts registry rows, and updates governed symbols;
- apply prints counts and identifiers only, never database URLs or payloads;
- no production apply during implementation without explicit authorization.

**Focused command:**
```bash
./scripts/test-backend.sh tests/test_manage_catalog_symbol_ids.py tests/test_catalog_symbol_backfill.py -q
```

**Checkpoint C:** Backfill can be audited and applied to a disposable database; production remains untouched.

---

## Phase 4 — first-publication assignment and fail-closed invariant

### Task 4.1: Allocate before publication rows become visible

**Objective:** Ensure every newly published symbol receives an ID in the publication transaction.

**Files:**
- Modify: `backend/symgov_backend/runtime.py:2173-2234`
- Modify: `backend/symgov_backend/catalog_symbol_ids.py`
- Create: `tests/test_catalog_symbol_publication.py`
- Regression: `tests/test_f0_4_review_without_unpublication.py`
- Regression: `tests/test_publication_handoff_split_status.py`

**Implementation seam:** In `RuntimePersistenceBridge`, after loading the governed symbol and validating the revision, call `ensure_catalog_symbol_id(...)` before creating/updating `PublishedPage` or `PackEntry` and before setting `revision.lifecycle_state = 'published'`.

**Tests:** first publication allocates; republishing/revising keeps the ID; renaming/moving packs keeps it; two symbols receive different sequence IDs; forced later failure rolls back registry/field/publication together.

**Focused command:**
```bash
./scripts/test-backend.sh tests/test_catalog_symbol_publication.py tests/test_publication_handoff_split_status.py -q
```

### Task 4.2: Add the post-backfill publication invariant migration

**Objective:** Prevent any published state from committing without a canonical ID.

**Files:**
- Create: `backend/alembic/versions/20260802_0027_catalog_symbol_publication_invariant.py`
- Create: `tests/test_catalog_symbol_publication_invariant_migration.py`

**Migration behavior:**
- `down_revision = '20260802_0026'`;
- preflight query refuses upgrade if a currently published symbol lacks `catalog_symbol_id` or the registry relationship is inconsistent;
- deferred constraint triggers cover inserts/updates to `symbol_revisions`, `published_pages`, `pack_entries`, and canonical identifier relationships;
- downgrade removes only 0027 invariant triggers/check helpers; it does not delete IDs;
- upgrade/downgrade/re-upgrade is safe after backfill.

**Focused command:**
```bash
./scripts/test-backend.sh tests/test_catalog_symbol_identifier_migration.py tests/test_catalog_symbol_publication_invariant_migration.py tests/test_catalog_symbol_publication.py -q
```

**Checkpoint D:** Publication assignment and invariant green. Do not migrate a shared or production database.

---

## Phase 5 — one deterministic server-side resolver

### Task 5.1: Parse references safely

**Objective:** Reject malformed path references before lookup while preserving distinct canonical/UUID/slug parsing.

**Files:**
- Create: `backend/symgov_backend/catalog_symbol_resolution.py`
- Create: `tests/test_catalog_symbol_resolution.py`

**Required API:**

```python
@dataclass(frozen=True)
class ResolvedCatalogSymbol:
    symbol_id: uuid.UUID
    catalog_symbol_id: str
    matched_by: Literal["canonical", "uuid", "slug", "historical_alias", "page_code"]


def resolve_catalog_symbol(session: Session, raw_reference: str) -> ResolvedCatalogSymbol | None:
    ...
```

**Parsing:** one bounded decode; reject whitespace, residual `%`, slash/backslash, control/query/fragment markers, malformed encoding, and overlength; canonical/alias lookup uppercase; UUID comparison by parsed value; slug exact; page-code compatibility exact.

**Priority:** canonical, UUID, current slug, historical alias, recognized unique page code, not found. During backfill, register approved historical short IDs in the registry; do not scan arbitrary revision JSON indefinitely.

### Task 5.2: Refuse ambiguity and expose bounded telemetry

**Objective:** Never select an arbitrary symbol when compatibility inputs conflict.

**Files:**
- Modify: `backend/symgov_backend/catalog_symbol_resolution.py`
- Modify: `tests/test_catalog_symbol_resolution.py`

**Tests:** same-symbol aliases resolve; cross-symbol ambiguity returns no result/typed conflict; page code resolving to multiple governed symbols fails closed even if corrupted data bypasses uniqueness; telemetry records route family, match type, outcome, and canonical ID only—never full sensitive return URLs or raw payloads.

**Checkpoint E:** Resolver green in isolation.

---

## Phase 6 — published and integration API contracts

### Task 6.1: Return canonical identity and links from published rows

**Objective:** Make canonical identity authoritative in browser Catalog responses.

**Files:**
- Modify: `backend/symgov_backend/published_catalog.py:6-40`
- Modify: `backend/symgov_backend/routes/published.py:79-140`
- Modify: `tests/test_catalog_favourites_api.py`
- Create: `tests/test_published_catalog_canonical_ids.py`

**Changes:** select `gs.catalog_symbol_id`; return `catalogSymbolId`; set public `displayName` to the persisted canonical ID; retain `symbolId` UUID and `slug`; add deployment-valid `links.web` semantic data without hard-coding an aspirational clean absolute URL. A published row missing an ID is a server integrity error, never a slug/package fallback.

### Task 6.2: Route published detail through the shared resolver

**Objective:** Resolve canonical, UUID, slug, alias, and page-code references consistently.

**Files:**
- Modify: `backend/symgov_backend/routes/published.py:316-374,430-472`
- Modify: `tests/test_published_catalog_canonical_ids.py`

**Response contract:**
- 200: `{ "item": ..., "resolvedBy": "..." }`;
- malformed/unknown: 404 with bounded `code: catalog_symbol_not_found`;
- known canonical/alias but not currently published for an entitled user: 404 with `code: catalog_symbol_unavailable` and message `This Catalog symbol is not currently available.`;
- authentication failure remains 401 before lookup;
- resolver/database failure: 503 with retry-safe message;
- unauthorized/non-entitled callers receive the ordinary non-disclosing access response.

### Task 6.3: Replace mixed Catalog API lookup and links

**Objective:** Remove revision-JSON identity scanning from normal Catalog API resolution.

**Files:**
- Modify: `backend/symgov_backend/routes/catalog.py:389-527`
- Modify: `backend/symgov_backend/catalog_search.py` where `_catalog_symbol_ref` remains derived
- Modify: `tests/test_catalog_symbol_detail.py`
- Modify: `tests/test_catalog_symbol_search.py`
- Modify: `tests/test_catalog_contextual_search.py`
- Modify: `tests/test_catalog_symbol_download.py`
- Modify: `tests/test_catalog_feedback.py`

**Changes:** use `catalogSymbolId` for `displayId`, API links, preview links, citations, filenames, download receipts, usage `symbol_ref`, and feedback receipts. UUID and slug remain compatibility inputs. Remove `_published_symbol_ref_filter_sql()` JSON precedence after backfill; callers load the published row by resolved governed-symbol UUID.

### Task 6.4: Unify every published-symbol caller

**Objective:** Prevent sibling routes from retaining weaker lookup behavior.

**Files:**
- Modify: `backend/symgov_backend/routes/published.py`
- Modify: `backend/symgov_backend/routes/catalog.py`
- Modify: `backend/symgov_backend/services/published_feedback.py`
- Modify as required: preview, comments, favorites mutation, download, feedback, and command call paths
- Tests: existing Catalog/published focused files

**Tests:** canonical and compatibility refs work for detail, preview, comments, favorites add/remove, download, and feedback; malformed/ambiguous refs fail consistently; deleting a stale favorite by UUID still works for its authenticated owner without making unpublished content visible.

**Focused regression:**
```bash
./scripts/test-backend.sh \
  tests/test_catalog_symbol_detail.py \
  tests/test_catalog_symbol_search.py \
  tests/test_catalog_symbol_download.py \
  tests/test_catalog_feedback.py \
  tests/test_catalog_favourites_api.py -q
```

**Checkpoint F:** Backend contract complete and green.

---

## Phase 7 — safe internal return-to behavior

### Task 7.1: Extract and test safe destination helpers

**Objective:** Preserve pathname, query, and hash without accepting external return URLs.

**Files:**
- Create: `frontend/src/catalogRoutes.js`
- Create: `frontend/src/catalogRoutes.test.js`

**Required helpers:**

```javascript
export function internalDestinationFromLocation(location) { ... }
export function safeInternalDestination(value, fallback = '/standards') { ... }
export function routeForCatalogSymbol(catalogSymbolId) { ... }
export function absoluteHashRoute(origin, semanticPath) { ... }
```

**Safety tests:** preserve `/s/0003-12?x=1#detail`; reject absolute origins, `//host`, encoded schemes, backslashes, malformed escapes, controls, login loops, and non-object state; never decode/re-encode into a different origin.

### Task 7.2: Preserve destination through login

**Objective:** Make both login effect and submit handler resume exactly one validated internal destination.

**Files:**
- Modify: `frontend/src/App.jsx:377-390,486-515`
- Modify: `frontend/src/catalogRoutes.test.js`
- Create if component rendering is extracted: `frontend/src/AuthRoutes.js` and `frontend/src/AuthRoutes.test.js`

**Changes:** `RequireAuth` stores the full parsed internal destination; login derives one target before authentication; successful normal login navigates there; mandatory PIN login navigates to `/change-pin` while carrying the same target; remove the default-path navigation race.

### Task 7.3: Preserve destination through mandatory PIN change and logout safely

**Objective:** Resume the same destination after PIN change without creating a logout bypass.

**Files:**
- Modify: `frontend/src/App.jsx:604-668`
- Modify: auth route tests

**Tests:** exact symbol/favorites destination survives PIN change; malformed state falls back to `/standards`; logout clears auth and navigates to `/login` without replaying protected state.

**Focused command:**
```bash
npm run test:frontend -- frontend/src/catalogRoutes.test.js frontend/src/AuthRoutes.test.js
```

**Checkpoint G:** Auth return-to behavior green.

---

## Phase 8 — HashRouter symbol and favorites routes

### Task 8.1: Add route parsing and deployed URL builders

**Objective:** Make Release A generate only URLs that the deployed HashRouter can open.

**Files:**
- Modify: `frontend/src/catalogRoutes.js`
- Modify: `frontend/src/catalogRoutes.test.js`

**Tests:** semantic route is `/s/0003-12`; absolute Release A URL is `https://host/#/s/0003-12`; favorites is `https://host/#/favorites`; invalid/missing canonical ID cannot generate a link.

### Task 8.2: Add canonical symbol and favorites routes

**Objective:** Route `/#/s/:symbolRef` and `/#/favorites` into the existing Standards experience.

**Files:**
- Modify: `frontend/src/App.jsx:445-461,888-1146`
- Modify: `frontend/src/api.js:1187-1216`
- Create: `frontend/src/catalogDeepLinks.test.js`

**Changes:**
- add protected routes `/s/:symbolRef` and `/favorites`;
- add `fetchPublishedSymbol(symbolRef)` for exact server resolution;
- symbol route loads the exact target, then loads/binds the surrounding Catalog without ever selecting the first symbol as fallback;
- favorites route activates Favorites and clears query, column, facet, and other default filters;
- crafted `userId`, email, account, or owner parameters do not alter ownership and should be ignored for view state or rejected if introduced as an API contract;
- existing `/standards?symbol=` remains a compatibility entry.

### Task 8.3: Canonicalize legacy routes without adding history

**Objective:** Replace successful UUID/slug/page-code/`?symbol=` navigation with the canonical symbol route.

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/catalogDeepLinks.test.js`

**Tests:** successful alias resolution calls replace navigation to `/s/<catalogSymbolId>`; back/forward has no duplicate legacy entry; unknown and unavailable responses remain explicit; API error shows retry guidance; canonical route is not replaced unnecessarily.

### Task 8.4: Render distinct symbol and favorites states

**Objective:** Meet loading, empty, unavailable, offline, and authorization requirements.

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/catalogDeepLinks.test.js`

**Required copy:**
- malformed/unknown: `Catalog symbol not found.`
- recognized but unavailable: `This Catalog symbol is not currently available.`
- resolver/API failure: retry guidance;
- favorites empty: purposeful success state, not not-found;
- favorites offline and authorization states remain distinct.

**Checkpoint H:** HashRouter deep links and states green.

---

## Phase 9 — copy actions, metadata, and accessibility

### Task 9.1: Add clipboard helpers and failure handling

**Objective:** Copy only validated, absolute, deployment-valid URLs.

**Files:**
- Modify: `frontend/src/catalogRoutes.js`
- Modify: `frontend/src/catalogRoutes.test.js`

**Required helper:** accept an injected clipboard for testing; return success/failure without logging clipboard contents; use `window.location.origin` and HashRouter URL builders.

### Task 9.2: Add `Copy link` to symbol detail

**Objective:** Provide a keyboard-accessible symbol link action only when canonical identity is loaded.

**Files:**
- Modify: `frontend/src/App.jsx:1986-2055`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/catalogDeepLinks.test.js`

**Tests:** disabled/absent without `catalogSymbolId`; copies `/#/s/...`; success/failure announced through a persistent `role="status"`/`role="alert"`; no UUID/slug/package fallback; visible focus style; long hostnames wrap without overflow.

### Task 9.3: Add `Copy favorites link` and explanatory text

**Objective:** Explain that the link opens each recipient's own favorites.

**Files:**
- Modify: `frontend/src/App.jsx:1691-1810`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/catalogDeepLinks.test.js`

**Required text:** recipients must sign in and will see the favorites belonging to their own account. The copied URL is always `/#/favorites` in Release A.

### Task 9.4: Set document title without leaking private data

**Objective:** Make a resolved symbol tab identifiable without exposing user/favorite data.

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/catalogDeepLinks.test.js`

**Behavior:** title contains canonical ID and symbol name while selected; restores default on cleanup; favorites never puts favorite contents/user identity in title; do not emit clean-route canonical metadata in Release A.

**Focused frontend regression:**
```bash
npm run test:frontend -- \
  frontend/src/catalogRoutes.test.js \
  frontend/src/catalogDeepLinks.test.js \
  frontend/src/catalogFavourites.test.js \
  frontend/src/catalogFavouritesApi.test.js \
  frontend/src/catalogWorkbench.test.js
```

**Checkpoint I:** Release A frontend complete and green.

---

## Phase 10 — documentation, full gates, and disposable integration evidence

### Task 10.1: Update source-backed documentation

**Objective:** Document implemented source behavior without claiming deployment.

**Files:**
- Modify: `README.md`
- Modify: `backend/README.md`
- Modify: `docs/README.md`
- Modify: `docs/catalog-api/README.md`
- Modify: `docs/catalog-api/quickstart.md`

**Content:** canonical ID field/registry, retained legacy IDs, new `S-` allocator, compatibility policy, account-scoped favorites semantics, Release A hash URLs, and explicit statement that clean URLs/deployment are not yet proven.

### Task 10.2: Run complete source gates

**Commands:**

```bash
git diff --check
./scripts/test-backend.sh
npm run test:frontend
npm run build:isolated
```

Expected: all pass. If the full backend wrapper partitions external tests, report each partition separately rather than collapsing skipped external prerequisites into success.

### Task 10.3: Rehearse migrations on a disposable PostgreSQL database

**Prerequisite:** `SYMGOV_MIGRATION_DATABASE_URL` points to a disposable production-shaped PostgreSQL database. Never use a shared/production URL.

**Commands:**

```bash
cd backend
SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 alembic upgrade 20260802_0026
cd ..
PYTHONPATH=backend python3 scripts/manage_catalog_symbol_ids.py audit \
  --migration-db --output /tmp/catalog-symbol-id-inventory.json
# Create and review /tmp/catalog-symbol-id-mapping.json from the inventory.
PYTHONPATH=backend python3 scripts/manage_catalog_symbol_ids.py apply \
  --migration-db \
  --mapping /tmp/catalog-symbol-id-mapping.json \
  --expected-inventory-sha256 <reviewed-hash>
cd backend
SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 alembic upgrade 20260802_0027
SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 alembic downgrade 20260802_0026
SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 alembic upgrade 20260802_0027
```

Then prove:
- one canonical ID per published governed symbol;
- one governed symbol per canonical ID;
- no duplicate registry values;
- aliases target only their original symbol;
- tombstones resolve nowhere;
- 0026 downgrade refuses once issued IDs exist;
- no mapping/report includes secrets or raw payloads.

### Task 10.4: Perform authenticated two-account application smoke on a disposable environment

**Tests:**
- same `/#/favorites` URL shows each account's own set;
- user/account query/body values cannot retarget it;
- logged-out symbol and favorites links resume after login;
- mandatory PIN change resumes the exact destination;
- canonical, UUID, slug, page-code, and `?symbol=` compatibility behave as specified;
- unknown/unavailable/API-failure states are distinct;
- copied links open on direct navigation and refresh under static HashRouter hosting;
- logs contain no PIN, cookie, authorization header, clipboard content, favorite list, or full sensitive return URL.

### Task 10.5: Release A completion checkpoint

Record:
- HEAD and worktree status;
- focused RED→GREEN ledger;
- full backend/frontend/build results;
- migration rehearsal database identity (redacted) and commands;
- uncompleted operational gates;
- no push/deploy/migrate/restart performed unless separately authorized.

Do not begin Release B automatically.

---

## Release B — separately authorized clean-route migration

### Task B1: Identify and version the production web-serving configuration

**Objective:** Locate the real Nginx/Traefik/static-host configuration before changing frontend router mode.

**Read-only discovery:** inspect deployment repository/host configuration, served build marker, `/api`, health, assets, genuine 404 behavior, cache headers, and rollback mechanism. The current Symgov repository does not contain enough production web-server configuration to authorize a change.

**Deliverable:** a reviewed config path/owner, route allowlist, fallback rule, rollback artifact, and production-like test environment. Stop if any is unknown.

### Task B2: Write failing production-like fallback tests

**Required cases:** direct GET and refresh for `/s/0003-12` and `/favorites` serve the SPA entry; `/api/...`, `/assets/...`, health, and unknown non-SPA paths are not swallowed; cache behavior is correct; rollback restores HashRouter build/config.

### Task B3: Change BrowserRouter and generated-link mode

**Files:**
- Modify: `frontend/src/main.jsx`
- Modify: `frontend/src/catalogRoutes.js`
- Modify: frontend route tests
- Modify: the verified deployment config path from B1

**TDD:** change expected copied URLs from `/#/...` to clean paths only after fallback tests are RED for the intended reason; implement minimal router/config change; run focused and full gates.

### Task B4: Deploy and verify only with explicit authorization

Deployment requires separate approval for migration/static publication/service reload. Verify external direct navigation, refresh, API/assets/health exclusions, back/forward, login/PIN return, copied links, build marker, cache, and rollback. Preserve deployment evidence separately from source test evidence.

---

## Final acceptance checklist

- [ ] Every published symbol has one persisted canonical ID.
- [ ] Existing valid IDs retained; new IDs use `S-` sequence.
- [ ] IDs survive rename, revision, republish, and pack/page movement.
- [ ] Corrections preserve same-symbol aliases or tombstones; no reuse.
- [ ] All symbol consumers use one deterministic resolver.
- [ ] Published/API responses expose `catalogSymbolId`, UUID, slug, and valid web link data.
- [ ] Login and mandatory PIN change preserve exact safe internal destination.
- [ ] `/#/s/<id>` and `/#/favorites` work in Release A.
- [ ] Favorites remain account-scoped and cannot be retargeted.
- [ ] Copy actions are accessible and never fall back to noncanonical identity.
- [ ] Unknown, unavailable, unauthorized, empty, offline, and resolver-failure states are distinct.
- [ ] Focused tests, full backend, full frontend, and isolated build pass.
- [ ] Migration upgrade/downgrade/re-upgrade rehearsed on disposable PostgreSQL.
- [ ] No production mutation/deployment occurred without authorization.
- [ ] Clean routes remain gated until Release B topology and rollback are verified.
