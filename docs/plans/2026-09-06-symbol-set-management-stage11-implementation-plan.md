# Symbol Set Management — Product Stage 11 Implementation Plan

**Status:** Drafted 2026-09-06. Not started. No implementation, migration, commit (of anything beyond this plan and the reconstructed kickoff prompt), push, deployment, or feature-activation has occurred.

This plan does not itself authorize deployment, migration, or any production mutation — per programme plan §17's own text, Stage 11 produces "production-ready source and an explicit, reversible rollout package," and every actually-mutating step described below (§ "Rollout plan requiring separate authorization") requires Chris's separate, explicit go-ahead at the time it happens.

## 1. Repository baseline and current-state evidence

### 1.1 Repository state at drafting time (2026-09-06)

- Branch `main`, local `HEAD` `d5fdcd2` ("docs: record Stage 10 commit hash in plan doc"), fully pushed (`origin/main` 0 ahead/0 behind).
- Sole Alembic head: `20260905_0044`.
- Tracked tree clean except the pre-existing, unrelated `.claude/settings.local.json` diff — leave untouched, per every prior stage's own baseline note.
- Feature flags (`organizations_enabled`, `organization_admin_enabled`, `organization_custom_icons_enabled`, `organization_icon_upload_enabled`, `platform_admin_enabled`, `symbol_sets_enabled`, `organization_symbols_enabled`) all default to off (`backend/symgov_backend/settings.py:145-179`) and are environment-variable-controlled. **This repository cannot prove what is actually live in any deployed environment** — §17 itself says so explicitly ("Production topology is externally managed and the repository does not prove the currently deployed release," programme plan line 1033) — so before any rollout-plan step is executed, re-verify the live flag state and deployed release identity out-of-band rather than trusting repo defaults or any prior session's notes.

### 1.2 Stages 1–10 are complete; this stage does not redo their work

All ten prior stages are committed with recorded closeout hashes (see `docs/plans/2026-09-06-symbol-set-management-stage11-kickoff-prompt.md` for the full stage-by-stage ledger and the consolidated list of items each stage explicitly deferred). Stage 11's integrated-verification work package re-exercises this existing functionality end-to-end; it does not rebuild it. Deferred items carried forward (icon-upload malware scan, promotion reject/changes-requested decisioning, contribution points/badges, `agent_findings` retention policy, the Organization Steward/Platform Governance Hermes binding, etc.) are **verified as still-intentional and still-documented**, not silently closed, unless a specific work package below says otherwise.

### 1.3 The §17-required secret-scan gate does not exist yet — this is a real prerequisite gap, not an oversight to route around

Programme plan line 328 states: *"Create and review a repository-owned added-line secret-scan script or pin an installed scanner and exact invocation before Stage 11. Do not call an unspecified 'static check' or 'secret scan' a passing gate."* A repository-wide search (`find . -iname "*secret*scan*"`, plus a grep across every `docs/plans/*.md` file) found **no such script or pinned-scanner invocation anywhere in this repository or its plan history** — no prior stage built it. This must be resolved as an actual Stage 11 work package (WP11.2 below), not assumed complete or waved through as "a secret scan was run" without a concrete, reviewed, reproducible invocation.

### 1.4 Disposable-Postgres harness precedent already exists — reuse it, do not reinvent it

`tests/test_f0_5_postgresql_security.py` and `tests/test_wp95_contribution_reputation_postgresql.py` already establish the pattern for tests that require real Postgres (not SQLite) semantics — this is the same class of cross-table/lock/trigger behavior §17's migration rehearsal needs. Confirm the exact harness mechanism (how the disposable instance is provisioned, connected to, and torn down) from these files before building anything new for WP11.8.

### 1.5 Existing test/build commands (do not substitute or invent alternatives)

- Backend: `./scripts/test-backend.sh` (portable suite, `-m "not external_workspace"`), `./scripts/test-backend.sh --external` (external-workspace suite), `./scripts/test-backend.sh --full` (both) — per `backend/README.md:100-107`.
- Frontend: `npm run test:frontend` (`./scripts/test-frontend.sh`).
- Frontend build: `npm run build` (plain) or `npm run build:isolated` (`./scripts/build-frontend-isolated.sh`) — per CLAUDE.md, do not run `npm run build:publish` or `npm run publish:static` without Chris's explicit approval.
- Frozen compile gate (programme plan lines 992-1000, verbatim, do not substitute plain `python -m compileall`):

    ```bash
    (
      set -eu
      pycache_dir="$(mktemp -d)"
      trap 'rm -rf "$pycache_dir"' EXIT
      PYTHONPYCACHEPREFIX="$pycache_dir" python3 -m compileall -q backend/symgov_backend scripts
    )
    ```
- `git diff --check` for whitespace/conflict-marker hygiene on the actual diff being verified.

### 1.6 `symgov-stage6-fixes` worktree — triage outcome (already performed 2026-09-06)

Full detail is in the kickoff prompt doc; summary for this plan's work-package mapping:

| Item | Disposition | Target work package |
|---|---|---|
| Category/discipline/format search filters on `GET /builder-search` | Port (safe, orthogonal) | WP11.1 |
| Human-readable `displayId` surfacing (`symbol_identity.py`) | Port (safe, orthogonal; also serves CLAUDE.md's human-readable-ID rule) | WP11.1 |
| ETag/428 optimistic-concurrency guard on `replace_items` | Port (safe, orthogonal; fixes a real lost-update race) | WP11.1 |
| Same-organization approved private symbol as a direct `SymbolSetItem` (`symbol_eligibility.py`) | Port — **Chris decided to loosen Stage 6's public-only restriction** to match the original programme-plan wording; cross-organization isolation must be re-verified, not weakened | WP11.1 |
| `eligible_organization_private_symbols` refactor | Port only if convenient alongside the above (no behavior change) | WP11.1 |
| Frontend wiring (`api.js`, `EffectivePalettePanel.js`, `SymbolSetBuilderPanel.js`, tests) | Not yet deeply reviewed — verify completeness during WP11.1, don't assume it's finished | WP11.1 |

### 1.7 Naming collision to avoid: "Stage 1/Stage 2 review" is not this programme's Stage 1/Stage 2

Programme plan line 1003 requires *"fresh immutable Stage 1 specification review and Stage 2 security/code-quality review of the exact release candidate."* This refers to the Hermes `symgov-feature-implementation` skill's own Kanban review lanes (implementation → Stage 1 spec-compliance review → Stage 2 security/code-quality review → final verification), **not** this programme's own Stage 1 (organization schema/bootstrap) or Stage 2 (org-bound auth) work. Anyone executing this plan should keep that distinction explicit to avoid confusing "re-review the release candidate" with "redo programme Stage 1/2."

### 1.8 Organization Steward / Platform Governance Hermes binding is out of this plan's scope

The binding decisions (reuse Ed, Platform Admin scope accepted for now, escalation assignee = Chris's account, daily cron cadence) are fully specified in the kickoff prompt doc and are being handled directly between Chris and Hermes Agent, outside this repository's commits. This plan's only obligation regarding Organization Steward/Platform Governance is to **verify** the repo-side behavior in WP11.4/WP11.5 (deterministic-only, no direct authority, attributable/scoped findings) — not to build or wire the binding itself.

## 2. Work-package sequence

Dependencies run top to bottom; later packages assume earlier ones are green.

### WP11.1 — `symgov-stage6-fixes` worktree integration

- Use the Hermes skill `symgov-uncommitted-worktree-integration` to build a clean integration branch from exact `origin/main` (not a copy of the stale worktree).
- Port, by explicit path allowlist, only the unique bytes identified in §1.6: search filters, `displayId` surfacing, the ETag guard, and the private-set-item loosening (including its `symbol_eligibility.py` helpers).
- Fix the worktree's own known wiring gap: its tenant-isolation test helpers don't send the new ETag header, causing two tests to fail against the ETag guard — update those call sites, don't weaken the guard.
- Add or update tenant-isolation tests proving the loosened rule **only** widens same-organization access: an approved private symbol from Organization A must remain unavailable as a direct set item to Organization B, exactly as today, while becoming addable within Organization A's own sets.
- Update the effective-palette description at programme plan line 1048 ("Effective palette is active-set items plus approved organization-wide symbols... Public Catalog stays independently browseable") to reflect the widened active-set-item scope, and note this as a Stage 11 amendment in this plan's decisions log (§4).
- No forked/duplicate Alembic revision — confirm no migration is actually needed for this package (the underlying `SymbolSetItem`/constraint change, if any, must linearize onto the current single head).
- Full regression (§1.5 commands) must stay green after integration, plus the new/updated tests above.

### WP11.2 — repository-owned added-line secret-scan gate

- **Decided (2026-09-06): build a repository-owned custom script, not a pinned external scanner.** No scanner is currently installed anywhere in this environment (verified: no `gitleaks`/`detect-secrets`/`trufflehog` binary, no matching pip/npm dependency) — a custom script keeps this dependency-free and fully auditable, consistent with CLAUDE.md's preference against unspecified tooling.
- Implement as a small script (e.g. `scripts/secret-scan-added-lines.sh` or `.py`) that runs against `git diff` **added lines only** (not the whole tree) between a given base and the candidate, checking each added line against a curated pattern list: AWS access/secret keys, generic API-key/token-looking strings, private-key PEM headers, JWTs, database connection strings with embedded credentials, and common cloud-provider secret formats. Keep the pattern list itself in the script (or an adjacent reviewable file) so it can be extended without redesigning the mechanism.
- Document the exact invocation in this plan once written, so "ran a secret scan" is never again an unspecified claim.
- Run it against the current `main` tip as a baseline (expect clean; if not, that is its own finding to report, not to silently fix as part of this package).

**Done (2026-09-06).** Implemented as `scripts/secret_scan_added_lines.py` — dependency-free, stdlib-only. Exact invocation, from the repository root:

```bash
python3 scripts/secret_scan_added_lines.py --base origin/main
```

(`--base` accepts any ref; `--candidate` defaults to the current working tree and also accepts a ref, for checking a specific commit instead of the working tree.) Exit code `0` = clean, `1` = at least one finding, each printed as `<path>:<line>: <pattern name>: <redacted excerpt>` on stderr. Only lines a diff actually *adds* are checked — never a whole-tree scan. Pattern list (AWS access/secret keys, Google API keys, GitHub/Slack tokens, private-key PEM headers, JWTs, database URLs with embedded credentials, a generic key/token/password assignment pattern with a placeholder-value allowlist) lives at the top of the script itself, reviewable and extensible in one place. A small, reviewable `EXCLUDED_PATHS` set (added once WP11.3's own tests hit this exact case) excludes only this scan's own test-fixture file, whose entire purpose is synthetic secret-shaped data — never used to silence a real finding elsewhere.

Proof it actually detects a real secret and passes clean on the real candidate: `tests/test_secret_scan_added_lines.py` — seven tests, including one true end-to-end CLI invocation against a disposable git repository (proves the exit-code contract, not just the internal function) and one that runs the actual script against this repository's real `origin/main` diff and asserts a clean exit. Run against the WP11.1 release candidate (`python3 scripts/secret_scan_added_lines.py --base origin/main` from repo root, 2026-09-06): **clean, exit 0** — no findings.

### WP11.3 — two-organization adversarial fixture and route-policy/tenant-isolation matrices

- Build a fixture covering every principal type named in §17: personal, Organization User/Admin/reviewer (per org), Platform Admin, inactive/suspended accounts, and API-key principals, across two organizations.
- Run a route-policy inventory and tenant-isolation matrix for every list/detail/search/count/asset/download/mutation endpoint touched by any stage 1–10 feature, including the WP11.1-widened private-set-item path.
- Every unauthorized cross-tenant access attempt must 404 (not 403), consistent with the precedent Stage 10's own tests set (`test_organization_findings_are_not_visible_to_a_different_organizations_admin`).

**Done (2026-09-06).** Full audit: `docs/plans/2026-09-06-stage11-wp11.3-route-policy-tenant-isolation-matrix.md` — every one of the 161 unique `/api/v1/*` routes, grouped by tag family, with policy classification, tenant-scope note, and citation of the Stage 1–10 test file(s) that already prove it (this package does not rebuild what already passes). Five genuine gaps were found and closed by the new consolidated fixture, `tests/test_stage11_wp11_3_adversarial_fixture.py` (13 tests, real disposable Postgres): non-admin `symbol_reviewer`-capability write-denial under the WP11.1 widened path; a plain Organization User (not just Admin) 404s on cross-org Symbol Set by-ID/items/projects; Platform Admin's org-scoped `symgov` session cannot reach another organization's org-scoped routes (with a positive control proving the same principal's actual platform-level routes still work); an inactive membership resolves login to a personal session rather than a 401 or a bind into that organization; an API-key principal is rejected by every organization-scoped route tried and still reaches the public Catalog.

### WP11.4 — full end-to-end journey exercises

Exercise, against the fixture above, every journey programme plan §17 names: personal login and public Catalog; single/multiple-organization login and forced PIN change; project/set creation and active selection; private draft → organization review → set/palette (including the WP11.1-widened direct-item path); public contribution → human governance → Catalog; feedback without unpublication; demotion impact/approval/privacy; favourites across personal/owner/other-org sessions; usage/reputation; and agent finding → human response (Organization Steward/Platform Governance, verifying deterministic-only/no-direct-authority/attributable-and-scoped behavior per §1.8, without touching the Hermes binding itself).

### WP11.5 — full regression/build/secret-scan/compile gate suite

- Run every command in §1.5, including the now-built WP11.2 secret scan, plus `git diff --check`, against the exact release-candidate diff (everything from WP11.1 onward).
- Run the frozen compile gate exactly as specified — no substitutions.
- Record representative indexed query/load evidence for the provisional P95 targets. **Decided (2026-09-06):** assume a roughly production-scale dataset (on the order of a few thousand symbols across a handful of organizations) on whatever hardware this session runs on, and label the resulting numbers clearly as provisional/illustrative — not a capacity guarantee — per CLAUDE.md's rule against presenting illustrative values as real metrics. Prove advisory agents (Organization Steward/Platform Governance) do not block core paths under authorization-dependency failure (fail closed, not fail blocking).
- Run keyboard, screen-reader-semantic, responsive-viewport, focus/error/status, American-English-copy, and locale-aware date/number checks for every flow touched since Stage 10, including WP11.1's new UI surfaces.

### WP11.6 — fresh immutable spec-compliance and security/code-quality review

- Obtain the Kanban "Stage 1" (spec-compliance) and "Stage 2" (security/code-quality) reviews (see §1.7 for the naming clarification) of the **exact** release candidate assembled through WP11.5 — not an earlier snapshot.
- Any defect found invalidates downstream approval and creates correction/fresh-review work per programme plan line 1003 — do not patch around a finding without a fresh review cycle on the corrected candidate.

### WP11.7 — disposable-PostgreSQL migration rehearsal

**Decided (2026-09-06): use a local ephemeral Postgres container**, the same class of harness `test_f0_5_postgresql_security.py`/`test_wp95_contribution_reputation_postgresql.py` already use — fast, fully disposable, no external dependency. Against that database only, reusing the harness identified in §1.4:

- Restore/migrate from the pre-organization production revision through every current head; inventory users/public symbols/canonical IDs before change.
- Run the Symgov organization bootstrap in dry-run, then apply with expected hash; verify legacy ownerless public symbols and personal accounts remain correct.
- Seed two organizations and execute the WP11.3/WP11.4 isolation/journey suite against the disposable database.
- Before private rows exist: rehearse schema-first pre-floor rollback with flags off and zero private rows.
- After private/demoted rows exist: rehearse flags-off rollback only to the exact visibility-floor release, and prove the complete route/background-reader matrix at that floor.
- Rehearse emergency pre-floor recovery ordering: deny external access to every public Catalog/published/page/package/download/asset/alias/Favorite route, then stop/drain all web/API/Hannah/Whitney/other readers — no route reopening or reader resume until an at/above-floor release is restored and verified.
- Prove downgrade-then-upgrade is safe for any migration whose contract says downgrade is safe, before tenant data exists.
- Prove one Alembic head, correct constraints/indexes, and no orphan/cross-tenant references.
- Redact database URLs and user/tenant private data from all evidence produced by this package.

### WP11.8 — rollout-plan document (drafting only — no execution)

Produce the 10-step rollout plan as a reviewable document (backup/restore verification; flags-off migration deploy; reviewed bootstrap; flags-hidden frontend deploy; `symgov`-only pilot allowlist enablement with `/auth/me` capability-negotiation verification; smoke-test list; go/no-go-gated expansion; rollback procedure; emergency pre-floor recovery ordering; legacy protected-owner safeguard retained pending separate cutover) exactly matching programme plan lines 1020–1033. **Drafting this document does not execute any of its steps.** Each step's actual execution requires Chris's separate, explicit authorization at the time, per CLAUDE.md and §17's own text.

**Decided (2026-09-06): pilot-expansion targets/timing beyond the mandatory `symgov`-only first step stay intentionally open** — this is the furthest-downstream decision in the whole stage, and depends on every earlier work package being green first. Draft this package with the mandatory `symgov`-only pilot step fully specified, and explicitly mark "which organization(s) next, and when" as pending Chris's recorded go/no-go at the time WP11.8 is actually executed — do not invent a placeholder org or date.

### WP11.9 — final acceptance checklist walkthrough

Walk the 20-item checklist (programme plan lines 1035–1060) against the WP11.1–WP11.8 evidence, item by item, with evidence citations — not a bare checkbox. Flag any item that cannot be closed with real evidence rather than marking it done.

**Decided (2026-09-06): the other carried-forward gaps (icon-upload malware scan, contribution points/badges, `agent_findings` retention policy, promotion reject/changes-requested decisioning) are re-confirmed only, not closed, in this stage.** Matches §17's own checklist wording ("verify X holds," not "close every gap") and keeps Stage 11 scoped to hardening/release-readiness rather than new feature work. This walkthrough should explicitly state, for each such gap, that it remains an intentional, documented deferral beyond Stage 11 — not silently drop it from the record.

### WP11.10 — whole-stage closing audit

Scoped now, executed at actual closeout, mirroring the Stage 9 (WP9.9) and Stage 10 format: independently re-run the full regression suite, a Contract Review against every acceptance criterion, a Security Review, and an explicit list of anything still deliberately deferred beyond Stage 11 (distinct from anything accidentally missed).

## 3. Test-artifact inventory (expected, not exhaustive — confirm against actual code during each package)

- WP11.1: updates to `tests/test_symbol_set_tenant_isolation.py`, `tests/test_symbol_set_items.py`, `tests/test_symbol_set_builder_api.py`, `tests/test_effective_palette.py`; new/updated `frontend/src/symbolSetApi.test.js`, `effectivePalette.test.js`, `symbolSetBuilder.test.js`.
- WP11.2: `tests/test_secret_scan_added_lines.py` (done) — proves the gate detects an injected fixture secret (including one true CLI end-to-end invocation) and passes clean on the real candidate.
- WP11.3: `tests/test_stage11_wp11_3_adversarial_fixture.py` (done) — the two-organization, all-principal-types fixture, reusable for WP11.4's journey exercises.
- WP11.4: to be added when that package starts.
- WP11.7: disposable-Postgres rehearsal evidence (redacted), likely as a new `docs/plans/*-migration-rehearsal-evidence.md` or similar, following the redaction precedent of `docs/plans/2026-07-29-f0-4-*-evidence-redacted.json`.

## 4. Decisions confirmed with Chris (2026-09-06)

- Worktree triage dispositions: see §1.6 table.
- Loosen Stage 6's public-only Symbol-Set-item restriction to allow same-organization approved private symbols as direct set items, with cross-organization isolation re-verified (not weakened).
- Organization Steward/Platform Governance Hermes binding (out of this plan's own scope, tracked in the kickoff prompt): reuse Ed; accept full Platform Admin privilege for now, revisit before wider rollout; escalation assignee is Chris's own account; daily Hermes-side cron cadence.
- **WP11.2:** build a repository-owned custom added-line secret-scan script (no scanner is currently installed anywhere in this environment) rather than pin an external tool.
- **WP11.5:** assume a roughly production-scale dataset (a few thousand symbols, a handful of organizations) for provisional P95 performance evidence, labeled clearly as illustrative.
- **WP11.7:** rehearse against a local ephemeral Postgres container, matching the existing `test_f0_5_postgresql_security.py`/`test_wp95_contribution_reputation_postgresql.py` harness class.
- **WP11.9 scope:** the other carried-forward gaps (icon-upload malware scan, contribution points/badges, `agent_findings` retention policy, promotion reject/changes-requested decisioning) are re-confirmed as intentional deferrals only in this stage, not closed.
- **WP11.8 rollout timing:** pilot-expansion targets/timing beyond the mandatory `symgov`-only first step stay intentionally open until WP11.8 is actually executed, pending Chris's recorded go/no-go at that time.
- **WP11.1 amendment to the programme-plan acceptance checklist (line 1048):** `docs/2026-08-10-symbol-set-management-implementation-plan.md` itself is a frozen controlling-scope document cited by exact line number throughout this plan and the kickoff prompt, so it is intentionally left unedited (editing it would shift every downstream line-number citation). Recorded here instead: the checklist item at line 1048 ("Effective palette is active-set items plus approved organization-wide symbols... Public Catalog stays independently browseable") should now be read as satisfied by the widened definition — "active-set items" includes both public `SymbolSetItem`s and approved same-organization `organization_private` items (never cross-organization) — per `symbol_eligibility.py` and the updated module docstring in `effective_palette.py`. WP11.9's acceptance-checklist walkthrough should cite this note rather than the original programme-plan line text verbatim.

## 5. Open decisions requiring Chris's sign-off before the affected package starts

None remaining as of 2026-09-06 — all five decisions originally listed here have been resolved (§4). WP11.8's pilot-expansion target/timing is deliberately left as a standing future decision (not an oversight) — revisit it when WP11.1–WP11.7 are green and WP11.8 is about to start.

## 6. Regression standard

Every package above must leave the full suite green: `./scripts/test-backend.sh --full`, `npm run test:frontend`, `npm run build` (or `build:isolated`), the frozen compile gate (§1.5), `python3 scripts/secret_scan_added_lines.py --base origin/main` (WP11.2), and `git diff --check`. No package is considered complete on the basis of a partial or "focused" test run alone — full-suite evidence is required before moving to the next package, matching every prior stage's own regression discipline.

## 7. Global prohibited side effects (applies to every package above)

- No commit, push, migration, deployment, service restart, feature-flag activation, publication, or withdrawal without Chris's explicit go-ahead for that specific action, per `CLAUDE.md`.
- No `npm run build:publish` or `npm run publish:static`.
- No mutation against any real/shared database — only the disposable-Postgres environment named in WP11.7, and only after Chris's sign-off on which environment that is (§5).
- No work on the Organization Steward/Platform Governance Hermes binding itself (account provisioning, cron wiring, credential creation) — that is explicitly Chris/Hermes Agent's track, not this plan's.
- Do not assume the repository's feature-flag defaults reflect any live environment's actual state (§1.1) — re-verify out-of-band before any rollout-adjacent claim.
