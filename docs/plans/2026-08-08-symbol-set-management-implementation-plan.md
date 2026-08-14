# Symbol Set Management — staged implementation plan

> For Hermes/Luna: execute one stage per fresh context. Read the controlling specification, this plan's stage section, and the Luna resume file before acting. Do not begin a later stage until the current stage's completion gate is recorded. Substantial implementation must use the durable, serialized Symgov Kanban/Cody lane unless Chris explicitly authorizes direct Luna implementation for that stage.

**Status:** DRAFT IMPLEMENTATION PLAN — no implementation or runtime change authorized by this document

**Plan date:** 2026-08-08

**Controlling product source:** `docs/Symbol Set Management Spec v0.3.md`

**Source SHA-256:** `42c240782a4732438a24a53d7ae80eefa6a78282601a1c4a91d19d86254a1344`

**Repository baseline inspected:** `main` / `origin/main` at `a18d5b3587ebb11c95f45ca16643efe94b322c61`

**Companion handoff:** `docs/plans/2026-08-08-symbol-set-management-luna-resume.md`

## 1. Goal and delivery strategy

Deliver the organization, project, Symbol Set, private/public symbol, Catalog, telemetry, and agent-oversight capabilities in the specification without weakening the existing individual-account, subscription, publication, Catalog, audit, and specialist-agent controls.

This is a programme, not one coding task. It is deliberately split into independently reviewable stages. Each stage:

1. begins from a recorded immutable source baseline;
2. has one dominant data/security concern;
3. adds focused executable tests before or with implementation;
4. receives fresh Stage 1 specification review and Stage 2 security/code-quality review;
5. runs focused gates and the necessary broader regressions;
6. updates the resume file with exact evidence;
7. stops before the next stage or any production side effect.

Luna (max) should own stage orchestration and difficult design/review reasoning. Implementation should remain serialized because every stage touches the same repository and database model.

## 2. Current-state reconciliation

### 2.1 What already exists and should be extended

- Individual user accounts, four-digit PIN login, HTTP-only session cookies, additive global roles, Free/Plus subscriptions, protected-owner handling, profile and user administration: `backend/symgov_backend/auth.py`, `routes/auth.py`, `routes/admin.py`, `subscriptions.py`, and `models/schema.py`.
- Backend role and workspace guards: `backend/symgov_backend/dependencies.py` and route-level dependencies in `backend/symgov_backend/app.py`.
- A generic append-only `AuditEvent` model and transaction-local audit creation in selected review, Catalog API-key, and publication paths. Current user-admin create/update/delete/PIN-reset routes do not consistently audit and must not be treated as a complete account-audit implementation.
- Governed symbols, revisions, published pages, packages, review cases, human decisions, feedback, publication jobs, and durable agent queue records in `backend/symgov_backend/models/schema.py`.
- Catalog browsing, account-scoped favourites, published symbol detail/download/search, API-key usage telemetry, and developer surfaces.
- React/Vite HashRouter UI, including login, mandatory PIN change, admin, workspace, Catalog, favourites, and profile experiences in the currently large `frontend/src/App.jsx` plus focused helper modules.
- Agent definitions, durable queue items, a repository-owned worker, configurable Hermes runtime, and per-agent model selection.
- Additive canonical Catalog identity schema and lifecycle service at migration head `20260802_0026`: `backend/alembic/versions/20260802_0026_catalog_symbol_identifiers.py` and `backend/symgov_backend/catalog_symbol_ids.py`.
- Reliable test wrappers: portable/external backend partitions, flat frontend Node tests, Langfuse POC, and isolated frontend build.

### 2.2 What does not exist

Repository searches and model/route inspection found no implemented domain for:

- organizations, organization memberships, organization roles, or platform roles;
- organization-bound sessions or organization selection after login;
- projects or project context;
- Symbol Sets, set items, active-set selection, releases, or releases imported into sets;
- organization-owned/private symbol visibility and organization-wide palette scope;
- organization review records distinct from public governance review;
- organization administration APIs or UI;
- organization usage telemetry, contribution reputation, organization dashboards, or organization-scoped agent findings.

These are new authorization and persistence boundaries, not frontend filters over existing global data.

### 2.3 Important inherited work

This programme must not silently bypass three already-defined controls:

1. `docs/plans/2026-07-30-f0-5-account-security-invariants-spec.md` is specified but not implemented. Forced-PIN backend enforcement, session revocation, login throttling, and consistent CSRF are prerequisites for organization-bound sessions.
2. `docs/plans/2026-08-02-catalog-canonical-urls-and-short-links-implementation-plan.md` currently has only its additive `0026` identity schema/service implemented. Its planned completeness/backfill revision owns the next migration slot conceptually. Finish or explicitly rebase that work before allocating organization revisions.
3. F0.6 in `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md` must reconcile Alfi/Telegram routing before organization-scoped agent automation is enabled.

The older backlog's F1.6 organization deferral is superseded only if Chris accepts the new Symbol Set Management specification as the separate product decision it required. Its other security and governance blockers remain valid.

### 2.4 Verified planning baseline

At plan time:

- `main` equalled `origin/main` at `a18d5b3`;
- the only pre-existing untracked files were `CLAUDE.md` and the v0.3 specification;
- Alembic reported one head: `20260802_0026`;
- focused auth/canonical-ID/migration tests passed;
- all 74 frontend Node tests passed;
- the full portable backend suite had one known baseline failure: `tests/test_llm_usage_migration.py` still asserted obsolete Alembic head `20260730_0025` instead of actual head `20260802_0026`; this is a stale test, not evidence of a second migration head;
- `git diff --check` passed;
- no migration, deployment, gateway change, service restart, or production data mutation was performed.

## 3. Decisions that must be frozen before implementation

The v0.3 document is intentionally a draft and leaves several security-significant seams. Stage 0 must either accept the recommended defaults below or record a different explicit decision in an accepted v0.4/addendum. No implementation should invent these rules mid-stage.

| ID | Open seam | Recommended implementation contract |
|---|---|---|
| I-01 | Who may create an organization? | Platform Admin only in v1. Creation atomically assigns one nominated existing active user as the first Organization Admin. Invitations and self-service organization creation are out of scope, but membership rows retain nullable invitation/activation timestamps for future use. |
| I-02 | Organization uniqueness and entitlement | `normalized_code` is globally unique and immutable. Freeze a normalized-name/disambiguation policy that blocks accidental duplicates while allowing legitimate same-name legal entities, as required by spec 8.3. Initial entitlement is manually administered `active` or `suspended`; billing, seats, and organization subscription purchase are out of scope. |
| I-03 | Existing global `admin` versus new roles | Preserve global roles during migration. Add independent organization roles and a separate `platform_admin` role. Do not infer one from another. Bootstrap Chris explicitly into all required roles rather than relying on email at authorization time. |
| I-04 | Platform-admin activation | Effective only when the session is bound to the Symgov organization, the user is an active Symgov Organization Admin, and the account holds active `platform_admin`. A personal/other-organization session never activates it. Sensitive mutations require recent step-up authentication. |
| I-05 | Multi-organization login route compatibility | Preserve `POST /auth/login` for existing clients and decide whether Appendix A's `/auth/session` is a versioned alias or a later replacement. If one eligible organization exists, issue the full bound session; if several exist, return a short-lived opaque selection challenge and no privileged session; the accepted selection route consumes it once. Unassigned users receive a personal session. |
| I-06 | Active organization mutation | `active_organization_id` and session mode are immutable after full session creation. Switching organization always revokes/replaces the session through fresh sign-in. |
| I-07 | Project and active-set context | Project selection is mutable only inside the bound organization session and is validated server-side. Persist one active-set preference per `(user, project)`; do not use browser storage as authority. Resolve explicit set, user preference, project default, then organization default in that order. |
| I-08 | Organization capabilities | Keep base Organization Admin/User roles. Add explicit `contributor` and `symbol_reviewer` capabilities. Organization Admin is not automatically a reviewer; an admin may grant the reviewer capability to themself under last-admin/platform rules. Do not reuse global roles as tenant authority. |
| I-09 | Private-symbol availability | Use the specification's `organization_wide` boolean on organization-owned symbols. Visibility (`organization_private | public`) and organization-wide availability are separate concepts. Set-only private symbols appear only through eligible set membership. |
| I-10 | Organization review record | Approval is revision-specific. Add a dedicated organization review/submission record and decision history; do not overload public `SymbolRevision.lifecycle_state` or public `ReviewCase` semantics. |
| I-11 | Public contribution and demotion authority | Organization Admin may request promotion/demotion. Human public-governance authority approves publication/withdrawal. Demotion requires impact preview, recent step-up authentication by the executing Platform Admin, and cannot make a legacy ownerless public symbol private. |
| I-12 | Icon security | Accept raster formats and SVG upload only through a vetted scan/parse/sanitize/rasterize pipeline. Store normalized PNG derivatives; never serve untrusted uploaded SVG. Generated fallback is deterministic, local, and safe. |
| I-13 | Browser telemetry | Keep `CatalogApiUsageEvent` for API-key traffic and add future-safe nullable scope dimensions only where useful. Add a separate organization product-usage event domain for authenticated browser/governance activity, with server-derived context and no raw search text by default. |
| I-14 | Reputation scoring | First ship auditable contribution/review counts and badges. Add weighted points only through a versioned policy accepted by Chris; never make reputation an authorization input. |
| I-15 | Membership cardinality | Implement the specification's zero/one/many organization memberships without an arbitrary product cap. Bound and paginate administrative lists and login-choice payloads; test more than five memberships. Any future cap requires a product amendment and migration/UX policy. |
| I-16 | Local checkpoints | At programme start Chris should authorize or decline local checkpoint commits once. Without authorization, freeze per-path hashes and diffs instead. Push, migration, deployment, publication, withdrawal, and service actions always require separate explicit authorization. |
| I-17 | Language/localization compatibility | New product-facing copy uses American English (`Organization`, `Favorite`, `Authorized`, `Behavior`, `Localization`). Existing internal/API/database identifiers such as `favourite` remain until a separately versioned compatibility change. Locale-aware dates/numbers and stable-ID translation support are required; customer names/descriptions are not auto-translated. |
| I-18 | Forced-PIN and organization-selection ordering | Follow the accepted F0.5 state machine: valid temporary credentials may create only a credential-change-limited session. Complete mandatory PIN change before issuing an organization-selection challenge or a personal/organization application session. Return-to data must remain server-validated and authority-free. |
| I-19 | Protected-owner cutover | Keep the current protected-owner/email safeguard until the reserved `symgov` organization, initial active Symgov Admin, active Platform Admin assignment, and replacement lockout protections are created and verified. Retire email-based authorization only in a separately reviewed cutover; never remove the old safeguard in the same transaction that first creates the replacement controls. |
| I-20 | Feature flags and pilot scope | Backend authority uses explicit default-off flags for organizations, organization administration, Symbol Sets, organization symbols, and organization agents, plus an organization-code pilot allowlist. `/auth/me`/capabilities expose effective server state to the UI; frontend visibility never grants authority. Each backend flag has a documented disabled response and kill-switch behavior. |
| I-21 | Agent configuration source of truth | The active Hermes `symgov` profile resolves allowlisted logical `model_alias` values. Database configuration stores aliases and policy, while each run snapshots the resolved provider/model. Existing `AgentDefinition.model` is a compatibility/runtime projection; the legacy OpenClaw manifest cannot override Hermes after cutover. |
| I-22 | Finding and audit vocabulary | Freeze versioned action/event names, actor kinds, severity/status values, deterministic finding fingerprints, one-active-finding rules, acknowledgement/dismissal/resolution/supersession transitions, optional assignee/issue reference, failed-authorization events, retention, redaction, and read permissions before Stage 10. |
| I-23 | Rolling-set revision semantics | Ordinary `SymbolSetItem` rows reference only the stable governed-symbol UUID and resolve the current eligible approved revision at read time. Historical usage/audit records retain the revision actually used. Future immutable `SymbolSetReleaseItem` rows pin revision UUIDs. The accepted addendum must amend/clarify FR-SYM-011 accordingly rather than leaving implementation to choose between contradictory requirements. |
| I-24 | Reserved and commercial organization-code normalization | Store immutable display `code` plus immutable lowercase ASCII `normalized_code`; uniqueness and selectors use `normalized_code`. The reserved organization stores exact display/normalized code `symgov`. Commercial display codes use uppercase grammar and normalize by ASCII lowercase. Pilot allowlists contain normalized lowercase codes. Case-fold collisions are rejected. |
| I-25 | Selection-challenge and step-up security bounds | Default contract: organization-selection challenges expire after 10 minutes, permit at most 5 invalid selection attempts, are stored hashed, and are atomically consumed exactly once. A new successful credential verification supersedes older outstanding challenges; successful consumption, attempt exhaustion, logout, PIN change, user/session revocation, or eligibility loss consumes/revokes them. Concurrent selection has one winner. Recent step-up is session-bound and valid for 10 minutes, is cleared by logout/PIN change/revocation, and is required for Platform Admin mutations, protected/bootstrap cutover, organization suspension/deactivation, organization-admin or Platform-Admin grant/revoke, public demotion/withdrawal, and agent model/policy changes. Freeze any different values/list explicitly in the accepted addendum. |

Recommended code/code-name grammar to freeze in Stage 0:

- commercial organization display code: uppercase `^[A-Z][A-Z0-9-]{1,31}$`; the reserved system organization is the explicit exact-code exception `symgov`;
- organization `normalized_code`: lowercase ASCII `^[a-z][a-z0-9-]{1,31}$`, derived once from display code and used for uniqueness, selectors, reserved-code protection, and pilot allowlists;
- project and set code: uppercase `^[A-Z0-9][A-Z0-9-]{0,31}$`; project code and Set Code are each unique within the owning organization;
- codes are immutable after creation; display names/titles are editable;
- all state and role values use database checks plus domain-service validation, not ad hoc route strings.

Recommended I-20 flag contract to freeze in Stage 0:

- `SYMGOV_ORGANIZATIONS_ENABLED` — master backend authority gate; when false, current login/personal behavior is unchanged and new organization routes return the accepted privacy-preserving disabled response;
- `SYMGOV_ORGANIZATION_ADMIN_ENABLED` — organization and Platform Admin control planes;
- `SYMGOV_SYMBOL_SETS_ENABLED` — project/set context and builder APIs/UI;
- `SYMGOV_ORGANIZATION_SYMBOLS_ENABLED` — private symbol/review/contribution behavior;
- `SYMGOV_ORGANIZATION_AGENTS_ENABLED` — agent configuration, claims, runs, and findings;
- `SYMGOV_ORGANIZATION_PILOT_CODES` — normalized organization-code allowlist evaluated by the backend; non-allowlisted memberships do not alter existing personal behavior during pilot;
- `/auth/me` exposes effective boolean capabilities for UI negotiation; a false UI flag never weakens backend checks, and worker kill switches stop new claims without discarding durable queue state.

## 4. Cross-cutting architecture contracts

### 4.1 Tenant isolation

- Every organization-owned table carries a non-null `organization_id`, directly or through a parent with an enforceable foreign key path.
- Every service accepts the authenticated principal/context and derives organization scope server-side. Client-supplied organization IDs are selectors only and must match the session.
- Repository/service queries include organization predicates before pagination, aggregation, or object retrieval.
- Cross-organization private symbol access returns a privacy-preserving not-found response unless a documented Platform Admin exception path is used.
- Database constraints prevent cross-parent set/project references where feasible; service tests prove cases that PostgreSQL foreign keys cannot express directly.
- API-key Catalog routes remain public-Catalog-only until an independent organization-scoped API-key design is accepted.

### 4.2 Authentication and authorization

Introduce one principal shape that includes user identity, global roles, session mode, active organization, effective organization role/capabilities, and effective Platform Admin status. Avoid route-local reconstruction.

Authorization dependencies should be explicit and composable, for example:

- authenticated personal-or-organization user;
- organization session required;
- Organization Admin required;
- organization symbol reviewer required;
- Platform Admin in Symgov context required;
- recent step-up authentication required;
- existing workspace/global role checks preserved.

Implement the accepted I-25 values as named configuration constants with bounded validation. Enforce challenge expiry/attempt/consume/revoke transitions atomically in the database/service transaction and test exact time/attempt boundaries and concurrent consume races. Step-up freshness is derived from the current full session only; it is never copied to a replacement session or inferred from account-level history.

Every new route must appear in the automated route-policy inventory for both v1 and any intentional legacy alias. New organization routes do not need legacy aliases unless a real compatibility consumer exists.

### 4.3 Audit

Every security/governance mutation commits its audit event in the same transaction. The event payload should include:

- actor user and effective role/capability;
- active organization and affected organization;
- project/set/symbol/revision identifiers where relevant;
- action and reason;
- bounded before/after fields;
- request/trace/correlation IDs;
- step-up evidence as a boolean/timestamp reference, never a PIN or credential;
- source `human | agent | system` and agent/model/run identifiers when applicable.

Do not place icons, raw queries, PINs, cookies, authorization headers, model secrets, or arbitrary tenant payloads in audit JSON.
Record high-value failed cross-tenant/Platform Admin authorization attempts through a bounded security-event path with privacy-preserving target identifiers; ordinary 404 noise must not become an unbounded audit flood. Freeze action names, actor kinds, retention, redaction, and audit-query permissions in I-22.

### 4.4 Service boundaries

Prefer small domain modules over adding more logic to `routes/*.py`, `runtime.py`, or the 7,000+ line `frontend/src/App.jsx`.

Expected backend modules, introduced only as their stage needs them:

- `organization_service.py`
- `organization_authorization.py`
- `organization_icons.py`
- `project_service.py`
- `symbol_set_service.py`
- `effective_palette.py`
- `organization_symbol_service.py`
- `organization_review.py`
- `public_contributions.py`
- `organization_usage.py`
- `organization_agents.py`

Expected route modules:

- `routes/organizations.py`
- `routes/platform_admin.py`
- `routes/projects.py`
- `routes/symbol_sets.py`
- `routes/organization_symbols.py`
- `routes/organization_usage.py`
- `routes/organization_agents.py`

Expected frontend extraction points:

- pure request/response and route helpers in flat `frontend/src/*.js` files so the existing test wrapper discovers tests;
- page components for organization selection/admin, project context, Symbol Set management, contribution/reputation, and agent findings;
- `App.jsx` retained as shell/routing integration rather than the home of every new domain algorithm.

### 4.5 Migration policy

- Determine the live Alembic head at each schema stage. Do not hardcode `0027` in advance: the canonical identity plan currently reserves a completeness revision after `0026`.
- Keep each schema stage additive and independently deployable with feature flags off.
- Treat every column touched by a currently deployable writer as an expand/contract change. The expand migration must backfill existing rows and preserve the old insert shape using a safe server default or transitional nullability; the new writer then sets the field explicitly. Remove compatibility defaults/nullability only in a later reviewed contract migration after deployment evidence proves no writer in the supported deployment/rollback set still needs them.
- Use explicit foreign keys, indexes, unique constraints, and database check constraints for status/role vocabulary.
- Avoid destructive backfills inside normal web startup. Use idempotent audited management commands with inventory/dry-run/apply modes and expected hashes.
- Legacy public symbols retain existing required user `owner_id` values and gain `owner_organization_id = NULL`; this is a supported compatibility state, not bad data. New organization symbols retain creator/user attribution while adding organization ownership. Never infer tenant identity from `ExternalIdentity.organization` free text or `CatalogApiKey.customer_name`.
- Do not infer historical organization/project/set attribution for old usage events. Backfill only fields supported by deterministic provenance and leave the rest null.
- The fully visibility-aware Stage 5 backend is the minimum code rollback floor before any private row is created/imported/backfilled or any demotion can be enabled. The floor includes the shared visibility policy and `active_public_symbol_projections` use by every public Catalog/published/page/package/download/asset/alias/Favorite route and every direct/background reader, including Hannah and Whitney. Deploy and verify that floor on every web, API, and background-reader process before crossing the gate.
- Before that gate, pre-floor code may be tested only against the additive schema with organization-symbol flags off and no private semantics/data. Once any private semantics/data exists, never claim or perform ordinary rollback to a pre-floor reader: disable feature flags and roll forward, or deploy only a release at or above the visibility floor. Production downgrade of used tenant tables is not an ordinary rollback path.
- Emergency recovery that cannot avoid a pre-floor backend must first deny at the external ingress every public Catalog/published/page/package/download/asset/alias/Favorite route, then stop and drain all web/API and background readers, including Hannah and Whitney, before starting the older backend. Keep those routes denied and readers stopped; never reopen or resume them until a release at or above the visibility floor is restored and the complete visibility/reader checks pass.
- For every schema stage, run mixed-version contract tests against the upgraded database: execute compatible pre-stage writer shapes with flags off and execute the new writer. Before the private-data gate, also prove pre-floor code against a database containing no private semantics/data. From Stage 5 onward, rollback-reader tests must use an exact release at or above the visibility floor and must never generalize compatible old writer inserts into a claim that pre-floor readers are safe. Migration source/model parity alone is insufficient.

### 4.6 Test command discipline

The repository wrappers always include their full partition. They are broad gates, not focused commands.

Use this direct isolated command for focused backend work, replacing the file list:

```bash
PYTHONPATH=backend uv run --isolated \
  --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  python -m pytest tests/test_one.py tests/test_two.py -q
```

Use direct Node tests for focused frontend work:

```bash
node --test frontend/src/one.test.js frontend/src/two.test.js
```

At the appropriate completion gate run the broad wrappers:

```bash
./scripts/test-backend.sh
./scripts/test-backend.sh --external
./scripts/test-langfuse-poc.sh
./scripts/test-frontend.sh
./scripts/build-frontend-isolated.sh
git diff --check
```

Only stages that alter agent workspace wiring require the external partition during iteration; it remains part of the final programme gate.

### 4.7 Performance, accessibility, and localization

- Preserve the specification's provisional P95 goals: Public Catalog search under 1.5 seconds, organization context under 500 ms, project/set switch under 750 ms, and representative effective-palette query under 1 second.
- Add query-plan/index and bounded representative-load tests at each data stage; record hardware/dataset assumptions so these are engineering evidence, not contractual SLA claims.
- Core sign-in, Catalog, review, and approval paths must not synchronously wait for advisory agents.
- Every new control and administration flow must be keyboard-operable, screen-reader labelled, responsive at supported viewport sizes, and have visible focus/error/status treatment.
- Icons have organization-name alternative text unless decorative. Project-description counters and validation are announced accessibly.
- Use locale-aware date/number formatting. Preserve one stable symbol identity while allowing translated symbol names/descriptions through a versioned metadata contract; never auto-translate customer project/set/organization content.

### 4.8 Repeatable execution protocol for Stages 1–10

For each stage, Luna should use this order and record the exact commands/results in the resume:

1. Run an executable skill-availability preflight: call `skill_view(name='<required-skill>')` for every skill required for that stage in the resume, verify each call succeeds and reports an available/readiness-success state, and stop before card creation or implementation if any required skill is unavailable. Do not rename the directly verified `kanban-orchestrator`, `kanban-dependency-orchestration-safety`, or `kanban-worker` skills.
2. Rebase the stage analysis on current HEAD/status/Alembic head and freeze the accepted spec/addendum and stage path hashes.
3. Write the narrow migration/model contract tests and prove RED for the intended missing behavior; implement only that additive storage slice and prove GREEN.
4. Write service/invariant/concurrency tests and prove RED; implement the smallest transaction-local domain services and prove GREEN.
5. Write authorization/API contract tests, including cross-tenant and fail-before-write cases; add routes/schemas only after those boundaries are explicit.
6. Write pure frontend contract/helper tests before wiring pages into the application shell; then add accessibility/responsive assertions and an isolated build.
7. Run stage-focused regressions plus every existing suite touching changed boundaries. Do not burn time running a broad wrapper after every small edit.
8. Freeze the exact implementation snapshot. Run fresh immutable Stage 1 review, correct and re-review if necessary, then fresh Stage 2 review; never reuse approval after a correction.
9. Run the stage completion gates, capture status/diff/path hashes, update the resume with residual risks and side effects not performed, and stop before the next stage.

## 5. Dependency graph

```text
Stage 0 contract/prerequisite gate
  └─> Stage 1 organization schema/invariants
        └─> Stage 2 bound session + authorization
              ├─> Stage 3 organization/platform admin + icons ───────────────────────────────┐
              └─> Stage 4 projects + Symbol Set persistence                                 │
                    └─> Stage 5 private symbols + review + visibility foundation             │
                          └─> Stage 6 effective palette + set builder                         │
                                └─> Stage 7 public contribution/demotion                     │
                                      └─> Stage 8 Catalog/favourites/UI integration          │
                                            └─> Stage 9 telemetry/reputation                 │
                                                  └─> Stage 10 agent oversight ──────────────┤
                                                                                                └─> Stage 11 hardening/release
```

Stage 3 and the persistence portion of Stage 4 may be developed in parallel only in separate worktrees/branches with no shared migration head. Under the current shared-worktree policy, keep them serialized.

## 6. Stage 0 — freeze the contract and close entry gates

**Outcome:** an accepted implementation contract, an uncontested migration head, and a safe account/session baseline.

### Tasks

1. Re-read v0.3 against this decision table. Create an accepted v0.4 or dated decision addendum; do not rewrite the original draft or this historical plan in place after execution begins.
2. Record Chris's answers for I-01–I-25, especially organization creation, role coexistence, review authority, demotion, SVG handling, scoring, forced-PIN ordering, protected-owner cutover, flags, audit/findings, agent model aliases, rolling-set revision semantics, reserved-code normalization, challenge/step-up security bounds, compatibility, and local checkpoint commits. The accepted addendum must explicitly reconcile FR-SYM-011 with the rolling-set lifecycle and freeze I-25's TTL, attempt, atomic-consume/revoke, freshness, and protected-mutation list.
3. Execute or explicitly sequence F0.5. At minimum, backend forced-PIN enforcement, session revocation, login throttling, and unified cookie-mutation CSRF must be complete before Stage 2.
4. Correct the stale `tests/test_llm_usage_migration.py` expected-head assertion to the actual current head and prove the full portable suite is green before creating another migration. Do not mix that baseline correction into the first tenancy migration.
5. Resume the canonical-ID plan from its actual checkpoint. Complete its resolver/publication invariant before Stage 5 and avoid migration-number collision before Stage 1 creates a revision.
6. Freeze exact default-off backend/UI flag names, normalized organization-code pilot allowlist behavior, disabled responses, capability-negotiation shape, and kill-switch semantics. Freeze audit/finding vocabularies and retention/redaction/read policies.
7. Record F0.6 as a blocker for Stage 10, not necessarily for Stages 1–9.
8. Run `skill_view(name='...')` for every Stage 0 skill named in the resume and stop if any call is unavailable; only then create a stage parent/card graph in the durable Symgov Kanban lane. Keep implementation → Stage 1 review → Stage 2 review → final verification dependencies serialized; review defects create correction cards and fresh downstream reviews.
9. Freeze baseline evidence in the resume file: branch, HEAD, origin/main, status, spec/addendum hash, Alembic heads, relevant existing-plan checkpoints, and permitted side effects.
10. Freeze executable final-gate commands. Use the repository wrappers/build plus this workspace-clean compile gate, which uses `python3` and directs all bytecode to a temporary cache outside the worktree:

    ```bash
    (
      set -eu
      pycache_dir="$(mktemp -d)"
      trap 'rm -rf "$pycache_dir"' EXIT
      PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m compileall -q backend/symgov_backend scripts
    )
    ```

    Create and review a repository-owned added-line secret-scan script or pin an installed scanner and exact invocation before Stage 11. Do not call an unspecified “static check” or “secret scan” a passing gate.

### Acceptance

- The product source is marked accepted for implementation or has an accepted addendum covering I-01–I-25 and specification O1–O6, including the FR-SYM-011 clarification and exact I-25 security bounds.
- F0.5 is complete before any organization-bound privileged session is enabled.
- The full portable backend suite is green on the uncontested pre-tenancy migration head.
- Migration ownership/order is explicit and there is one Alembic head.
- No implementation is started against unresolved reviewer/Platform Admin authority.
- No production action has occurred.

**Stop:** begin Stage 1 only in a fresh Luna context.

## 7. Stage 1 — organization schema and invariant services

**Outcome:** additive tenant identity storage and deterministic invariants, still disabled at runtime.

### Primary files

Create/modify only after exact inspection:

- new Alembic revision after the then-current head;
- `backend/symgov_backend/models/schema.py`
- `backend/symgov_backend/models/__init__.py`
- `backend/symgov_backend/organization_service.py`
- `backend/symgov_backend/management.py` or a dedicated management script;
- `backend/symgov_backend/settings.py`
- new migration/model/service tests.

### Schema slice

Add:

- `organizations`: ID, immutable display `code`, immutable lowercase `normalized_code`, disambiguated display/legal name contract, locale, entitlement status, active/protected flags, generated-icon seed/metadata, uploaded-icon metadata, timestamps; enforce I-24 including exact reserved `symgov` and case-fold collision rejection;
- `organization_memberships`: organization, user, status, invited/activated/deactivated timestamps, unique membership;
- `organization_role_assignments`: surrogate history ID, foreign key to an existing membership, base role `admin | user`, assignment/revocation metadata, and a partial unique index enforcing at most one active base-role assignment per membership;
- `organization_member_capabilities`: membership, capability initially `contributor | symbol_reviewer`, status, audit timestamps;
- `platform_role_assignments`: surrogate history ID, user, role initially `platform_admin`, assignment/revocation metadata, and a partial unique index for one active `(user, role)` assignment while allowing revoke/reassign history;
- `user_sessions`: nullable `active_organization_id`; non-null checked mode `personal | organization` added with server default `personal` and existing-row backfill so the current `create_user_session()` insert shape remains valid; nullable recent-step-up timestamp; database/service protection prevents changing mode or active organization after insert while still allowing revocation, step-up, and later project context updates. New writers set mode explicitly, but retain the safe server default until the contract phase proves all old/rollback writers retired;
- `auth_organization_selection_challenges`: hashed opaque token, user, eligible organization snapshot/hash, exact I-25 expiry, consumed/revoked timestamps, and exact bounded attempt count.

Do not add project, set, release, private-symbol, telemetry, or agent tables yet.

### Invariants and services

- Normalize and validate codes once.
- Enforce active-user and active-entitlement checks.
- Support multiple active memberships without an arbitrary cap; keep membership listing/selection bounded and deterministic.
- Prevent removal/deactivation of the last active Organization Admin.
- Require every active Platform Admin to remain an active Symgov Organization Admin and prevent any transaction that leaves zero eligible active Platform Admins.
- Assign initial admin atomically with organization creation.
- An active membership has exactly one active base role at transaction commit. Create membership plus initial role atomically; replace roles under a membership-row lock by revoking the old and assigning the new in one transaction; allow zero active roles only once the membership is inactive. Enforce the at-least-one half with a deferred PostgreSQL constraint trigger, because the partial unique index enforces only at most one; mirror the invariant in the service for early errors.
- Treat membership removal and organization suspension as immediate access loss without deleting history.
- Generate a unique deterministic fallback icon locally from immutable organization UUID plus a versioned non-secret algorithm seed as part of organization creation; never use name/email/PII, and never call an image service or LLM.
- Add idempotent `audit` and `apply` management operations to create/reconcile the protected, immutable-code Symgov organization and protected owner membership/Platform Admin role. Preserve existing protected-owner authorization until the separately reviewed I-19 cutover proves the replacement controls.
- Add the accepted I-20 feature flags and pilot allowlist defaulting off. Schema presence alone changes no visible behavior.

### Tests

Create focused tests such as:

- `tests/test_organization_migration.py`
- `tests/test_organization_models.py`
- `tests/test_organization_service.py`
- `tests/test_organization_bootstrap.py`

Prove upgrade/model parity; current pre-stage login/session inserts still work after upgrade with `mode=personal`; flags-off and prior-code rollback work against the additive schema; membership-backed exactly-one-active-role constraints; atomic concurrent revoke/replace and inactive-membership zero-role behavior; revoke/reassign history; commercial display-code grammar; exact reserved lowercase `symgov`; normalized case-fold uniqueness/collisions and pilot-allowlist matching; unique/disambiguated names; more-than-five membership behavior; duplicate membership races; last-organization-admin and last-eligible-Platform-Admin protection; Platform Admin eligibility; database-backed session-organization immutability; inactive-user/organization rejection; PII-free deterministic unique generated fallback icons; idempotent bootstrap; protected Symgov code/deletion rules; protected-owner fallback retained; legacy users unaffected; downgrade safety; and no audit/secret leakage.

### Acceptance

- One Alembic head and additive migration.
- Personal account/login behavior unchanged with flags off.
- Organization invariants are service- and database-backed.
- Symgov bootstrap has dry-run/inventory evidence but has not mutated production without explicit approval.
- Fresh Stage 1 and Stage 2 reviews approve the exact implementation snapshot.

## 8. Stage 2 — organization-bound authentication and authorization

**Outcome:** secure personal or organization sessions with immutable organization context.

### Primary files

- `backend/symgov_backend/auth.py`
- `backend/symgov_backend/dependencies.py`
- `backend/symgov_backend/schemas.py`
- `backend/symgov_backend/routes/auth.py`
- `backend/symgov_backend/organization_authorization.py`
- `backend/symgov_backend/routes/platform_admin.py` for reauthentication only
- `backend/symgov_backend/app.py`
- auth/route-policy tests
- frontend auth helpers and organization-selection page/tests

### Backend tasks

1. Extend the authenticated principal with session mode, active organization, organization role/capabilities, and effective Platform Admin flag.
2. Enforce I-18 before context selection: `must_change_pin` credentials receive only the backend-enforced credential-change-limited session. Issue no organization challenge, personal application session, or commercial application session until the PIN change succeeds.
3. Refactor login into credential verification followed by context selection:
   - no eligible orgs → personal full session;
   - one eligible org → organization-bound full session;
   - multiple eligible orgs → one-time selection challenge, no privileged cookie;
   - inactive memberships/orgs omitted;
   - challenge contents server-side and token hashed at rest.
4. Add `POST /auth/select-organization`; reject arbitrary, stale, replayed, or no-longer-eligible choices.
5. Extend `/auth/me` with bounded context data, effective I-20 flags/capabilities, and no private organization directory data.
6. Add `POST /auth/reauthenticate`; successful current-PIN verification sets a short recent-step-up timestamp on the same session. Reuse F0.5 throttling/audit policy and never log PINs.
7. Add composable organization and Platform Admin dependencies. Platform Admin requires active Symgov context plus role assignment. Every tenant request revalidates active user, organization, entitlement, membership, role/capability, and effective feature/allowlist state rather than trusting the login-time snapshot alone.
8. Apply route-policy inventory tests. Keep existing personal routes working and avoid legacy aliases for brand-new organization APIs.
9. Revoke bound sessions immediately after membership deactivation, org suspension, or role removal where privilege loss requires it.

### Frontend tasks

- Update login response handling without exposing challenge details in URLs/local storage.
- Add an accessible organization-selection screen with logo/fallback icon, keyboard operation, expiry/retry states, and no default selection when several exist.
- Keep mandatory PIN-change destination handling intact.
- Show current organization context in the shell; switching organization returns to sign-in.

### Tests

Create/update:

- `tests/test_organization_auth_context.py`
- `tests/test_organization_authorization.py`
- `tests/test_route_auth_enforcement.py`
- `tests/test_auth_routes.py`
- `frontend/src/organizationSession.test.js`

Matrix: zero/one/many memberships including more than five; bounded/paginated choice handling; inactive membership; suspended org; challenge expiry at 599/600 seconds, usability after four invalid selections, atomic exhaustion/revocation on the fifth, denial of subsequent attempts, one-winner concurrent consume, supersession, replay, logout/PIN-change/revocation and eligibility-loss invalidation; forged organization ID; personal session; wrong-org Platform Admin; correct Symgov Platform Admin; step-up validity at 599/600 seconds, replacement-session noninheritance, clearing events, and every I-25 protected-mutation boundary; must-change-PIN interaction; v1/legacy parity for existing auth.

### Acceptance

- Every organization request derives immutable organization scope from the full session.
- No selection challenge is a usable application session.
- Personal use remains available to unassigned users.
- Platform Admin is impossible outside active Symgov context.
- F0.5 controls remain green.

## 9. Stage 3 — organization and Platform Admin control planes, including icons

**Outcome:** safe organization/member/role administration and read-only platform visibility.

### Primary files

- `organization_service.py`, `organization_icons.py`
- `routes/organizations.py`, `routes/platform_admin.py`
- `schemas.py`, `app.py`, `settings.py`
- object-storage helper only after inspecting current storage conventions
- frontend API helpers, `OrganizationAdminPage`, `PlatformAdminPage`, shell header, flat tests

### APIs

Implement the v0.3 surface with explicit scopes:

- Organization Admin: current organization detail/update, member list/add-existing-user/deactivate, role/capability changes, and icon upload/remove. Symgov-organization admin and Platform Admin lifecycle changes remain protected exceptions.
- Platform Admin: organization directory, create/suspend/reactivate, protected Symgov-admin changes, member diagnostics, Platform Admin grants/revocations, feature toggles/operational defaults where server settings permit. Every grant and eligibility-removal path preserves at least one eligible active Platform Admin under concurrency.
- No normal cross-tenant content endpoint. Exceptional private-data inspection, if later required, must be a separate reason-required, step-up, audit-heavy endpoint and is not part of this stage.

### Icon pipeline

1. Time-box a dependency/security spike and freeze the accepted malware scanner, parser, and rasterizer, including fail-closed behavior when scanning is unavailable.
2. Enforce authentication, authorization, content length, extension/MIME/signature agreement, dimensions, decoded image type, decompression-bomb limits, malware scan, and rate limits.
3. For SVG: reject scripts, external references, event handlers, foreign objects, data/network references, and unsupported constructs; rasterize in a resource-bounded process.
4. Strip metadata; store normalized PNG variants under non-user-controlled object keys; checksum and version them.
5. Treat object storage and database activation as a compensating workflow: upload/process/verify new immutable objects first, switch the active database reference transactionally, and clean abandoned objects through an idempotent reconciler. Failure at any point leaves the previous custom or generated fallback icon active.
6. Serve through an authenticated, cache-safe endpoint with fallback generated icon. Support accessible crop/preview in the UI without trusting client crop metadata as processed output.
7. Audit upload/remove metadata only, not image payload.

### Tests

- `tests/test_organization_admin_api.py`
- `tests/test_platform_admin_api.py`
- `tests/test_organization_icons.py`
- `frontend/src/organizationAdmin.test.js`
- `frontend/src/platformAdmin.test.js`

Prove role matrix, last-admin protection, bounded and paginated membership handling without an arbitrary cap, tenant isolation, step-up for sensitive platform changes, suspended organization behavior, malicious/polyglot/oversized/decompression SVG/raster rejection, deterministic fallback, and accessible frontend states.

### Acceptance

- Organization Admin cannot inspect or mutate another organization.
- Platform Admin directory does not imply private tenant-data access.
- No untrusted SVG is served.
- Every membership/role/capability/platform mutation is audited transactionally.

## 10. Stage 4 — projects, Symbol Set persistence, and active context

**Outcome:** organization projects and organization-owned Symbol Sets with many-to-many project availability, defaults, and one server-authoritative active set per user/project.

### Schema

Add in one additive migration:

- `projects`: organization, immutable code unique within organization, name, plain-text `short_description` limited to 50 characters in database/schema/UI, status, optional external-reference metadata unique within organization when non-null, timestamps;
- `symbol_sets`: owner organization, immutable Set Code unique within organization, name/title, description, disciplines/use cases, lifecycle `draft | active | superseded | archived`, optional `copied_from_symbol_set_id`, created_by, timestamps;
- `project_symbol_sets`: project, set, availability status, `is_default`, timestamps, unique pair, with one default set per project and service/database enforcement that both owners match;
- an organization-default-set record/reference with one valid active default per organization;
- `symbol_set_items`: set, symbol, sort order, section/group, display label, notes, preferred format, provenance, availability status/reason/timestamps, unique `(symbol_set_id, governed_symbol_id)`;
- `user_project_set_selections`: user, project, nullable active set, timestamps, unique `(user_id, project_id)`, with the selected set required to be available to that project;
- mutable selected-project context on the bound session or a dedicated session-context record, added only now that the project FK exists.

Do not make `symbol_sets` a child of exactly one project: the specification requires one set to be available to several projects. Do not add `SymbolSetRelease`, `SymbolSetReleaseItem`, or release-import tables yet; stable set/symbol IDs, lineage, and service boundaries must leave that extension open.

### Services and APIs

- Organization Admin creates/updates/closes projects, creates/copies/activates/supersedes/archives sets, makes sets available to one or more same-organization projects, and nominates project/organization defaults.
- Organization User lists eligible projects/sets and changes their active set.
- Enforce project and set belong to the session organization, project-set availability exists, active status, immutable codes, the 50-character project description at all three layers, unique external reference, idempotent/batch item add/remove, and last-selection/default cleanup on close/archive/supersede.
- Set removal deletes only the membership row; it never deletes a governed symbol.
- Copying a set creates a new stable set ID and records source lineage while retaining symbol references and item metadata; it does not copy symbol rows.
- Under accepted I-23, a rolling set item references only the stable governed symbol. Palette/context responses identify the currently resolved eligible approved revision; ordinary symbol revision changes do not rewrite the set item. Historical usage/audit retains the revision actually used. Only a future immutable release item pins a revision UUID.
- Add route-policy entries and bounded pagination/filtering from the start.

### Frontend

- Add project context selector showing name plus optional short description, accessible 50-character counter in administration, and active Symbol Set selector resolved from sets available to that project.
- Add Organization Admin project/set management screens.
- Keep context server-authoritative and recover gracefully when a selected project/set is archived.

### Tests

- `tests/test_project_symbol_set_migration.py`
- `tests/test_projects_api.py`
- `tests/test_symbol_sets_api.py`
- `tests/test_user_project_set_selection.py`
- `frontend/src/projectContext.test.js`
- `frontend/src/symbolSetAdmin.test.js`

Matrix: cross-org IDs, unavailable project/set pairs, one set shared by several projects, duplicate organization-scoped codes, duplicate/non-null external references, 0/50/51-character descriptions, one project default under races, organization-default fallback, inactive parents, concurrent selection updates, archive/supersede cleanup, set-copy lineage, rolling item current-approved-revision resolution and historical used-revision evidence, item metadata/order, item removal non-deletion, pagination, Organization User/Admin differences, and personal-session rejection.

### Acceptance

- Stable project/set identifiers, many-to-many project availability, defaults, and owner scopes are enforced.
- At most one active set exists per user/project.
- No public/private visibility logic is guessed yet; item APIs accept only symbols authorized by the existing state until Stage 5.

## 11. Stage 5 — organization-owned symbols, organization review, and visibility foundation

**Outcome:** organization members can create private drafts, an authorized organization reviewer can approve a specific revision without public publication, and every existing public read/asset path enforces the visibility property as the minimum rollback floor before any private row or demotion is possible.

### Schema

Add:

- `governed_symbols.owner_organization_id` nullable FK added alongside the existing required user `owner_id`; for new organization symbols `owner_id` remains creator/actor attribution while `owner_organization_id` is tenant ownership;
- `governed_symbols.visibility` checked `organization_private | public`, added non-null with server default `public` and legacy rows backfilled to `public` so current publication-handoff inserts that omit the field remain valid during the schema-first deployment; new organization writers always set it explicitly. The compatibility default supports writer expansion only and does not make pre-floor readers safe after private data exists;
- `governed_symbols.organization_wide` non-null boolean with server default false and existing-row backfill, meaningful only for organization-owned records;
- organization-review submission/decision tables keyed to organization, symbol, and revision, with immutable actor/timestamps/rationale and one active review per revision;
- any indexes needed for tenant/visibility/palette queries.

Keep canonical Catalog identity and publication completeness invariants intact. Do not assign public Catalog IDs to private-only symbols unless the accepted canonical-ID contract explicitly supports reserved private identity.

### Domain services

- Active Organization Admin or member with `contributor` capability creates an owner-bound private draft in the active organization only.
- Existing intake/asset validation remains deterministic; do not let an LLM decide persistence authority.
- Draft/submitted metadata and assets are visible only to the creator, active Organization Admins, and active appointed Organization Reviewers; ordinary members cannot enumerate or infer them.
- Submit the current revision to organization review.
- Only an active member with explicit `symbol_reviewer` capability may approve/reject/request changes; Organization Admin status alone is insufficient, although an admin may be appointed as reviewer. Actor derives from session.
- Approval is revision-specific and does not publish publicly.
- Mutation after approval creates a new draft revision requiring new review.
- Organization-wide scope can be enabled only for an organization-approved revision.
- Add/remove from sets remains separate from symbol approval.
- Introduce one tenant-aware visibility policy used by existing browser `/published/*` and integration `/catalog/*` list/detail/search/facet/count/page/package/download/asset routes, canonical IDs/aliases, and Favorites. Public/personal/API-key paths admit only `visibility=public`; private organization endpoints require matching bound organization and role/capability.
- Create one authoritative `active_public_symbol_projections` database view joining symbol, revision, page, entry, and package. At Stage 5 it requires `visibility=public`, published revision, public audience, and published package. Existing route queries and direct background readers—including Hannah curation and Whitney market-intelligence/clarification queries—must join this view rather than restating weaker raw-table predicates. This visibility-aware backend plus all migrated readers is the minimum rollback floor. Stage 7 replaces the view definition to include per-page/per-entry active state before demotion is enabled.
- Ensure stored objects are not anonymously reachable outside an authorization-aware delivery path. If the current storage topology exposes durable public object URLs, demotion remains disabled until objects are private or only short-lived, revocable authorization-derived URLs are issued.
- Preserve current public behavior for legacy rows, all of which migrate to `visibility=public` with null organization owner.

Roll out this stage schema-first with organization-symbol flags off. Pre-floor code may run only while all rows remain public. Before creating, importing, or backfilling the first private row—or enabling any private-symbol mutation—deploy the visibility-floor backend to every web/API process and Hannah/Whitney/other background reader, inventory and drain any older reader, and prove the complete route/reader matrix. Once that gate is crossed, rollback is feature-disable plus roll-forward or deployment of a version at/above the floor; the emergency external-deny/stop/drain procedure in Sections 4.5 and 17 is the only pre-floor recovery path.

### Tests

- `tests/test_organization_symbol_migration.py`
- `tests/test_organization_symbols_api.py`
- `tests/test_organization_symbol_review.py`
- `tests/test_private_symbol_asset_access.py`
- `tests/test_catalog_visibility_policy.py`
- `tests/test_background_public_projection_visibility.py`
- `tests/test_catalog_symbol_ids.py` regressions
- existing published search/detail/page/package/download/asset/alias/Favorites regressions
- frontend draft/review tests

Prove creator/admin/reviewer draft visibility, ordinary-member and cross-tenant non-disclosure on metadata/assets/search/counts, session-authoritative reviewer identity, explicit reviewer appointment, revision-specific approval, stale-decision conflict, no implicit public lifecycle change, approval/organization-wide matrix, asset checksum/format/dimension/provenance retention, audit transactions, all public resolver paths and Hannah/Whitney direct reads rejecting private records, direct-object access safety, current pre-stage publication-handoff insert shapes defaulting to public after migration, and new writer explicit values. Split rollback evidence explicitly: pre-floor code is exercised only with flags off and zero private rows; after inserting a private row, flags-off rollback is exercised only with the exact visibility-floor release, and tests reject any claim that a pre-floor reader can reopen public routes.

### Acceptance

- Private symbols are private by backend query and asset authorization, not UI convention.
- Organization approval has no public visibility side effect.
- Stable governed-symbol UUID survives revision and set membership changes.
- The shared visibility policy covers every public Catalog/published/Favorite/alias/asset path before Stage 7 can enable demotion.
- The visibility-floor release is deployed to and verified on every web/API/background reader before private rows are allowed, and all later rollback candidates remain at or above that floor.

## 12. Stage 6 — effective palette and Symbol Set Builder

**Outcome:** deterministic effective palette from active-set items plus approved organization-wide symbols, while Public Catalog browsing remains a separate search scope.

### Backend tasks

1. Add one `effective_palette` query/service returning bounded, paginated records with source badges and availability state.
2. Implement exact union semantics:
   - approved/eligible set items for the active set;
   - approved organization-owned symbols in the active organization where `organization_wide=true`;
   - de-duplicate by governed-symbol UUID.
3. A public symbol appears in the effective palette only when the active set references it; all Public Catalog symbols remain independently browseable/searchable under the Public Catalog scope.
4. Never include drafts, rejected revisions, cross-org private symbols, unavailable references, or withdrawn/deprecated public records unless an explicit historical/admin mode is requested.
5. Preserve explicit set-item ordering/grouping/preferred-format metadata. Put inherited organization-wide symbols in a deterministic Organization-wide group/position.
6. Keep unavailable set-item rows so builders can diagnose the gap; palette users receive only eligible symbols.
7. Add Symbol Set Builder search over Public Catalog plus authorized organization symbols, with server-side authorization and no client-only filtering.
8. Resolve active set in the required order: explicit eligible Set Code, user's last eligible set for the project, project default, organization default, then no active set.
9. Record transactional audit events for set mutations and context changes now. Stage 9 adds prospective product-usage events to these journeys; do not infer or fabricate historical tenant usage.

### Frontend tasks

- Build palette source badges such as `Set`, `Organization-wide`, and `Public`, without implying that every Public Catalog result is in the active palette.
- Build set-item search/filter, batch add/remove, drag/drop plus keyboard ordering, section/group editing, duplicate prevention, and counts by discipline/category/format, with loading, empty, inaccessible, unavailable, deprecated, unapproved, demoted, and archived states.
- Add accessible confirmation and non-destructive removal copy.

### Tests

- `tests/test_effective_palette.py`
- `tests/test_symbol_set_builder_api.py`
- `tests/test_symbol_set_tenant_isolation.py`
- `frontend/src/effectivePalette.test.js`
- `frontend/src/symbolSetBuilder.test.js`

Use a fixture matrix with two organizations, one set available to multiple projects, public symbols in/out of the active set, organization-wide private symbols, set-only symbols, drafts, defaults and explicit overrides, duplicate union paths, ordering/group metadata, and unavailable references.

### Acceptance

- Union, fallback resolution, ordering, and de-duplication are deterministic and pagination-safe.
- Public Catalog browsing remains independent and does not silently widen the active palette.
- No cross-organization private symbol can enter a set or palette.
- Removing an item never affects source symbol identity or publication.

## 13. Stage 7 — public contribution, promotion, withdrawal, and demotion

**Outcome:** reversible, auditable transitions between organization-private and public governance without bypassing human authority.

### Public-projection migration

- Extend `SymbolRevision.lifecycle_state` with `withdrawn`; demoting an organization-owned public governed-symbol UUID sets every revision of that symbol currently in `published` state to `withdrawn`. Separate organization-review approval records remain immutable evidence. Re-promotion requires a new public request/review for a newly approved target revision; the final successful handoff publishes only that target revision and creates/activates only its target projections. Every older revision remains `withdrawn` and every older projection remains `retired`.
- Add checked `publication_state = active | retired`, nullable retirement actor/time/reason to both `published_pages` and `pack_entries`. Backfill existing rows to `active` and retain server default `active` so prior publication writers remain compatible during rolling deployment; new writers set it explicitly.
- Replace `active_public_symbol_projections` so it requires public symbol visibility, `published` revision lifecycle, active page, active pack entry, public package audience, and published package status. All route, alias, asset, Hannah, Whitney, and other background/public readers continue to join this view.
- Keep `publication_packs.status` package-wide. Demoting one symbol retires every active page/pack-entry projection for that governed-symbol UUID across every revision and every affected pack; unrelated active projections in each multi-symbol pack remain public and that pack remains `published`. Set an affected package to `retired` only when no active page/entry remains. Never delete page, entry, package, alias, or revision history, and never reactivate older projections during re-promotion.

### State-machine tasks

1. Define explicit promotion request states aligned with the specification and existing public workflow: `submitted`, `triage`, `in_review`, `changes_requested`, `accepted`, `rejected`, `withdrawn`, plus a terminal publication outcome where needed; enforce idempotency and one active request per revision.
2. Promotion submission snapshots the organization-approved revision, proposed public metadata, reason, explicit acknowledgment that acceptance shares it freely with the Symgov community, provenance, rights evidence, requester, and trace ID. Do not expose project names, set composition, private source documents, or unrelated organization data to public review.
3. Feed the existing public review/publication pipeline; do not create an autonomous shortcut. Tracy rights/licensing conflicts and existing human approval remain blockers.
4. Refactor the current publication handoff for organization contributions to require the exact existing governed-symbol UUID and organization-approved revision UUID. Reject organization/approval/revision mismatches and never resolve or create an organization contribution by mutable/global slug or service-user ownership.
5. Set `visibility=public` only in the final successful public publication transaction/handoff contract; publication must not create a second governed symbol.
6. Organization Admin may withdraw a still-pending request without changing current visibility.
7. Demotion begins as a request and impact preview. Enumerate every revision of the governed-symbol UUID, external-org set references, favourites, packages/pages/entries across all revisions and packs, API identifiers, links, object-delivery exposure, and caches. Fail closed if the Stage 5 visibility floor, complete reader deployment, or private object delivery cannot be proven for the target.
8. Human Platform Admin in active Symgov context, with recent step-up and reason, executes approved demotion/withdrawal. Ownerless legacy public symbols cannot become private.
9. Execute demotion in fail-closed order: verify the private delivery/read policy and visibility-floor deployment; lock the symbol, every `published` revision for its governed-symbol UUID, every matching active page/entry projection across all revisions/packs, and each affected package row; transactionally set symbol visibility to `organization_private`, every such published revision to `withdrawn`, and every active target-symbol page/entry projection to `retired`; retire each package only if no active projections remain; then purge/invalidate application/CDN caches and short-link projections. A pre-commit failure leaves every state unchanged. A post-commit purge failure leaves the symbol private, blocks reads through `active_public_symbol_projections`, raises an operational alert, and is retried—it must never roll visibility back to public automatically.
10. Cross-org set/favourite rows remain historical but become unavailable/hidden according to privacy rules; do not leak the new private owner through public error details. Previously downloaded files are not recalled or rewritten; future direct or signed URLs must expire or be denied under current visibility.
11. Public Catalog attribution displays the contributing organization/company only, never the individual employee.

### Likely modules/tests

- `public_contributions.py`
- additions to publication handoff/runtime only at the narrow transition point
- organization/public routes and schemas
- `tests/test_public_contribution_workflow.py`
- `tests/test_symbol_demotion.py`
- `tests/test_publication_visibility_transitions.py`
- `tests/test_public_projection_migration.py`
- `tests/test_demotion_asset_cache_safety.py`
- `tests/test_background_readers_after_demotion.py`
- existing publication, feedback-without-unpublication, attribution, Catalog identity, search/detail/download regressions

Migration/integration fixtures must prove the current pre-stage (and therefore at/above-floor) publication writer can still insert active pages/entries after upgrade, new writers set explicit state, and flags-off rollback to the exact visibility-floor release works against the additive schema. Add one exact transition fixture in which a governed symbol has two published revisions in different multi-symbol packs: demotion must withdraw both revisions and retire every page/entry for both revisions while unrelated symbols in both packs remain queryable and pack counts remain correct; list/detail/search/facet/count/page/package/download routes, aliases, assets, Favorites, Hannah, and Whitney must all exclude the demoted UUID. Repeat that complete exclusion/count/alias/asset/Hannah/Whitney matrix after switching web/API/background code to the exact visibility-floor rollback release. Re-promotion through a fresh public workflow must publish/activate only a newly approved target revision/projections, with both older revisions still withdrawn and all older projections still retired. A fully retired pack must disappear. Do not run or claim a pre-floor reader rollback after private or demoted data exists.

### Acceptance

- No agent or Organization Admin can self-publish.
- Promotion/demotion is idempotent, actor-attributed, auditable, and reversible where policy permits; multi-symbol packs retain every unrelated active page/entry.
- Review requests never unpublish an existing public symbol.
- Demotion never exposes private metadata to former consumers, including through pages, packages, Hannah/Whitney readers, assets, aliases, Favorites, direct delivery URLs, or stale caches; history remains retained but outside the active public projection.

## 14. Stage 8 — Catalog, favourites, and integrated organization UI

**Outcome:** the signed-in organization experience exposes private organization symbols and public Catalog symbols with correct context and privacy.

### Backend tasks

- Extend the Stage 5 tenant-aware visibility policy and existing authenticated browser Catalog/published list/detail/asset responses to include authorized organization-private records when the session is organization-bound; do not fork the two existing Catalog read models or weaken the already-proven public/demotion filters.
- Keep personal sessions public-only.
- Keep Catalog API-key routes public-only.
- Apply visibility before search, facets, counts, pagination, downloads, favourites, and asset lookup.
- Favourites remain keyed to user and governed symbol. Hidden/demoted private symbols outside the active owner organization are never returned, even though current behavior deliberately lists stale unpublished IDs; historical rows remain for restoration/audit and may be safely removed by their owning user without exposing hidden symbol details.
- Return bounded source/provenance/context fields sufficient for UI badges, not internal tenant data.
- Ensure canonical resolver aliases cannot bypass visibility.

### Frontend tasks

- Show organization icon/name in the header.
- Carry project/set context into palette and workbench without putting authority in query parameters.
- Label private organization symbols versus Public Catalog records consistently.
- Add contribution/status pages, personal recently-used shortcuts, stable ID/revision/format/standards/company-attribution/usage details, and clear `Public`, `Organization Private`, `Organization-wide`, `Draft`, `Deprecated`, `Demoted`, and unavailable states.
- Preserve personal mode, current Favorites semantics, mandatory PIN routing, canonical links, and accessible loading/empty/error states. Change new and affected user-facing copy to American English while retaining compatibility identifiers internally.
- Use locale-aware date/number formatting and the accepted translated-symbol-metadata contract without translating customer organization/project/set content automatically.
- Continue extracting pure helpers/components rather than growing `App.jsx` indiscriminately.

### Tests

- `tests/test_catalog_organization_context.py`
- `tests/test_catalog_favourites_visibility.py`
- `tests/test_catalog_private_asset_access.py`
- canonical resolver/search/detail/download regressions
- `frontend/src/catalogOrganizationContext.test.js`
- `frontend/src/organizationHeader.test.js`
- existing Catalog/favourites/workbench tests

### Acceptance

- The same URL under different authorized sessions never leaks another organization's private result.
- Facet totals and pagination cannot reveal hidden records.
- Public/API behavior remains compatible.
- Organization/private status is clear and accessible in the UI.

## 15. Stage 9 — usage telemetry and contribution reputation

**Outcome:** privacy-bounded, server-derived organization dashboards and fair contribution indicators.

### Schema/service tasks

- Add an append-only organization product-usage event table with event type, pseudonymous/internal user, session mode, organization, project/set where available, symbol/revision/format, source, outcome, coarse query/filter metadata, trace ID, and timestamp.
- Do not repurpose API-key usage rows for browser events.
- Validate an allowlist of events: personal/organization session start, organization selection/creation, icon generated/uploaded/removed, role/capability/platform-role changes, project create/update/archive/select, context resolution, browse/search/zero-result, symbol preview/download and format, Favorite add/remove, set create/update/copy/item add/remove/select, private draft/review, promotion request/outcome, public governance outcome, demotion request/outcome, and agent finding lifecycle.
- Derive principal/context server-side and emit in the same transaction for governance mutations; low-value browse events may use a bounded durable ingestion path with documented loss behavior.
- Add aggregation services for Organization Admin and Platform Admin. Default dashboards to aggregate counts; restrict raw per-user browsing visibility.
- Cover adoption, searches/zero results/conversion, previews/download formats, set membership and organization-wide use, organization/public review queue size/age/turnaround, contribution acceptance/demotion/downstream adoption, generated/uploaded icon status, admin/reviewer/Platform Admin continuity, and agent finding severity/age/outcome.
- Build a contribution read model from immutable events and append-only correction/reversal records. First expose accepted contributions, acceptance rate, public symbols, reviews, cross-organization adoption, reuse, completed sets, turnaround, and badges such as First Contribution, Contributor Organization, Multi-Discipline Contributor, Metadata Improver, and Community Partner. Add points/opt-in leaderboards only after an accepted, versioned scoring policy.
- Reputation is informational and must not grant roles, approve reviews, or publish.
- Enforce anti-gaming rules: no credit for raw uploads, likes, self-downloads, or same-organization activity alone; deduplicate before review; hide point values from reviewers; cap prompt responses; rate-limit repeated submissions; flag suspicious linked accounts/organizations without automatic punishment.
- Define retention periods and minimum aggregation thresholds separately for product analytics, operational logs, security signals, audits, and reputation.

### Tests

- `tests/test_organization_usage_events.py`
- `tests/test_organization_usage_api.py`
- `tests/test_contribution_reputation.py`
- `frontend/src/organizationUsage.test.js`
- `frontend/src/contributionReputation.test.js`

Prove no raw query/private description/prompt/image/credential leakage, spoofed context rejection, aggregation thresholds and tenant isolation, idempotent governance events/reversals, bounded cardinality/retention, self-use exclusion, fair normalization/versioning, and no authorization dependence on score.

### Acceptance

- Admin dashboards are useful without becoming employee surveillance.
- Audit and analytics purposes remain distinct.
- Score changes are reproducible from a named policy version.

## 16. Stage 10 — organization agent oversight

**Outcome:** the logical Organization Steward and Platform Governance capabilities produce structured, reviewable, correctly scoped findings without direct governance authority. Their final agent names remain the specification's O4 decision and must not be invented in code.

### Prerequisite

F0.6 routing policy is complete. Hermes remains the target runtime; do not introduce or configure Ollama for this programme.

### Schema/runtime tasks

- Add organization agent configuration/enablement records, runs, and structured findings, or extend existing agent definition/queue records only where tenant and trace isolation can be proven.
- Apply I-21 explicitly: database configuration stores allowlisted logical `model_alias` and policy; the active Hermes `symgov` profile is the resolver; each queue item/run snapshots organization, optional project/set, capability/agent slug, alias plus resolved provider/model, prompt/policy version, trace ID, status, timestamps, and bounded error metadata. Legacy OpenClaw `model_profile` and current `AgentDefinition.model` do not become competing authority.
- Organization Steward performs deterministic-first reviewer-coverage, backlog, icon, project/set health, and unresolved-reference analysis inside one organization. Platform Governance monitors eligible Platform Admin continuity, duplicate-organization indicators, cross-tenant authorization failures, and unresolved governance exceptions from approved platform reports.
- Agent tools expose scoped read models/reports, not unrestricted ORM sessions or cross-tenant tables.
- Validate model output against strict schemas. Findings use the accepted I-22 severity/status vocabulary and deterministic fingerprint over capability, scope, finding type, target IDs, and policy version. Retries/upserts cannot create duplicate active findings; findings are recommendations requiring human acknowledgement, dismissal, resolution, or supersession with actor/timestamps.
- Route accepted escalation recommendations through durable Symgov workflow to Ed/Alfi as appropriate; never let a model send external messages, alter gateways, or bypass the human authorization of the resulting action.
- Platform Admin configures an allowlisted default model in active Symgov context with step-up and audit. Secrets remain outside the repository and never appear in findings.
- Support explicit cadence/trigger records, disable/pause, retry, idempotency, stale-snapshot detection, optional assignee/issue mapping, retention, and traceable human response.

### UI/tests

- Add organization findings/insights dashboard with severity, evidence, model/policy version, stale status, acknowledgement, dismissal, and action links.
- Add Platform Admin model/agent configuration UI with clear operational state.
- Test tenant-scoped fixtures, malformed output, prompt injection in symbol metadata, retries, duplicate deliveries, stale snapshots, disabled agents, model fallback, queue crash recovery, and human-only decisions.
- Run external workspace partitions because agent wiring changes.

### Acceptance

- Agents cannot publish, approve organization review, demote, grant roles, or mutate sets directly.
- Every finding is attributable and scoped.
- Failure is visible and recoverable; no silent success.

## 17. Stage 11 — integrated hardening, migration rehearsal, and controlled release

**Outcome:** production-ready source and an explicit, reversible rollout package. This stage does not itself authorize deployment.

### Integrated verification

1. Build a complete two-organization adversarial fixture with personal, Organization User/Admin/reviewer, Platform Admin, inactive/suspended, and API-key principals.
2. Run route-policy inventory and tenant isolation matrices for list/detail/search/count/asset/download/mutation endpoints.
3. Exercise full journeys:
   - personal login and public Catalog;
   - one/multiple organization login and forced PIN change;
   - project/set creation and active selection;
   - private draft → organization review → set/palette;
   - public contribution → human governance → Catalog;
   - feedback without unpublication;
   - demotion impact/approval/privacy;
   - favourites across personal/owner/other-org sessions;
   - usage/reputation;
   - agent finding → human response.
4. Run focused tests, all full wrappers, isolated build, the reviewed repository-owned/pinned added-line secret scan, and `git diff --check`. Run the Stage 0-frozen workspace-clean compile gate exactly as follows; do not substitute plain `python -m compileall`, leave bytecode in the workspace, or claim unspecified static/lint tooling:

    ```bash
    (
      set -eu
      pycache_dir="$(mktemp -d)"
      trap 'rm -rf "$pycache_dir"' EXIT
      PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m compileall -q backend/symgov_backend scripts
    )
    ```
5. Run representative indexed query/load evidence for the provisional P95 targets and record dataset/hardware assumptions. Fail closed under authorization dependency errors; prove advisory agents do not block core paths.
6. Run keyboard, screen-reader-semantic, responsive viewport, focus/error/status, American-English-copy, and locale-aware date/number checks for every new flow.
7. Obtain fresh immutable Stage 1 specification review and Stage 2 security/code-quality review of the exact release candidate. Any defect invalidates downstream approval and creates correction/fresh-review work.

### Disposable PostgreSQL rehearsal

Against a production-shaped disposable database only:

- restore/migrate from the pre-organization production revision through every new head;
- inventory users/public symbols/canonical IDs before change;
- run Symgov organization bootstrap in dry-run then apply with expected hash;
- verify legacy ownerless public symbols and personal accounts;
- seed two organizations and execute the isolation/journey suite;
- before private rows exist, rehearse schema-first pre-floor rollback with flags off and zero private rows; after private/demoted rows exist, rehearse flags-off rollback only to the exact visibility-floor release and prove the complete route/background-reader matrix;
- rehearse emergency pre-floor recovery ordering: external denial of public Catalog/published/page/package/download/asset/alias/Favorite routes, then stop/drain of all web/API/Hannah/Whitney/other readers, with no route reopening or reader resume until an at/above-floor release is restored and verified;
- downgrade only migrations whose contract says downgrade is safe before tenant data, then upgrade again;
- prove one Alembic head, constraints, indexes, and no orphan/cross-tenant references;
- redact database URLs and user/tenant private data from evidence.

### Rollout plan requiring separate authorization

1. Back up database and object storage; verify restore command in disposable environment.
2. Deploy migrations/additive backend with all organization flags off.
3. Run read-only inventory and reviewed Symgov bootstrap; apply only with explicit authorization.
4. Deploy frontend hidden behind flags.
5. Enable the backend pilot allowlist for the active `symgov` organization only. Active pilot-organization membership and effective role/capability are the user restriction; there is no separate undocumented named-user allowlist. Verify `/auth/me` capability negotiation before exposing UI navigation.
6. Smoke login/session, admin, project/set, private symbol, Catalog, audit, queue, email, liveness/readiness, and rollback.
7. Expand to another organization only after recorded go/no-go.
8. Roll back first by disabling flags and deploying only a verified release at or above the Stage 5 visibility floor, then roll forward. Do not drop used tenant data in production and do not revert readers below the floor once private semantics/data exists.
9. If emergency recovery absolutely requires a pre-floor backend, first deny at external ingress all public Catalog/published/page/package/download/asset/alias/Favorite routes; then stop and drain every web/API and background reader, explicitly including Hannah and Whitney, before starting the older backend. Keep those routes denied and readers stopped. Never reopen or resume them until a release at or above the floor is restored and the full visibility, demoted-revision, multi-symbol-pack, count, alias, asset, Favorite, Hannah, and Whitney checks pass.
10. Keep the legacy protected-owner safeguard until a separate reviewed cutover proves the reserved organization, eligible Platform Admin continuity, step-up, recovery, and rollback. Do not retire it automatically as part of initial enablement.

Production topology is externally managed and the repository does not prove the currently deployed release. Before any authorized rollout, inspect the live deployment/release identity and external nginx/Compose/service configuration rather than assuming repository `main` is live.

### Final acceptance checklist

- [ ] Every I-01–I-25 implementation decision and specification O1–O6 decision has an accepted source, without confusing them with the specification's D1–D19 product decisions.
- [ ] Existing F0.5 account-security and F0.6 routing blockers are closed at their required boundaries.
- [ ] Organization and platform roles are independent and session-scoped.
- [ ] Forced-PIN ordering, selection challenges, and protected-owner cutover follow their accepted contracts.
- [ ] Selection-challenge TTL/attempt/consume/revoke races and session-bound step-up freshness/protected mutations follow exact I-25 boundaries.
- [ ] Active organization is immutable inside a full session.
- [ ] Zero/one/many memberships, bounded selection, duplicate-membership races, and last-admin protection hold under concurrency.
- [ ] Last-eligible-Platform-Admin and protected Symgov organization invariants hold under concurrency.
- [ ] Reserved code `symgov`, commercial display-code grammar, lowercase normalized uniqueness, and pilot-allowlist matching follow I-24.
- [ ] Projects and sets are tenant-scoped; sets can serve several same-organization projects; one active set exists per user/project.
- [ ] Rolling set items and historical used-revision evidence follow accepted I-23; only future immutable releases pin revision UUIDs.
- [ ] Effective palette is active-set items plus approved organization-wide symbols, deterministic, de-duplicated, and private-safe; Public Catalog stays independently browseable.
- [ ] Organization approval is revision-specific and never public by implication.
- [ ] Promotion/demotion preserves human authority, identity, provenance, and audit.
- [ ] Catalog search/count/facet/favourite/asset paths enforce visibility before output.
- [ ] API-key Catalog behavior remains public-only unless separately specified.
- [ ] Icons cannot execute active content or exhaust resources.
- [ ] Usage/reputation is privacy-bounded and never authorization input.
- [ ] Organization Steward/Platform Governance are scoped recommenders with structured findings and no direct authority.
- [ ] Backend-authoritative flags, pilot allowlist, disabled responses, UI capability negotiation, and kill switches are proven.
- [ ] Provisional performance evidence, accessibility/responsive checks, American-English UI copy, and locale-aware formatting pass.
- [ ] Migration rehearsal, restore, at/above-visibility-floor rollback, and externally denied/stopped emergency pre-floor recovery are proven with demoted prior revisions and all public/background reader surfaces.
- [ ] Full backend/external/frontend/Langfuse/build gates pass on the frozen candidate.
- [ ] No production migration, deployment, publication, withdrawal, gateway, or service action occurred without explicit authorization.

## 18. Requirement coverage map

| Spec area | Plan stage |
|---|---|
| Organization entity, uncapped zero/one/many membership, Admin/User/capabilities | 0–3 |
| Organization selection during login; no org switching | 1–2 |
| Symgov Platform Admin boundary and step-up | 1–3 |
| Organization branding and safe icons | 3 |
| Projects, many-to-many set availability, defaults, and one active Symbol Set context | 4–6 |
| Symbol Set create/copy/manage/items/order/groups and future-capable storage | 4–6 |
| Effective palette: eligible active-set items (public/private) + organization-wide; separate Public Catalog scope | 6 |
| Private organization symbols, review, and shared visibility foundation | 5 |
| Promotion/demotion and provenance | 7 |
| Catalog/favourites/integrated UI | 8 |
| Usage events, dashboards, contribution recognition | 9 |
| Organization Steward/Platform Governance model aliases, runs, findings, audit | 10 |
| Feature flags, migrations, security, performance, testing, release | 0–11 |
| Future Symbol Set releases/imports | Explicitly deferred; Stage 4 schema must not block them |

Acceptance-criterion traceability:

| Spec acceptance IDs | Primary plan evidence |
|---|---|
| AC-01–AC-03 personal/multi-organization sign-in and tenant rejection | Stages 1–2 and 8 |
| AC-04–AC-08 first/last organization admin, protected Symgov org, Platform Admin eligibility/continuity | Stages 1–3 |
| AC-09 generated/uploaded icon fallback | Stages 1 and 3 |
| AC-10–AC-11 all-member project selection and 50-character description | Stage 4 |
| AC-12–AC-14 mixed sets, one active set, and organization-wide inclusion | Stages 4–6 |
| AC-15 explicit appointed-reviewer approval | Stage 5 |
| AC-16–AC-19 same-ID publication, company attribution, demotion, and safe cross-tenant references | Stages 7–8 |
| AC-20–AC-21 scoped advisory agents and prompt-injection resistance | Stage 10 |
| AC-22 session/context/download telemetry | Stage 9 |
| AC-23 American-English product UI | Stages 3–11, especially 8 |
| AC-24 online-only initial operation | Explicit exclusion and Stage 11 checks |

## 19. Explicit exclusions for this programme

Unless separately accepted, do not add:

- billing, organization subscription purchase, seat management, or invitation email flows;
- organizations as a replacement for personal Free/Plus accounts;
- public Organization Catalog pages;
- organization-scoped Catalog API keys;
- automatic publication, demotion, role grant, or approval by an agent;
- cross-organization private symbol sharing;
- mutable reputation balances or reputation-based authorization;
- future Symbol Set release/import tables before a release workflow is specified;
- offline Symbol Set packages, offline claims, or offline synchronization in the first implementation;
- clean BrowserRouter deployment changes unrelated to the existing canonical-route Release B plan;
- Ollama or legacy OpenClaw voice/speech mechanisms.

## 20. Risks to keep visible

1. **Tenant leakage:** search/facets/counts/assets are as sensitive as detail endpoints; test all of them.
2. **Role collision:** current global `admin/reviewer/submitter` semantics cannot be casually reinterpreted as organization authority.
3. **Session ambiguity:** never issue a privileged unbound session while organization choice is pending.
4. **Migration collision:** canonical-ID completion likely owns the next revision after `0026`.
5. **Review-state conflation:** organization approval and public publication must remain separate records/state machines.
6. **Demotion blast radius:** external sets, favourites, packages, aliases, API consumers, and caches need explicit impact handling.
7. **Icon active content/resource exhaustion:** normalize to safe raster derivatives.
8. **Large frontend shell:** extract pure helpers/pages to avoid making `App.jsx` untestable.
9. **Analytics surveillance/cardinality:** aggregate by default and avoid raw search text.
10. **Agent prompt injection/authority creep:** scoped read models, schema validation, deterministic checks, and human action gates.
11. **Rollback illusion:** once private semantics/data is live, disable features and roll forward or remain at/above the Stage 5 visibility floor; a pre-floor backend is usable only while public routes are externally denied and all readers are stopped/drained.
12. **Context exhaustion:** one stage per Luna context; update the compact resume after every stage and do not re-litigate accepted prior decisions.
