# Stage 11 day-one rollout plan (drafting only — no execution)

**Status:** Drafted 2026-09-07. **Drafting this document does not execute
any of its steps.** Each step still requires Chris's separate, explicit
authorization at the time it is actually executed, per `CLAUDE.md`.

This is a compressed variant of
`docs/plans/2026-09-06-stage11-wp11.8-rollout-plan.md` ("WP11.8"), written
for the case Chris has confirmed applies now: **there are no active users
in production yet.** WP11.8 stays the reference document for its full
reasoning, citations, and the emergency/rollback procedures — this doc
only records what changes when there is no live tenant to protect during
rollout, and should be read alongside it, not instead of it.

## Release this plan describes

`origin/main` at commit `3c790be` (pushed 2026-09-07). Alembic head:
`20260905_0044` (unchanged since WP11.8 was drafted — no new migrations
have landed).

## What does not change from WP11.8

- **Step 1 (backup/restore verification)** — still required exactly as
  written, even with no live data yet: it proves the restore mechanism
  works *before* it's ever needed, and establishes the pre-migration
  baseline.
- **Step 2 (deploy migrations/backend, org flags off)** — still required
  as-is: land the additive migration and the new backend code with every
  organization flag off first, so the deploy itself is verified
  independent of the flag flip.
- **Step 3 (bootstrap the `symgov` organization)** — still required as-is,
  including the protected-owner precondition
  (`chris.brighouse@hotmail.co.uk` must already exist and be active) and
  dry-run-before-apply.
- **Step 4 (deploy frontend behind flags)** — still required as-is.
- **Step 10 (keep the legacy protected-owner safeguard)** — unaffected;
  not user-count-dependent.
- The rollback procedure (WP11.8 step 8) and the emergency pre-floor
  recovery ordering (WP11.8 step 9) must still **exist and be understood**
  before go-live, even though day one has no tenant data to protect —
  because from the moment step 5 below runs, that stops being true.

## What compresses, because there are no live users to protect yet

- **WP11.8 steps 5–7 collapse into one step.** Their staged structure
  (single pilot org → smoke test → separately-authorized expansion to
  further orgs) exists specifically to limit blast radius against real
  tenants while validating the rollout. With no live users, there is
  nothing yet for a staged pilot to protect, so:
  - Enable every organization flag needed for day-one operation in a
    single pass rather than the minimal pilot subset, for every
    organization Chris wants live at launch (this may be just `symgov`,
    or `symgov` plus others — **Chris decides which organizations belong
    in `SYMGOV_ORGANIZATION_PILOT_CODES` at launch; this plan does not
    invent that list**).
  - Icon upload stays off regardless
    (`SYMGOV_ORGANIZATION_ICON_UPLOAD_ENABLED`) — this restriction is
    about the unclosed malware-scan gap, not about live-user count, so it
    is not affected by this compression.
  - `SYMGOV_PLATFORM_ADMIN_ENABLED` only once the platform-admin agent
    binding is actually ready (unrelated to user count — WP11.8's caveat
    stands).
  - Run the WP11.8 step 6 smoke-test list once, immediately after flags
    are on, before calling launch done. Nothing here removes the smoke
    test — it removes the *staging* of which orgs see it first.
- **Timing pressure is lower, not the correctness bar.** Because there's
  no live traffic to avoid disrupting, steps 1–4 and the collapsed step
  above don't need to happen in a tightly sequenced maintenance window —
  but each step still must complete and be verified before the next
  starts, in the same order as WP11.8.

## What this plan still does not decide

- The exact list of organizations enabled at launch (the collapsed
  step above) — Chris's call at execution time, not invented here.
- Whether/when to retire the legacy protected-owner safeguard (WP11.8
  step 10 — explicitly out of scope for any rollout plan, requires its
  own separately reviewed cutover).
- Anything about *how* deployment is actually executed (which host,
  which CI/CD path, whether via direct commands or a Hermes agent
  instruction set) — that is the next thing to work out, separately from
  this plan's *what* and *in what order*.
