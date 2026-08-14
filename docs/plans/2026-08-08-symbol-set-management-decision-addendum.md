# Symbol Set Management — Stage 0 decision addendum

Status: ACCEPTED
Date: 2026-08-08
Accepted: 2026-08-08T19:08:02Z
Authority: Chris Brighouse, CEO of Symgov

This is a dated addendum to `docs/Symbol Set Management Spec v0.3.md`. It does not rewrite the v0.3 draft or the historical implementation plan. It records the implementation contracts that must be accepted before organization implementation starts.

## Controlling sources

- Product draft: `docs/Symbol Set Management Spec v0.3.md`
- Product-draft SHA-256 at drafting time: `42c240782a4732438a24a53d7ae80eefa6a78282601a1c4a91d19d86254a1344`
- Master plan: `docs/plans/2026-08-08-symbol-set-management-implementation-plan.md`
- Master-plan SHA-256 at drafting time: `e69682310400c56af8b0633d01e57cbc3fa913b08a37485665ea0d5448dba283`
- Repository baseline inspected: `main` and `origin/main`, `a18d5b3587ebb11c95f45ca16643efe94b322c61`
- Current Alembic head: `20260802_0026`

## Acceptance rule

The recommended contracts below are the proposed Stage 0 defaults. Chris may accept all of them as one decision, or amend individual IDs in this file. Until the status changes to `ACCEPTED`, they are not implementation authority and Stage 1 organization implementation must not begin.

The specification's resolved product decisions D1–D19 remain distinct from this implementation decision register. O1–O6 are recorded as explicit deferrals/recommendations rather than silently decided during implementation.

## Implementation decision register

| ID | Proposed accepted contract |
|---|---|
| I-01 | In v1, only an active Platform Admin may create an organization. Creation atomically assigns one nominated existing active user as the first Organization Admin. Invitations and self-service organization creation are out of scope; membership rows retain nullable invitation/activation timestamps for future use. |
| I-02 | `normalized_code` is globally unique and immutable. Commercial entitlement is manually administered as `active` or `suspended`; billing, seats, and organization subscription purchase are out of scope. Organization display/legal names are normalized for duplicate detection, but same-name legal entities remain possible when their immutable codes disambiguate them. The exact normalization helper and duplicate-warning/error response are implementation details that must be covered by tests and must not weaken code uniqueness. |
| I-03 | Existing global roles remain intact. Organization roles are independent, and `platform_admin` is a separate platform role. No role is inferred from another. Chris is bootstrapped explicitly into required roles; email is never an authorization shortcut. |
| I-04 | Platform Admin authority is effective only in a session bound to the reserved Symgov organization, for an active Symgov Organization Admin who holds an active `platform_admin` assignment. Personal or other-organization sessions never activate it. Sensitive mutations require recent session-bound step-up authentication. |
| I-05 | Preserve existing `POST /auth/login` compatibility. The v0.3 `/auth/session` shape is delivered as a versioned API surface only when its exact compatibility consumer is established; it is not an unreviewed replacement. With zero eligible organizations, issue a personal full session; with one, issue one organization-bound full session; with several, issue only a short-lived opaque selection challenge; an unassigned user receives personal mode. The challenge is consumed by the accepted selection route and never represents application authority. |
| I-06 | `active_organization_id` and session mode are immutable after full session creation. Changing organization requires sign-out/revocation and fresh authentication; there is no in-session organization switch. |
| I-07 | Project context is mutable only inside a bound organization session and is server-validated. Persist one active-set preference per `(user, project)`. Resolve explicit eligible set, user preference, project default, organization default, then no active set. Browser storage is not authority. |
| I-08 | Retain base Organization Admin/User roles and add explicit `contributor` and `symbol_reviewer` capabilities. Organization Admin does not automatically become a reviewer, but an admin may be appointed as reviewer subject to last-admin/platform-admin protections. Global roles are not tenant authority. |
| I-09 | Use `organization_wide` separately from `visibility`. Organization-private symbols are either set-only or organization-wide; set-only private symbols are returned only through eligible set membership. |
| I-10 | Organization approval is revision-specific and uses a dedicated submission/decision history. It does not overload public `SymbolRevision.lifecycle_state` or public `ReviewCase`. |
| I-11 | Organization Admin may request promotion or demotion; human public-governance authority approves publication/withdrawal. Demotion requires impact preview, recent step-up by the executing Platform Admin, and cannot make a legacy ownerless public symbol private. No agent or Organization Admin self-publishes or self-demotes. |
| I-12 | Raster formats are accepted; SVG is accepted only through a vetted scan/parse/sanitize/rasterize pipeline. Store normalized PNG derivatives and never serve untrusted uploaded SVG. Generated fallback icons are deterministic, local, safe, and non-PII. Processing/scanning failure fails closed. |
| I-13 | Keep `CatalogApiUsageEvent` for API-key traffic. Add a separate server-derived authenticated browser/product-usage event domain, with nullable future scope dimensions only where useful and no raw search text by default. Audit, analytics, security events, and reputation remain separate domains. |
| I-14 | Initial reputation consists of auditable contribution/review counts and badges. Weighted points and commercial benefits require a later versioned policy accepted by Chris. Reputation never grants authority. |
| I-15 | Support zero/one/many organization memberships without an arbitrary product cap. Administrative lists and login choices are bounded/paginated; more-than-five memberships are supported and tested. Any future cap requires a product amendment and migration/UX policy. |
| I-16 | No local checkpoint commits are assumed authorized by this addendum. Until Chris explicitly authorizes them, use per-path hashes, diffs, tests, and resume evidence without committing. Push, migration, deployment, publication, withdrawal, service restart, and gateway actions always require separate authorization. |
| I-17 | New product-facing copy uses American English (`Organization`, `Favorite`, `Authorized`, `Behavior`, `Localization`). Existing API/database identifiers such as `favourite` remain for compatibility. Dates/numbers are locale-aware; customer content is not auto-translated; stable symbol identity is not translated. |
| I-18 | A valid temporary credential with `must_change_pin` creates only a credential-change-limited session. Mandatory PIN change completes before organization-selection challenge or personal/organization application session issuance. Return-to values are server-validated and carry no authority. |
| I-19 | Retain the current protected-owner/email safeguard until the reserved `symgov` organization, initial active Symgov Admin, active Platform Admin assignment, replacement lockout protections, recovery, and rollback evidence exist. Retire email-based authorization only through a separately reviewed cutover, never in the bootstrap transaction. |
| I-20 | Backend authority uses these default-off controls: `SYMGOV_ORGANIZATIONS_ENABLED`, `SYMGOV_ORGANIZATION_ADMIN_ENABLED`, `SYMGOV_SYMBOL_SETS_ENABLED`, `SYMGOV_ORGANIZATION_SYMBOLS_ENABLED`, `SYMGOV_ORGANIZATION_AGENTS_ENABLED`, and normalized lowercase `SYMGOV_ORGANIZATION_PILOT_CODES`. Each has a documented disabled response and kill-switch. `/auth/me` exposes effective boolean capabilities; UI visibility never grants authority. Worker kill switches stop new claims without deleting durable queue state. |
| I-21 | The active Hermes `symgov` profile is the only model resolver after the OpenClaw cutover. Database configuration stores allowlisted logical `model_alias` and policy; each run snapshots the resolved provider/model. Existing `AgentDefinition.model` is only a compatibility/runtime projection, and the legacy OpenClaw manifest cannot override Hermes. No Ollama is introduced for this programme. |
| I-22 | Before Stage 10, freeze a versioned finding/audit vocabulary: action/event names, actor kinds (`human`, `agent`, `system`), severity/status values, deterministic fingerprints, one-active-finding rule, acknowledgement/dismissal/resolution/supersession transitions, optional assignee/issue reference, failed-authorization event rules, retention, redaction, and read permissions. Findings are advisory and never authorize governed actions. The exact vocabulary must be repository-owned, schema-validated, tenant-scoped, and reviewed before agent implementation. |
| I-23 | Amend FR-SYM-011 to distinguish stable identity from rolling revision resolution: ordinary `SymbolSetItem` stores only the stable governed-symbol UUID and resolves the current eligible approved revision at read time; usage/audit records retain the revision actually used; future immutable `SymbolSetReleaseItem` rows may pin a revision UUID. Ordinary set items are not rewritten for rolling revision changes. |
| I-24 | Store immutable display `code` plus immutable lowercase ASCII `normalized_code`. The reserved organization uses exact display and normalized code `symgov`. Commercial display codes use uppercase grammar `^[A-Z][A-Z0-9-]{1,31}$`; normalized codes use lowercase grammar `^[a-z][a-z0-9-]{1,31}$`; project/set codes use `^[A-Z0-9][A-Z0-9-]{0,31}$`. Uniqueness, selectors, reserved-code protection, and pilot allowlists use normalized lowercase code. Case-fold collisions are rejected; display names/titles remain editable. |
| I-25 | Selection challenges expire after 10 minutes, allow at most 5 invalid selection attempts, store only a hash of the opaque token, and are atomically consumed exactly once. A successful credential verification supersedes older outstanding challenges. Successful consumption, exhaustion, logout, PIN change, session/user revocation, or eligibility loss consumes/revokes the challenge; concurrent selection has one winner. Recent step-up is session-bound, valid for 10 minutes, never copied to a replacement session, and is cleared by logout/PIN change/revocation. It is required for Platform Admin mutations, protected/bootstrap cutover, organization suspension/deactivation, Organization Admin or Platform Admin grant/revoke, public demotion/withdrawal, and agent model/policy changes. Exact 599/600-second and 4th/5th-attempt boundaries are tested. |

## Specification open decisions

| ID | Proposed disposition |
|---|---|
| O1 | Defer commercial benefits for reputation until contribution quality, economics, and anti-gaming controls are validated. |
| O2 | Defer offline packaging shape until the online model and immutable release/versioning approach are stable. |
| O3 | Treat SSO/service credentials as a separate authentication initiative after generic organization functionality. |
| O4 | Keep `Organization Steward` and `Platform Governance` as logical capability names. Do not invent final agent/persona names in code; map them only during the later agent stage. |
| O5 | Configure logical model aliases by environment and task through the active Hermes `symgov` profile. Do not hard-code a provider/model in the product specification or organization schema. |
| O6 | Keep the data model future-capable but defer immutable Symbol Set release UI until a concrete regulated-customer need and release policy are accepted. |

## Prerequisite and ownership decisions

### F0.5 account security

The controlling F0.5 specification remains `SPECIFIED, NOT IMPLEMENTED`. Current source has `must_change_pin` on users and exposes the state, but `routes/auth.py` creates a normal session immediately after credential verification, `dependencies.py` has no credential-change-limited guard or CSRF policy, and no F0.5 throttle/audit migration is present. F0.5 therefore remains a prerequisite before Stage 2 organization-bound sessions. Its implementation must preserve the I-18 ordering above and own its next migration after the canonical-ID owner has explicitly completed or rebased its completeness work.

### Canonical Catalog identity

Current head `20260802_0026` and the additive `CatalogSymbolIdentifier` model/service/tests are present. The canonical plan's audited backfill tooling, publication-completeness revision, shared resolver/caller migration, and related UI/release work are not complete in the inspected repository. The canonical-ID owner retains the next migration slot conceptually; Symbol Set organization migrations must not claim a revision until that work is explicitly complete/rebased and the live head is rechecked. No production backfill or migration is authorized here.

### F0.6 agent routing

The repository architecture/README policy says Alfi/main remains Telegram orchestrator, but `openclaw-agents.manifest.json` still contains a direct Telegram binding to Libby. This is a live configuration/policy contradiction. Resolve it before Stage 10 with one authoritative policy and a manifest-policy regression test. Do not change a live gateway or external messaging route under this addendum.

### Visibility rollback floor

Before any private row is created/imported/backfilled or demotion is enabled, deploy and verify the Stage 5 visibility-aware backend and all migrated public/background readers, including Hannah and Whitney. After private semantics/data exists, ordinary rollback is feature-disable plus roll-forward or deployment at/above that floor. If emergency pre-floor recovery is unavoidable, deny all public Catalog/published/page/package/download/asset/alias/Favorite routes at external ingress, then stop and drain all web/API/background readers; keep them denied/stopped until an at/above-floor release is restored and the full reader matrix passes.

## Stage 0 evidence recorded during Luna continuation

- Baseline branch: `main`; `HEAD` and `origin/main`: `a18d5b3587ebb11c95f45ca16643efe94b322c61`.
- Dirty paths preserved: `tests/test_llm_usage_migration.py` (the one-line stale-head correction). Pre-existing untracked `CLAUDE.md` and `docs/Symbol Set Management Spec v0.3.md` were not modified. Planning artifacts are the master plan, the resume (updated in this continuation), and this proposed addendum.
- Exact isolated Alembic check from `backend/`: `PYTHONPATH=. uv run --isolated --with-requirements requirements.txt --with-requirements requirements-test.txt alembic heads` → `20260802_0026 (head)`.
- Focused baseline correction: `./scripts/test-backend.sh tests/test_llm_usage_migration.py -q` → `2 passed`.
- Full portable backend suite after the isolated correction: `1490 passed, 3 skipped, 3 deselected`.
- Frontend Node suite: `74 passed`.
- Workspace-clean compile gate: passed using temporary `PYTHONPYCACHEPREFIX`.
- `git diff --check`: passed.
- No migration, production data mutation, deployment, push, publication, withdrawal, service restart, gateway change, or external message was performed.

## Acceptance block

Accepted in this continuation by Chris Brighouse, CEO of Symgov, at `2026-08-08T19:08:02Z`: all proposed I-01–I-25 contracts and O1–O6 dispositions are accepted without amendment. This acceptance freezes the implementation contract; it does not authorize commits, push, production migration, deployment, service restart, publication, withdrawal, gateway changes, or external messaging. The accepted addendum requires independent review against its exact post-acceptance hash before implementation proceeds.
