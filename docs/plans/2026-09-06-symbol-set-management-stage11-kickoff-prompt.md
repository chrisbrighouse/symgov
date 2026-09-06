# Symbol Set Management — Stage 11 kickoff prompt

Copy the "Prompt" section below verbatim into a new Claude Code session
started in `/docker/openclaw-hz0t/data/symgov`.

This file reconstructs the original `stage11-kickoff-prompt.md`, which was
lost in a restart before this session began. Nothing about Stage 11's scope
was actually lost: it is programme plan §17, quoted below, and was never
rewritten or reinterpreted to produce this file.

## Frozen baseline (captured 2026-09-06)

- Branch: `main`
- Local `HEAD`: `d5fdcd2` ("docs: record Stage 10 commit hash in plan doc")
- `origin/main`: identical — fully pushed (`git rev-list --left-right --count origin/main...main` → `0 0`)
- Sole Alembic head: `20260905_0044`
- Tracked tree was clean except the pre-existing, unrelated `.claude/settings.local.json` diff — leave it untouched, same as every prior stage's baseline note.
- Stages 1–10 are all committed and closed out with recorded commit hashes (Stage 9 closed at `c425845` with a WP9.9 whole-stage audit; Stage 10 closed at `575e5c6`/`d5fdcd2` with WP10.1–10.8 complete). See `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md` for the most recent closing-audit format precedent.
- **The `symgov-stage6-fixes` worktree has been triaged (2026-09-06, read-only, nothing yet ported/touched in the worktree itself).** Separate git worktree at `/docker/openclaw-hz0t/data/symgov-stage6-fixes`, branch `fix/stage6-review-remediation-20260902` (tip `bcf581c`, confirmed an ancestor of `main` — zero unique commits), has a dirty working tree written 2026-09-02/03 (after Stage 6 landed, at/after Stage 7's own `bcf581c`). Verdict: **not abandoned exploration — substantially working, mostly-additive unshipped code.** Four things it contains, and what Chris decided to do with each:
  - Category/discipline/format search filters on `GET /builder-search` (`main` only supports `q`) — safe/orthogonal, **port it**.
  - Human-readable `displayId` surfacing (new `symbol_identity.py`) on symbol/set-item responses — safe/orthogonal, directly relevant to CLAUDE.md's "keep human-readable symbol IDs prominent" rule (`main` today only exposes UUIDs + name/category/discipline/slug, no short display ID) — **port it**.
  - An ETag/428 optimistic-concurrency guard on `symbol_set_service.py::replace_items` — a real lost-update race `main` doesn't guard against today; safe/orthogonal — **port it**. (Two of the worktree's own tenant-isolation tests fail only because its test helpers don't send the new ETag header — fix that wiring when porting, it isn't evidence the guard is broken.)
  - `symbol_eligibility.py`'s `current_symbol_revisions`/`current_organization_private_revisions` let an approved same-organization **private** symbol become a direct `SymbolSetItem`, not just reachable via the organization-wide auto-include path. This directly conflicts with a decision Stage 6 shipped and tested (`docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md:28`; DB-constraint-enforced per `effective_palette.py:23-32`) — but matches the *original* programme-plan wording ("eligible active-set items **(public/private)** + organization-wide", `docs/2026-08-10-...-implementation-plan.md:1072`). **Chris decided (2026-09-06): loosen the restriction — port this capability as a real Stage 11 work package.** Cross-organization private-symbol access must remain blocked; the existing tenant-isolation enforcement/tests must be re-verified (not weakened) to prove the loosening only widened the same-organization case. The acceptance-checklist line at programme-plan line 1048 ("Effective palette is active-set items plus approved organization-wide symbols... Public Catalog stays independently browseable") needs re-confirming/updating against this widened scope in the Stage 11 plan doc.
  - `symbol_eligibility.py`'s other function, `eligible_organization_private_symbols`, is a straight refactor of logic `main` already has inline in `symbol_set_builder.py::_search_organization_symbols` — no behavior change, port only if convenient while doing the above.
  - The frontend changes (`api.js`, `EffectivePalettePanel.js`, `SymbolSetBuilderPanel.js`, tests) were not deeply reviewed during triage — inferred to be UI wiring for the above (displayId, filters, ETag handling); verify before assuming they're complete.

Re-run `git status`, `git log --oneline -5`, `git worktree list`, and confirm the Alembic head before doing anything else; if any of the above has drifted, reconcile against the live repository rather than trusting this file.

## What Stage 11 is

Programme plan §17 ("Stage 11 — integrated hardening, migration rehearsal, and controlled release"): `docs/2026-08-10-symbol-set-management-implementation-plan.md:972-1095`. This is the controlling scope statement — read it in full before doing anything else. Outcome (line 974): *"production-ready source and an explicit, reversible rollout package. This stage does not itself authorize deployment."*

It covers, in order:

1. **Integrated verification** — a two-organization adversarial fixture across every principal type; route-policy/tenant-isolation matrices; full end-to-end journey exercises across every Stage 1–10 feature; the complete regression/build/secret-scan/compile gate suite (the plan specifies the exact frozen compile-gate invocation — do not substitute plain `python -m compileall`); representative performance evidence; accessibility/responsive/locale checks; and a **fresh immutable Stage 1 spec review + Stage 2 security review of the exact release candidate**.
2. **Disposable-PostgreSQL migration rehearsal** — restore/migrate from the pre-organization revision through every new head; bootstrap dry-run then apply; schema-first pre-floor rollback rehearsal; flags-off rollback-to-visibility-floor rehearsal; emergency pre-floor recovery ordering (deny external routes, drain readers, no resume until at/above floor); downgrade-then-upgrade proof; single-Alembic-head proof.
3. **A rollout plan requiring separate authorization** (10 explicit steps, lines 1020–1033) — back up → deploy migrations/backend with flags off → reviewed bootstrap → deploy frontend hidden behind flags → enable the pilot allowlist for the `symgov` organization only → smoke test → expand only after recorded go/no-go → rollback procedure → emergency pre-floor recovery ordering → keep the legacy protected-owner safeguard until a separately reviewed cutover.
4. **A 20-item final acceptance checklist** (lines 1035–1060) and requirement/acceptance-criterion traceability tables (§18, lines 1062–1096) tying every prior stage's spec area back to this stage's gate.

This is not a new-feature stage like 4–10: it is a hardening/rehearsal/release-readiness gate over everything already built, and it explicitly does not authorize deployment on its own — a separate, later go-ahead is required for the rollout plan's actual mutating steps.

## Required reading before writing anything

1. `docs/2026-08-10-symbol-set-management-implementation-plan.md:972-1096` (§17–18, Stage 11 scope and traceability) — the controlling scope statement.
2. `docs/Symbol Set Management Spec v0.3.md` — full re-read; Stage 11 exercises every spec area, not one slice of it.
3. `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` — every accepted decision Stage 11 must hold as invariant.
4. `docs/plans/2026-08-08-symbol-set-management-luna-resume.md:94` — states Stage 11 must additionally load the Hermes skill `symgov-release-operations` (earlier stages' pure feature-implementation work did not need it); the plan still does not authorize push, migration, deployment, service restart, publication, or withdrawal without Chris's separate go-ahead for that specific action.
5. `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md` — most recent stage, for plan/closing-audit format precedent, and because Stage 10's own deferred items (below) are things Stage 11's acceptance checklist item *"Organization Steward/Platform Governance are scoped recommenders with structured findings and no direct authority"* must account for.
6. `docs/plans/2026-07-28-f0-4-production-preflight-blocked.md` — a prior, unrelated read-only production preflight that was blocked on a benign command-approval guard denial (not a defect); superseded context, but worth knowing before assuming this is the first production-readiness attempt.
7. `CLAUDE.md` (repo root) — governs every session in this repo. In particular: never run `build:publish`/`publish:static`/deployment/service-restart/migration commands without Chris's explicit approval; never push without approval; report only tests actually run.

## Known deferred items from stages 1–10 that Stage 11's integrated verification must account for (not silently re-open, not silently resolve)

- Icon-upload malware scan (fail-closed) not integrated — icon upload must stay disabled in production until resolved (Stage 3).
- Icon abandoned-object reconciler not implemented (Stage 3).
- Reject/changes-requested decisioning for promotion requests — accept-only shipped, Chris explicitly deferred (Stage 7).
- Cross-organization promotion-request-listing endpoint missing — Chris accepted the gap (Stage 7).
- `GET /published/packs` multi-symbol-pack partial-retirement accounting gap, deliberately deferred (Stage 7, `routes/published.py`).
- Contribution points/scoring column does not exist yet, pending a versioned policy (Stage 9).
- Several contribution badges and an opt-in leaderboard deferred (Stage 9).
- Usage-rollup refresh/purge batch jobs built but not wired to any scheduler — a separate ops/deployment decision (Stage 9).
- Three `AgentFinding.finding_type` slugs (`cross_tenant_authorization_failure`, `unresolved_governance_exception`, `icon_generation_missing`) omitted from the v1 `CheckConstraint` (Stage 10).
- No retention/purge policy for `agent_findings` (Stage 10).
- **No live Hermes agent identity/binding exists for Organization Steward/Platform Governance** — this is tracked and specified separately (see the Org Steward/Platform Governance binding spec Chris is handing to Hermes Agent directly, outside this repo's commits); Stage 11 should verify the repo-side behavior (deterministic-only, no direct authority, attributable/scoped findings) rather than attempt the binding itself.

None of these block Stage 11 from starting; they are exactly the kind of already-accepted, already-documented gap the acceptance checklist expects Stage 11 to verify is still true and still intentional, not to silently fix or silently re-litigate.

## First deliverable: a Stage 11 implementation plan — not verification/rehearsal code yet

The worktree triage above is done. Before any §17 integrated-verification/rehearsal work starts, follow the precedent every prior stage's kickoff set (most recently `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md`, and structurally `docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md`): produce a new `docs/plans/<today's date>-symbol-set-management-stage11-implementation-plan.md` that:

- Captures a fresh repository baseline (branch/HEAD/Alembic head/tree state) at the time you actually start; re-confirm the worktree triage findings above still hold (the worktree is uncommitted and could have changed).
- Includes, as an explicit early work package, integrating the four `symgov-stage6-fixes` items above using the Hermes skill `symgov-uncommitted-worktree-integration` (`/root/.hermes/profiles/symgov/skills/symgov/symgov-uncommitted-worktree-integration/SKILL.md`) — a clean integration branch from exact `origin/main`, porting only the unique bytes (not a wholesale copy of the stale worktree files), fixing the ETag-header test-wiring gap, adding/updating tenant-isolation tests that prove the loosened private-set-item rule still blocks cross-organization access, and updating the effective-palette acceptance-checklist wording (programme-plan line 1048) to match.
- Breaks the rest of §17's scope into a work-package sequence with explicit dependencies (worktree integration → integrated verification → migration rehearsal → rollout-plan drafting), mirroring prior stages' `## 2. Work-package sequence` structure.
- Flags every open decision needing Chris's sign-off before that package proceeds — in particular the exact rollout cadence/timing, which organization(s) beyond `symgov` get pilot access and when, and how the Organization Steward/Platform Governance Hermes binding (handled outside this plan, see below) gates or doesn't gate the rollout's own acceptance checklist item.
- States the disposable-Postgres rehearsal standard exactly as §17 specifies it (schema-first pre-floor rollback, flags-off rollback-to-floor, emergency pre-floor recovery ordering) — this cannot be simulated on SQLite.

Once the plan is drafted, present the open decisions to Chris before starting any implementation. Do not commit, push, or touch any migration, deployment, service-restart, or feature-activation command without his explicit go-ahead for that specific action — and remember §17's own text: this stage does not itself authorize deployment even once its own checklist is green.

## Organization Steward / Platform Governance Hermes binding (handled outside this repo's commits)

Chris is handing the following spec directly to Hermes Agent for creation — it is not a Stage 11 repo work package, but Stage 11's acceptance checklist ("Organization Steward/Platform Governance are scoped recommenders with structured findings and no direct authority") should verify the repo-side behavior holds regardless:

- Reuse the existing **Ed** agent identity (per spec FR-AGT-010's own suggestion) rather than minting a new persona for "Organization Steward"/"Platform Governance" — those stay logical capability names in `AgentConfiguration`, not separate Hermes identities.
- Ed's Symgov account needs the **Platform Admin** role to call `POST /platform/agents/platform-governance/run`, `POST /platform/organizations/{organization_id}/agents/organization-steward/run` (once per organization), the platform findings-list route, and `POST /platform/agent-findings/{finding_id}/escalate`. **Decided:** accept full Platform Admin for now (no live users) — revisit/narrow before wider rollout or production activation with real users, since there's no narrower "agent-runner" role today.
- **Decided:** escalated findings' `assignee_user_id` is Chris's own Symgov Platform Admin account. `escalate_finding` only writes `assignee_user_id`/`issue_reference` on the finding row — it does not notify anyone itself. Since Alfi is the repo's sole Telegram-facing orchestrator, actual human notification is a second step: Ed escalates in Symgov, then Alfi posts the Telegram alert. Ed must not message Telegram directly.
- **Decided:** daily Hermes-side cron trigger calling both run endpoints (the app deliberately has no built-in scheduler — Stage 10 §4 Q6).
- Provisioning Ed's account/role and wiring the Hermes-side cron/credential is infrastructure Chris/Hermes Agent does directly — same boundary Stage 10 itself drew (repo stops at readiness, binding is external).

## Prompt

> Continue Symbol Set Management work in `/docker/openclaw-hz0t/data/symgov`. Stages 1–10 are complete and committed through local `HEAD` `d5fdcd2`, fully pushed to `origin/main`. Read `CLAUDE.md`, `docs/2026-08-10-symbol-set-management-implementation-plan.md:972-1096` (§17–18, Stage 11 scope and traceability — the controlling scope statement), `docs/Symbol Set Management Spec v0.3.md`, the decision addendum, `docs/plans/2026-08-08-symbol-set-management-luna-resume.md` (note it says Stage 11 must additionally load the Hermes skill `symgov-release-operations`), and `docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md` (for plan/audit format precedent and Stage 10's own deferred items). Re-verify the current git/Alembic baseline before assuming anything from this file is still accurate. The `symgov-stage6-fixes` worktree (`/docker/openclaw-hz0t/data/symgov-stage6-fixes`, branch `fix/stage6-review-remediation-20260902`) has already been triaged — read this file's own account of what it contains and what I decided to do with each piece (port the search filters, the displayId surfacing, and the ETag guard; port the private-set-item loosening too, with re-verified cross-organization isolation tests) before starting. Then produce a repository-grounded Stage 11 implementation plan document (following prior stages' structure) that makes worktree integration its first work package, then breaks the rest of the programme plan's §17 scope into a work-package sequence (integrated verification, then disposable-Postgres migration rehearsal, then rollout-plan drafting), and explicitly lists every open decision that still needs my sign-off before implementation starts — do not resolve those decisions yourself, and do not conflate this plan's scope with the separate Organization Steward/Platform Governance Hermes agent binding (already specified in this file, and being handled directly with Hermes Agent outside this repository's commits). Do not write implementation/verification code, commit, push, migrate, deploy, or activate anything until I've reviewed the plan and answered the remaining open questions. §17 itself does not authorize deployment even once its checklist is green — that requires my separate, explicit authorization for the rollout plan's own steps.
