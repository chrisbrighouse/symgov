# Symbol Set Management — Stage 7 kickoff prompt

Copy the "Prompt" section below verbatim into a new Claude Code session
started in `/docker/openclaw-hz0t/data/symgov`.

## Frozen baseline (captured 2026-09-02, end of the Stage 6 session)

- Branch: `main`
- Local `HEAD`: `764a7fbd1bd58b36755fea9fffe0ace966124d10` (`fix: gate Symbol Set Builder search's organization half on the feature flag (Stage 6 WP6.6)`)
- `origin/main`: `87c2b24cb36599b5cc20f5c163c523bc135923af` — **local `main` is 8 commits ahead of `origin/main` and has not been pushed.** Confirm with Chris before pushing.
- Sole Alembic head: `20260901_0034` (`governed_symbol_catalog_visibility_barrier`) — Stage 6 added no migrations.
- Tracked tree was clean except the pre-existing, unrelated `.claude/settings.local.json` diff — leave it untouched.
- Stage 6 (WP6.1–WP6.6) is complete and committed. `docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md` records what was built, the decisions Chris made along the way, and one audit-confirmed gap (fixed in WP6.6) — read it for precedent on plan structure, decision-logging, and audit rigor before writing the Stage 7 plan.
- `organizations_enabled`/`organization_symbols_enabled` were, as of the Stage 6 session, still not activated in any real/shared environment. **Re-verify this with Chris before Stage 7 work touches anything live** — it can change without this session being told.

Re-run `git status`, `git log --oneline -5`, and confirm the Alembic head before doing anything else; if any of the above has drifted, reconcile against the live repository rather than trusting this file.

## What Stage 7 is

Programme plan §13 ("Stage 7 — public contribution, promotion, withdrawal, and demotion"): `docs/2026-08-10-symbol-set-management-implementation-plan.md:817-873`. This is the controlling scope statement. Read it in full before doing anything else — it covers:

- Public-projection migration (`SymbolRevision.lifecycle_state` gains `withdrawn`; `published_pages`/`pack_entries` gain `publication_state`; `active_public_symbol_projections` is replaced with a stricter view).
- An explicit promotion-request state machine (organization-approved revision → public review → publication), reusing the existing public review/publication pipeline rather than creating a shortcut.
- Demotion as a locked, eligibility-gated, auditable transition — blocked while any other organization's Symbol Set still references the symbol, human-executed by a Platform Admin with step-up, never automatic, never reversing itself on a post-commit cache-purge failure.
- A large acceptance/regression matrix (concurrent set-item-add-vs-demotion races, multi-symbol pack partial retirement, re-promotion only reactivating the newly approved revision, complete exclusion from every reader — routes, aliases, assets, Favorites, and the Hannah/Whitney background readers — after demotion, rollback-safety fixtures).

This is materially larger and higher-stakes than any Stage 6 work package: it is the first stage that makes a *public* symbol's visibility reversible, and it touches the publication pipeline every other stage has so far only read from.

## Required reading before writing anything

1. `docs/2026-08-10-symbol-set-management-implementation-plan.md:817-873` (§13, Stage 7 scope) plus whatever earlier sections it cross-references for the publication pipeline, `active_public_symbol_projections`, and the Stage 5 visibility floor.
2. `docs/Symbol Set Management Spec v0.3.md` — Stage 7-relevant sections (promotion/demotion, public contribution).
3. `docs/plans/2026-08-08-symbol-set-management-decision-addendum.md` — check for any Stage 7-relevant accepted decisions.
4. `docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md` — for plan structure/format and to confirm exactly what state Stage 6 left the codebase in (effective palette, organization-wide toggle, Symbol Set Builder) that Stage 7's demotion eligibility check must reason about.
5. `CLAUDE.md` (repo root) — governs every session in this repo. In particular: never run `build:publish`/`publish:static`/deployment/service-restart/migration commands without Chris's explicit approval; never push without approval; report only tests actually run.

## First deliverable: a Stage 7 implementation plan, not code

Follow the precedent `docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md` set: before writing any product code, produce a new `docs/plans/<today's date>-symbol-set-management-stage7-implementation-plan.md` that:

- Captures a fresh repository baseline (branch/HEAD/Alembic head/tree state) at the time you actually start.
- Inventories what already exists that Stage 7 must reuse rather than restate (the existing public review/publication pipeline, `active_public_symbol_projections`, the Stage 5 visibility floor and Stage 6 governed-symbol read paths) — grep first, don't assume.
- Breaks §13's scope into a work-package sequence with explicit dependencies, mirroring the Stage 6 plan's `## 2. Work-package sequence` structure.
- Flags every open product decision that needs Chris's sign-off *before* implementation starts on the package it affects — Stage 7 has materially more of these than Stage 6 did (e.g.: exact promotion-request states and who can submit/triage/decide each transition; whether demotion authority is Platform-Admin-only as §13 states or needs an organization-side counterpart; the precise step-up requirement for demotion; how the impact-preview surface is exposed to a human before they approve a demotion; whether/how this reaches a frontend surface in Stage 7 or is backend-only pending a later stage). Do not silently resolve these yourself — list them and ask.
- States the disposable-Postgres regression standard this stage requires (demotion/promotion races, multi-symbol pack partial retirement, and reader-exclusion-after-demotion are exactly the kind of cross-table/lock/trigger behavior Stage 5/6 established cannot be trusted to SQLite).

Once the plan is drafted, present the open decisions to Chris before starting WP1-equivalent implementation. Do not commit, push, or touch any migration, deployment, service-restart, or feature-activation command without his explicit go-ahead for that specific action, per `CLAUDE.md` and the plan's own authority note (copy the Stage 6 plan's §5-equivalent "prohibited side effects" section forward).

## Prompt

> Continue Symbol Set Management work in `/docker/openclaw-hz0t/data/symgov`. Stage 6 (WP6.1–WP6.6) is complete and committed at local `HEAD` `764a7fbd1bd58b36755fea9fffe0ace966124d10`, not yet pushed to `origin/main`. Read `CLAUDE.md`, `docs/2026-08-10-symbol-set-management-implementation-plan.md` §13 (Stage 7 scope, lines 817-873), `docs/Symbol Set Management Spec v0.3.md`'s promotion/demotion sections, the decision addendum, and `docs/plans/2026-09-01-symbol-set-management-stage6-implementation-plan.md` (for plan format and to know what Stage 6 actually left in place). Re-verify the current git/Alembic baseline and whether `organizations_enabled`/`organization_symbols_enabled` are live anywhere before assuming anything from this file is still accurate. Then produce a repository-grounded Stage 7 implementation plan document (following the Stage 6 plan's structure) that breaks the programme plan's Stage 7 scope into a work-package sequence, inventories what existing code Stage 7 must reuse, and explicitly lists every open product decision that needs my sign-off before implementation starts — do not resolve those decisions yourself. Do not write product code, commit, push, migrate, deploy, or activate anything until I've reviewed the plan and answered the open questions.
