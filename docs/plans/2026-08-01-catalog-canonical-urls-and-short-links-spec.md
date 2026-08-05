# Catalog Canonical URLs and Extensible Short Links Specification

**Status:** Implementation-ready product specification

**Date:** 2026-08-01

**Purpose:** Define authenticated, short, readable URLs for a single Catalog symbol and for the current user's favorite symbol set, while establishing a safe path to a reusable short-link facility.

## 1. Product decisions

1. A Catalog symbol receives a persisted short Catalog symbol ID. This is the canonical human-facing identifier for the symbol.
2. A symbol's name and slug are descriptive metadata, not canonical identity.
3. The canonical single-symbol URL uses the short Catalog symbol ID.
4. The favorites URL uses American spelling: `favorites`.
5. The favorites destination always means the favorite set of the currently authenticated user. No user identifier is embedded in the URL.
6. A URL is a locator, not a credential. Authentication, subscription, role, publication, and other authorization checks still apply after resolution.
7. Semantic URLs are preferred for the initial feature. The `/go/<code>` namespace is reserved for future links whose destinations cannot be represented cleanly and safely in a semantic URL.

## 2. User outcomes

### 2.1 Single symbol

A user can copy or generate a short URL for a published Catalog symbol and send or retain it. Opening the URL:

- checks the user's existing Symgov session;
- requests login if necessary;
- preserves the complete destination through login and mandatory PIN change;
- opens the Catalog with the requested symbol selected;
- shows an explicit unavailable/not-found state if the symbol is not currently available to that user.

### 2.2 Favorites

A user can copy or generate one stable URL that opens their current favorite symbol set. Opening the URL:

- checks the user's existing Symgov session;
- requests login if necessary;
- opens the favorite set belonging to the account that authenticated;
- reflects the live favorite set rather than a snapshot taken when the URL was generated;
- shows a purposeful empty state when the user has no favorites.

The URL is intentionally the same shape for every account. User specificity comes from the authenticated principal, not from a user ID, email address, token, or opaque owner reference in the URL.

## 3. Canonical URL design

### 3.1 Clean canonical URLs

Preferred externally visible routes:

- Single symbol: `/s/<catalog-symbol-id>`
- Current user's favorites: `/favorites`
- Reserved future short links: `/go/<code>`

Examples:

- `https://<symgov-host>/s/0003-12`
- `https://<symgov-host>/favorites`
- Future: `https://<symgov-host>/go/K7m2Qa`

These paths are deliberately short and readable. `/s/` is an allowlisted route family for Catalog symbols, not a general redirect mechanism.

### 3.2 Current HashRouter compatibility

The frontend currently uses `HashRouter`. Until clean-path SPA fallback is implemented and verified, the first-release shareable routes are:

- `https://<symgov-host>/#/s/0003-12`
- `https://<symgov-host>/#/favorites`

The intended product outcome is the clean form without `#`. Delivery is split into two releases:

1. The first release implements canonical identity, exact authenticated return-to behavior, symbol/favorites route semantics, canonicalization, and copy-link behavior using the existing HashRouter-compatible forms.
2. A separate routing release changes to clean BrowserRouter paths only after the production web topology has an approved SPA fallback for the allowlisted frontend routes.

The clean-route release must configure the production web server to serve the SPA entry document for `/s/<catalog-symbol-id>`, `/favorites`, and other explicitly approved frontend routes while preserving static asset, API, health, and genuine 404 behavior. Its implementation plan must include direct-navigation, refresh, rollback, cache, API/static-path exclusion, and served-build identity tests before changing router mode. Until that release is deployed and verified, generated links use the HashRouter-compatible form and must not claim that an unverified clean path is live.

### 3.3 Canonicalization

- The browser's canonical symbol route contains the normalized Catalog symbol ID.
- Legacy URLs containing a governed-symbol UUID, slug, old `?symbol=` query, page code, or recognized historical symbol reference may continue to resolve during a compatibility period.
- After successful legacy resolution, the frontend replaces the URL with the canonical semantic route without adding a history entry: `/#/s/<catalog-symbol-id>` in the first HashRouter release and `/s/<catalog-symbol-id>` after the clean-route release.
- Ambiguous legacy references must never select an arbitrary symbol.
- Unknown, malformed, withdrawn, unpublished, or unauthorized references must fail explicitly and without leaking whether a protected symbol exists.

## 4. Canonical Catalog symbol ID

### 4.1 Domain definition

Add a first-class persisted identifier named `catalog_symbol_id` to the governed symbol domain.

Required properties:

- globally unique within Symgov;
- stable across symbol revisions, renaming, recategorization, republishing, and movement between publication packs or pages;
- immutable after assignment except through a separately authorized data-correction procedure;
- never silently regenerated from the current pack, page, sort order, name, or slug;
- never reused for a different governed symbol, including after withdrawal or deprecation;
- safe and readable in a URL path segment;
- compared using a single documented normalization rule.

The UUID remains the internal relational primary key. `catalog_symbol_id` is the canonical external and human-facing identity.

### 4.2 Format

Initial accepted format:

- 2 to 32 characters;
- ASCII uppercase letters, digits, and internal hyphens;
- starts and ends with an alphanumeric character;
- normalized to uppercase before persistence and comparison;
- no whitespace, slash, query marker, fragment marker, percent escape, control character, or Unicode confusable.

Proposed validation expression:

`^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$`

Existing IDs such as `0003-12` conform. Once assigned, every ID is independent of subsequent package membership.

Resolved namespace policy:

- Preserve an existing package-shaped ID such as `0003-12` when the backfill proves that it is valid and maps unambiguously to exactly one governed symbol.
- Treat a retained package-shaped value as opaque historical identity after persistence. Its parts no longer carry mutable package, page, sequence, or sort-order semantics.
- Allocate new IDs from a dedicated global database sequence in the form `S-000001`, increasing the decimal portion without reuse. The numeric portion starts at six digits for readability but may grow beyond six digits without changing identity or requiring renumbering.
- Reserve the `S-` prefix for this allocator. Retained legacy IDs and `S-` IDs share one normalized global uniqueness domain.
- The source-package allocator and package-local symbol sequence are not authorized canonical-ID allocators.

### 4.3 Current-state correction

The current displayed short ID is derived by `published_symbol_display_id()` from revision payload values such as `package_display_id` and `package_symbol_sequence`, with fallbacks to publication pack code and sort order. That is suitable as a migration candidate but not as permanent identity because publication structure can change and a symbol can participate in publication records independently of its governed identity.

The current `governed_symbols.slug` column is database-unique, but it remains name-like metadata. Its uniqueness does not make it the desired permanent Catalog ID.

### 4.4 Persistence and constraints

Required model:

- `governed_symbols.catalog_symbol_id TEXT`;
- unique index on normalized value, using the same case-normalization rule as application validation;
- nullable for pre-Catalog drafts and during migration;
- required before a symbol can enter or remain in the published Catalog;
- application and publication-boundary checks that fail closed when a published symbol lacks a canonical ID.

Add one dedicated identifier registry that is authoritative for allocation, aliases, and permanent non-reuse. It records every normalized identifier once, its role (`canonical`, `historical_alias`, or `tombstone`), its governed-symbol assignment where resolvable, allocation source (`legacy_backfill`, `global_sequence`, or `reviewed_correction`), allocation time, and retirement/correction audit state. A governed symbol has exactly one active canonical registry entry and may have multiple historical aliases. A registry value cannot be canonical for one symbol and an alias for another. A tombstone has no resolvable target. The registry must not contain a mutable redirect path or external URL.

`governed_symbols.catalog_symbol_id` is a unique reference to that symbol's active canonical registry entry. Database constraints, with a deferred constraint trigger where cross-table enforcement is required, must ensure that the registry target and governed-symbol reference agree at transaction commit. A correction transaction assigns a newly reviewed canonical ID and changes the old ID to a historical alias for the same governed symbol when old links must survive; it never deletes or reallocates the old registry value.

Allocate a new canonical ID transactionally at the first publication boundary, before any published pack/page/entry state can become visible. Approval alone does not require allocation. A failed publication transaction must not expose a symbol without an ID; sequence gaps are acceptable and must never be filled by reusing an issued value. IDs must not be allocated by frontend code.

Once an ID has been allocated, the governed symbol must normally be withdrawn or soft-deleted rather than hard-deleted. Any separately authorized hard deletion must leave every issued registry value as a permanent tombstone. A reviewed data correction changes the old canonical value to a historical alias for the same symbol when old links must survive, or to a tombstone when resolution must stop. A retired value is never returned to the allocator.

### 4.5 Backfill and collision handling

Before adding a non-null publication invariant:

1. Inventory every governed and published symbol.
2. Derive candidate IDs from existing authoritative short-ID metadata only where the result is unambiguous.
3. Normalize candidates and report duplicates, malformed values, missing values, and one-symbol/multiple-candidate conflicts.
4. Do not resolve collisions automatically by appending a suffix.
5. Require a reviewed mapping for every collision or missing published ID.
6. Apply the mapping transactionally.
7. Verify one canonical ID per published symbol and one symbol per canonical ID.
8. Preserve the mapping as redacted deployment evidence.

Valid, unambiguous existing IDs are retained unchanged. Symbols are not renumbered merely to make the namespace visually uniform. Missing, malformed, duplicate, or conflicting candidates require a reviewed mapping; unresolved published rows block migration completion. New `S-` IDs may be assigned through the reviewed mapping where no safe legacy candidate exists.

The migration must be rehearsed against a production-shaped disposable database and must have an explicit downgrade/rollback policy. A downgrade must not cause already-issued canonical IDs to be reassigned later.

## 5. Symbol resolution contract

Create one deterministic server-side resolver used by published Catalog UI routes, Catalog APIs, favorites mutations, comments, previews, downloads, and future short links.

Resolution is type-aware rather than applying one destructive normalization to every reference:

- trim no input implicitly; leading or trailing whitespace is malformed;
- for a canonical-ID or registered-alias lookup, require the ASCII identifier grammar and normalize letters to uppercase;
- for UUID compatibility, parse the complete segment as a UUID and compare by UUID value;
- for current-slug compatibility, compare the complete decoded segment using the existing exact slug contract;
- perform one bounded percent decode, then reject residual percent escapes, slash, backslash, control characters, query markers, fragment markers, malformed encoding, or overlength input.

Resolution priority:

1. exact canonical `catalog_symbol_id`;
2. governed-symbol UUID for compatibility;
3. exact current slug for compatibility;
4. exact explicitly registered historical alias;
5. otherwise not found.

Derived display values from arbitrary revision JSON must not remain an indefinite competing identity source after migration. Compatibility resolution must be bounded, indexed where possible, deterministic, and observable.

The first release uses the indexed identifier registry for historical corrected canonical IDs and explicitly approved compatibility references. Every normalized alias maps to at most one governed symbol, occupies the same permanent global namespace as canonical IDs, records its alias type and audit attribution, and cannot target a free-form path. An old canonical ID may change role to a historical alias for the same symbol but can never target a different symbol. Resolver startup/migration checks fail closed on ambiguity. Arbitrary revision JSON values are not automatically inserted as aliases.

The resolver returns the governed symbol UUID and canonical Catalog symbol ID. Callers then apply their own publication, entitlement, role, asset, and mutation rules.

API responses should expose:

- `catalogSymbolId`: canonical short ID;
- `symbolId`: internal UUID where the existing contract requires it;
- `slug`: descriptive current slug;
- `links.web`: deployment-valid canonical web URL where appropriate—HashRouter-compatible in the first release and clean `/s/<catalog-symbol-id>` only after the clean-route deployment is verified.

Existing `displayName` behavior should be reconciled explicitly. The UI may display `catalogSymbolId`, but contracts must not maintain multiple fields that claim to be canonical while deriving different values.

Legacy UUID, exact current slug, recognized page-code links, and existing `?symbol=` navigation remain compatibility inputs for at least 12 months after clean-route general availability. Successful compatibility resolution replaces the browser URL with the canonical route without adding history. Compatibility use is counted by reference type without logging sensitive return destinations. Retirement requires an announced release, migration guidance, and at least 90 consecutive days of zero observed production use for the reference type. UUID resolution may remain longer for internal API compatibility. Ambiguous page codes or aliases fail closed and are never guessed.

## 6. Authentication and return-to behavior

### 6.1 Requirement

Authentication redirects must preserve the complete internal destination:

- pathname;
- query string;
- hash/router state where still applicable.

Current frontend behavior retains only `location.state.from.pathname` in one path and initially navigates to the default user page after login. This loses symbol query parameters and must be corrected.

### 6.2 Safety

- Store and replay only a parsed internal route, never an arbitrary absolute return URL.
- Reject protocol-relative paths, external origins, encoded scheme changes, backslashes, and malformed destinations.
- Mandatory PIN change must preserve the same safe destination and resume it after successful completion.
- Logout must not create an authenticated return bypass.
- Another user's active browser session may open `/favorites`, but it must show that active account's favorites; the URL carries no original-owner authority.

## 7. Favorites route behavior

Canonical route: `/favorites`.

Rules:

- Requires an authenticated account and applicable Catalog entitlement.
- Uses exactly the ordinary Standards View entitlement in force for direct Catalog browsing; possession of the URL grants no additional access. The first release does not introduce a new Plus-only gate because the current published Catalog contract requires an active authenticated account but has no separate subscription check. A later subscription-policy change must apply consistently to ordinary Catalog browsing, symbol links, and favorites links rather than being implemented only in deep-link resolution.
- Loads account-scoped favorite state using the authenticated principal only.
- Must not accept `userId`, email, account ID, or owner override in path, query, fragment, body, or client state.
- Uses the American spelling `favorites` in URLs and route names.
- Existing database/API/internal British spelling may remain temporarily where changing it would create unnecessary migration risk, but new public contracts should use `favorites` unless compatibility requires aliases.
- Opens the Catalog with the Favorites filter active and other default filters cleared unless a later saved-view specification says otherwise.
- Reflects additions/removals on reload.
- Presents distinct loading, empty, live, offline, and authorization states.
- An empty result is a valid success, not a not-found response.

Compatibility alias:

- If a `/favourites` route is ever released or already externally used, redirect or replace it with `/favorites` without retaining duplicate canonical URLs.

## 8. Link generation and UI

### 8.1 Single symbol

Add a keyboard-accessible `Copy link` action in the selected symbol detail. It copies the absolute deployment-valid canonical symbol URL: `/#/s/<catalog-symbol-id>` in the first release and `/s/<catalog-symbol-id>` after the clean-route release.

The action is available only after the canonical ID has been loaded and validated. It must not fall back to a UUID, slug, or derived package value while claiming to have copied the canonical link.

### 8.2 Favorites

Add a keyboard-accessible `Copy favorites link` action when the Favorites view is active and in an appropriate Catalog/workbench location. The copied URL is the absolute deployment-valid favorites URL: `/#/favorites` in the first release and `/favorites` after the clean-route release.

The UI should explain briefly that recipients must sign in and will see the favorites belonging to their own account. This avoids implying that the link shares the sender's favorite set.

### 8.3 Browser metadata

For a successfully resolved symbol:

- document title includes the canonical ID and symbol name;
- canonical link metadata uses the verified clean `/s/<catalog-symbol-id>` URL after the clean-route release; the HashRouter release does not emit misleading clean-route canonical metadata;
- no private user or favorite data is placed in title, URL, referrer metadata, or analytics labels.

## 9. Extensible `/go/<code>` mechanism

### 9.1 Initial scope

Reserve `/go/<code>` but do not require stored short links for `/s/<catalog-symbol-id>` or `/favorites`; those semantic routes are already shorter and more readable.

A stored short-link service should be introduced only when required for destinations such as:

- a saved Catalog view whose filters are too large for a semantic URL;
- a user-owned workflow continuation;
- a revocable or expiring internal link;
- a stable alias for a destination whose internal route changes;
- a controlled campaign or notification link requiring audited use.

### 9.2 Future data contract

A future short-link record should include:

- random non-sequential `code` with sufficient entropy;
- allowlisted `target_type`;
- validated versioned `target_payload`;
- optional owner user ID;
- explicit access policy;
- created by/at;
- optional expiry;
- revoked at/by/reason;
- bounded usage count and last-used timestamp;
- audit events for creation, revocation, and resolution outcome.

### 9.3 Security constraints

- Codes are locators, not bearer authentication.
- Do not store or resolve arbitrary external URLs.
- Do not accept free-form internal paths as target payloads.
- Authenticate and authorize after target resolution and before protected data access.
- Use 404-style non-disclosure for links restricted to another owner.
- Rate-limit enumeration attempts and use non-sequential, high-entropy codes.
- Refuse redirects to a different origin.
- Never put credentials, session tokens, personal identifiers, or sensitive filter values in codes, target payloads, logs, analytics, or referrer-visible query strings.
- Expired, revoked, malformed, unknown, unavailable, and unauthorized targets fail safely.

## 10. Error and lifecycle behavior

### 10.1 Symbol states

- Published and authorized: open the symbol.
- Valid canonical ID, symbol withdrawn/unpublished: show `This Catalog symbol is not currently available.`
- Unknown/malformed reference: show `Catalog symbol not found.`
- Unauthenticated: login, then resume exact destination.
- Authenticated but not entitled: existing access-denied/subscription journey.
- Resolver/API unavailable: fail soft with retry guidance; do not silently open the first Catalog symbol.

Responses and UI must avoid distinguishing protected existence where the user lacks access.

### 10.2 Identifier lifecycle

- Renaming a symbol does not change `catalog_symbol_id` or its URL.
- Revising or republishing does not change it.
- Moving between packs/pages does not change it.
- Withdrawal does not release it.
- Merge/supersession workflows require an explicit future policy. They must not silently retarget an old canonical ID to a semantically different symbol.
- Data correction requires audit evidence and, if old links must survive, an explicit non-conflicting alias record.
- An allocated ID survives governed-symbol withdrawal, soft deletion, and any separately authorized hard deletion through its permanent registry tombstone.
- Pre-publication drafts that have never received a canonical ID may be hard-deleted under the ordinary governance policy without consuming an ID.

## 11. Accessibility, privacy, and observability

- Link actions are operable by keyboard and expose clear accessible names and status announcements.
- Copy success/failure is communicated without relying on color alone.
- Mobile layouts do not overflow because of long hostnames or IDs.
- Logs may include canonical Catalog symbol ID, route family, outcome, and authenticated actor ID under existing privacy policy.
- Logs must not include PINs, session cookies, authorization headers, copied clipboard contents, personal favorite lists, or full sensitive return URLs.
- Favorite link use must not enumerate or expose another account's favorites.
- Cache protected symbol and favorites responses according to existing authenticated Catalog policy; favorites responses should be private/no-store unless a separately reviewed cache design proves otherwise.

## 12. Acceptance criteria

### 12.1 Canonical identity

- Every published Catalog symbol has exactly one persisted canonical `catalog_symbol_id`.
- Canonical IDs are globally unique under the documented normalization rule.
- A canonical ID survives revision, rename, and republishing tests unchanged.
- Duplicate, malformed, and missing IDs block publication or migration completion.
- The current package/sequence-derived value is no longer treated as canonical unless it was explicitly persisted through the reviewed backfill.
- Valid unambiguous legacy IDs are retained; newly allocated IDs use the reserved global `S-` sequence.
- First publication allocates an ID transactionally before published state is visible.
- Withdrawn, deleted, corrected, and retired IDs remain reserved permanently.

### 12.2 Single-symbol URL

- Opening the deployed canonical symbol URL—`/#/s/<catalog-symbol-id>` in the first release and `/s/<catalog-symbol-id>` after the clean-route release—while logged in opens exactly that published symbol.
- Opening it while logged out returns to the exact symbol after login.
- Mandatory PIN change also preserves the destination.
- Legacy UUID, slug, and existing `?symbol=` links resolve deterministically and canonicalize where supported.
- Unknown, ambiguous, withdrawn, and unauthorized references produce the specified safe states.
- `Copy link` copies the absolute canonical URL.

### 12.3 Favorites URL

- Opening the deployed favorites URL—`/#/favorites` in the first release and `/favorites` after the clean-route release—while logged in shows only that account's live favorite set.
- Opening it while logged out returns to that exact deployed favorites URL after login.
- Two users opening the same URL see their own isolated favorite sets.
- Crafted user/account parameters cannot retarget the route.
- Empty favorites display a valid empty state.
- The canonical route and copied URL use `favorites`, not `favourites`.

### 12.4 Routing and deployment

- Direct navigation and browser refresh work for first-release HashRouter URLs without a server fallback change.
- As a gate before the later clean-route release, direct navigation and browser refresh work for every canonical clean route in production-like web serving.
- API and static asset paths are not swallowed by the clean-route SPA fallback.
- Back/forward navigation, canonical replacement, and query preservation are tested.
- Deployment includes web-server configuration validation and rollback evidence if router mode changes.
- The first HashRouter-compatible release and the later clean BrowserRouter migration have separate completion gates and rollback evidence.
- Generated links reflect the router mode actually deployed and verified; they do not emit unverified clean paths.

## 13. Test strategy

Backend focused tests:

- model and migration constraints;
- normalization and validation;
- canonical resolver priority and collision refusal;
- publication invariant;
- favorite account isolation;
- unauthenticated, unauthorized, unavailable, and withdrawn behavior;
- legacy compatibility and canonical response links;
- migration backfill rehearsal and downgrade policy;
- dedicated sequence allocation, concurrency, sequence-gap, registry tombstone, and permanent non-reuse behavior;
- first-publication transactional invariant and rollback;
- retained legacy ID plus new `S-` namespace coexistence;
- alias uniqueness, canonical-ID collision refusal, and ambiguous page-code failure.

Frontend focused tests:

- route parsing and canonicalization;
- exact return-to preservation through login and PIN change;
- symbol and favorites loading/error/empty states;
- copy-link behavior and clipboard failure;
- no user override accepted;
- keyboard, focus, live-region, and mobile overflow behavior;
- HashRouter-to-clean-router compatibility in the separate clean-route release;
- generated URL shape for the currently deployed router mode.

Broader verification:

- full backend suite;
- full frontend tests and production build;
- migration upgrade/downgrade/re-upgrade rehearsal;
- production-shaped direct-navigation and refresh tests;
- authenticated smoke tests with two isolated accounts;
- secret/PII-safe log inspection;
- web-server configuration validation and rollback rehearsal.

## 14. Explicit exclusions from the first implementation

- Public anonymous symbol access.
- Sharing one user's favorite set with another user.
- Frozen favorite snapshots.
- User IDs or emails in URLs.
- Bearer-access links.
- Arbitrary URL shortening or external redirects.
- Expiring invitation links.
- Saved filter/view sharing.
- Automatic canonical-ID collision repair.
- Changing a canonical ID merely because a symbol's name, slug, pack, page, or order changes.

## 15. Resolved product decisions

1. Allocate canonical IDs transactionally at first publication, before published state is visible.
2. Preserve valid unambiguous existing IDs such as `0003-12`; allocate new IDs from the dedicated global `S-000001` sequence.
3. Use a first-release canonical-ID registry plus alias/tombstone records to guarantee global uniqueness, safe correction, and permanent non-reuse.
4. Do not hard-delete governed symbols after ID allocation in ordinary workflows. Any exceptional authorized deletion leaves a permanent tombstone.
5. Deliver HashRouter-compatible deep links first. Deliver clean BrowserRouter paths as a separate production-routing migration with its own verification and rollback gate.
6. Support legacy UUID, current slug, recognized page-code, and `?symbol=` links for at least 12 months after clean-route general availability, followed by an announced and telemetry-gated retirement.
7. Apply the ordinary Standards View authentication and entitlement policy. Deep links confer no access and do not introduce a separate Plus gate.
8. Include explicit historical aliases in the first release rather than waiting for the first correction.

## 16. Repository evidence informing this draft

- `frontend/src/main.jsx`: current `HashRouter`.
- `frontend/src/App.jsx`: existing `/standards` route, `?symbol=` selection, favorites filter, and incomplete login return-path preservation.
- `backend/symgov_backend/models/schema.py`: UUID primary key and unique slug on `GovernedSymbol`; publication pack/page/entry cardinality.
- `backend/symgov_backend/published_catalog.py`: current derived display ID behavior.
- `backend/symgov_backend/routes/published.py`: authenticated published symbols and account-scoped favorites.
- `backend/symgov_backend/routes/catalog.py`: current mixed symbol-reference resolution for Catalog API routes.
- `backend/alembic/versions/20260718_0022_catalog_favourites.py`: account-scoped favorite persistence.
- `tests/test_catalog_favourites_api.py` and frontend favorite tests: current isolation and authenticated-session contracts.
