# Stage 11 catalog identifier backfill and deployment resume plan

**Status:** Drafted 2026-09-07. **Drafting this document executes none of
it.** Each phase still needs Chris's explicit authorization at the time
it runs, per `CLAUDE.md`.

## Why this plan exists

The 2026-09-07 production deployment attempt completed steps 1-4 of
`docs/plans/2026-09-07-stage11-hermes-deployment-task.md` and stopped at
step 5. `alembic upgrade 20260905_0044` failed at revision
`20260826_0031` with:

    catalog publication invariant preflight failed:
    published symbol lacks matching canonical catalog identifier

The cause is a gap in the release, not in production's data or
environment. `20260802_0026` introduces the catalog identifier registry
empty; `20260826_0031` enforces that every published symbol already has
a canonical identifier; nothing populates the registry in between. Full
detail is in that task doc's "Corrections from the 2026-09-07 execution
attempt" section.

Because `env.py` runs the whole upgrade in one transaction, the failure
rolled everything back. **Production is untouched at `20260801_0026`**
and still serving the previous release healthily.

This is not a production-only problem: no database containing published
symbols can cross `20260826_0031`, so the release is undeployable
anywhere until the backfill exists.

## Objective

Land a tested backfill so `alembic upgrade 20260905_0044` succeeds on a
database that already has published symbols, then resume the gated
deployment at step 5.

## Constraints in force

- Additive migrations only; never downgrade below the visibility floor
  (`20260829_0033`) once organization data exists.
- Never enable `SYMGOV_ORGANIZATION_ICON_UPLOAD_ENABLED` (unresolved
  malware-scan gap).
- Never touch `symgov-minio` bucket contents directly.
- Do not delete or rewrite existing rows. Chris confirmed 2026-09-07
  that current production content is scratch test data and that
  **generating values for new columns is authorized** — that authorizes
  allocating new identifiers, not discarding existing symbols.
- Identifiers must match `format_allocated_catalog_symbol_id()`
  (`catalog_symbol_ids.py:62`) exactly: `S-%06d` from
  `catalog_symbol_id_seq`.

## Phase 1 — the backfill migration

Add one revision between `20260822_0030` and `20260826_0031`, and
re-parent `0031` onto it.

**Why in-chain rather than an operator-run command:** it cannot be
forgotten, `alembic upgrade` stays a single step for every future
environment, and it applies to any environment not yet past `0031`. A
management command would have to be remembered and correctly sequenced
by every operator, forever.

**Why a new revision rather than editing `0031`:** keeps enforcement and
data repair separately reviewable, and guarantees the backfill runs even
in an environment already past `0031`. The lower-churn alternative —
prepending the backfill to `0031.upgrade()` before its preflight —
avoids the graph change and the test edit below, and is safe only
because `0031` has never been applied to production. Recorded here as a
fallback, not the recommendation.

### Files

1. **New:** `backend/alembic/versions/20260823_0030a_catalog_symbol_identifier_backfill.py`
   - `revision = "20260823_0030a"`, `down_revision = "20260822_0030"`
   - Naming decision to confirm at implementation time: the repo's
     counter is global (`0026`…`0031`), so inserting mid-chain needs
     either the `0030a` suffix above or the next free counter
     (`20260907_0045`) placed mid-graph. Prefer `0030a`; switch if any
     test enforces a strict four-digit suffix.
2. **Edit:** `backend/alembic/versions/20260826_0031_catalog_symbol_publication_invariant.py`
   - `down_revision` → `"20260823_0030a"`
3. **Edit:** `tests/test_catalog_symbol_publication_invariant_migration.py:32`
   - the pinned `down_revision` assertion, currently `"20260822_0030"`
4. Re-check each migration test's `CURRENT_GLOBAL_HEAD` stale-head line.
   The head does not move (`20260905_0044` stays sole head), so no
   change is expected — verify rather than assume.

### `upgrade()`

Take `ACCESS EXCLUSIVE` on both tables first, matching the locking
discipline of the surrounding migrations, so no writer can race the
allocation:

```sql
LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE;
LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    target RECORD;
    allocated TEXT;
BEGIN
    FOR target IN
        SELECT id FROM governed_symbols
        WHERE catalog_symbol_id IS NULL
        ORDER BY created_at, slug
    LOOP
        allocated := 'S-' || lpad(nextval('catalog_symbol_id_seq')::text, 6, '0');
        INSERT INTO catalog_symbol_identifiers
            (identifier, role, governed_symbol_id, allocation_source, allocated_at)
        VALUES
            (allocated, 'canonical', target.id, 'legacy_backfill', now());
        UPDATE governed_symbols
           SET catalog_symbol_id = allocated
         WHERE id = target.id;
    END LOOP;
END;
$$;
```

Notes on correctness:

- Backfills **every** symbol lacking an identifier, not only published
  ones. The invariant only demands published symbols, but allocating for
  all of them means publishing a draft later cannot trip the trigger.
- `ORDER BY created_at, slug` is deterministic — `created_at` is
  `NOT NULL` and `slug` is unique.
- `S-000001` satisfies the grammar check
  (`identifier = upper(identifier)` and the `^[A-Z0-9]…[A-Z0-9]$`
  pattern) and `allocation_source = 'legacy_backfill'` satisfies the
  allocation-source check, both from `20260802_0026`.
- The `0026` consistency triggers are `DEFERRABLE INITIALLY DEFERRED`,
  so they evaluate at commit against the final, consistent state.
- Close with an assertion that no symbol remains without a canonical
  identifier, so the migration fails loudly rather than deferring the
  failure to `0031`'s preflight.

### `downgrade()`

Must restore the emptiness precondition that `20260802_0026`'s own
downgrade guards require. Order matters — null the links before deleting
registry rows, or the `ondelete="RESTRICT"` foreign key blocks it:

```sql
LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE;
LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE;

UPDATE governed_symbols
   SET catalog_symbol_id = NULL
 WHERE catalog_symbol_id IN (
     SELECT identifier FROM catalog_symbol_identifiers
      WHERE allocation_source = 'legacy_backfill' AND role = 'canonical');

DELETE FROM catalog_symbol_identifiers
 WHERE allocation_source = 'legacy_backfill';
```

Only `legacy_backfill` rows are removed, so identifiers allocated later
by normal operation are never touched.

## Phase 2 — the regression test

The gap survived 2356 passing tests because no test seeds published
symbols *before* the registry is introduced. Add a PostgreSQL test in
the WP11.7 rehearsal family
(`tests/test_stage11_wp11_7_migration_rehearsal_postgresql.py` pattern,
reusing its `_database()` / `_alembic()` helpers) that:

1. upgrades a disposable database to `20260801_0026`;
2. seeds a published symbol at that revision — `governed_symbols` +
   `symbol_revisions` with `lifecycle_state='published'`, plus
   `published_pages` and `pack_entries` rows, mirroring production's
   shape;
3. upgrades to `20260905_0044` and asserts it succeeds;
4. asserts every published symbol has a canonical identifier with
   `allocation_source='legacy_backfill'`, and that no symbol row was
   deleted.

Without step 2 this test proves nothing — that omission is the whole
reason the defect reached production.

## Phase 3 — rehearse against real production data

Before touching production again, prove the fixed chain against an
actual copy of it. This is the check step 2 of the deployment skipped:
it verified the dump restored and row counts matched, but never ran the
migration against the restored copy.

1. Restore `/data/symgov-backups/symgov-pre-stage11-migration-20260907T150848Z.dump`
   into a disposable `postgres:16-alpine` container (never into
   `symgov-postgres`).
2. Run the full upgrade to `20260905_0044` against it, with
   `SYMGOV_ALEMBIC_USE_MIGRATION_DB=1`.
3. Assert: final revision exactly `20260905_0044`; 95 canonical
   identifiers allocated; `governed_symbols`, `published_pages` and
   `pack_entries` counts unchanged at 95 / 84 / 84; the `0031` invariant
   query returns no rows.
4. Remove the disposable container.

Phase 4 does not start until this passes.

## Phase 4 — resume the gated deployment

Resume `docs/plans/2026-09-07-stage11-hermes-deployment-task.md` at step
5, applying every correction recorded in that doc. One numbered step per
authorization, as before.

Already done and **not** to be repeated:

- Step 1 — `$DEPLOY_SHA` (note: a new SHA once Phase 1 lands)
- Step 2 — backups, including the fresh 15:08 pre-migration dump
- Step 3 — release worktree and frontend build
- Step 4 — compose backup and the three `symgov-api` edits, plus
  `/docker/symgov-hermes/.env`

Note that Phase 1 produces a **new commit**, so the release directory
and compose file must be re-pointed at a new `$DEPLOY_SHA` before step
5 — steps 3 and 4 get redone for that SHA, and `stage11-47f25bc`
becomes obsolete.

Remaining, each separately authorized: step 5 (migration, with the
migration-role flag), 6 (backend deploy, `docker exec` health check),
7 (frontend bind-mount), 8 (organization bootstrap), 9 (flags including
`SYMGOV_PLATFORM_ADMIN_ENABLED`, icon upload still off), 10 (smoke
tests), 11 (stand down, old release retained).

## Interim safety

The compose file currently points `symgov-api` at `stage11-47f25bc`
while the database is on the old schema. Nothing has applied it, and a
host reboot will not (Docker restarts the existing container rather than
re-reading compose), but a stray `docker compose up` would start the new
backend against a schema it does not expect. While the deploy is paused,
either restore the compose file from
`docker-compose.yml.pre-stage11-20260907T124453Z` or leave the stack
alone entirely.

## Rollback

Unchanged from WP11.8 step 8: point the compose file back at the old
release directory and image tag and restart. Never downgrade the schema
below the visibility floor once organization data exists. The current
rollback target remains `llm-consumption-bb40f4c`, whose release
directory and compose backup both stay in place.

## Execution record — 2026-09-07

Phases 1-3 are complete and verified. Chris chose the "proper route"
(fix, test, rehearse, then deploy) and confirmed that current production
content is scratch test data, authorizing generated values for new
columns. Nothing has been committed and production has not been touched.

### Phase 1 — done

- **New:** `backend/alembic/versions/20260823_0030a_catalog_symbol_identifier_backfill.py`
- **Edited:** `20260826_0031_...py` — `down_revision` → `"20260823_0030a"`
- **Edited:** `tests/test_catalog_symbol_publication_invariant_migration.py:32`
  — pinned parent assertion
- `CURRENT_GLOBAL_HEAD` in `tests/test_public_projection_migration.py`
  needed no change: `20260905_0044` remains the sole head.
- Graph verified linear:
  `0029 → 0030 → 0030a → 0031 → 0032 → … → 20260905_0044`.

**Defect found and fixed during Phase 1 — worth knowing for any future
data migration in this repo.** The first version of the backfill passed
stepwise but failed a single-shot upgrade with:

    cannot ALTER TABLE "governed_symbols" because it has pending trigger events
    [SQL: ALTER TABLE governed_symbols ADD COLUMN owner_organization_id UUID]

The backfill's `UPDATE governed_symbols` leaves the `20260802_0026`
consistency triggers pending, because they are `DEFERRABLE INITIALLY
DEFERRED` and the whole chain runs in one transaction. Three revisions
later, `20260829_0033` alters that table, and PostgreSQL refuses to
alter a table with pending trigger events. Resolved by firing them
explicitly at the end of both `upgrade()` and `downgrade()`:

    SET CONSTRAINTS
      trg_governed_symbols_validate_catalog_symbol_consistency,
      trg_catalog_symbol_identifiers_validate_consistency IMMEDIATE

Named rather than `ALL`, matching the existing convention at
`organization_symbol_review.py:262`, so unrelated deferred constraints
in the same transaction are undisturbed. **Any future migration that
writes to `governed_symbols` or `catalog_symbol_identifiers` mid-chain
needs the same treatment.**

### Phase 2 — done

**New:** `tests/test_catalog_symbol_identifier_backfill_postgresql.py`
— three tests, all passing: full-chain upgrade with published data
seeded at `20260801_0026`, a direct re-run of `0031`'s own preflight
predicate, and idempotency of the allocation block.

The test was proven to actually guard the defect: run against the
pre-fix chain in the `stage11-47f25bc` worktree, the same scenario
reproduces the original production failure exactly
(`catalog publication invariant preflight failed: published symbol
lacks matching canonical catalog identifier`). It passes only against
the fixed chain.

### Phase 3 — done, passed

Restored `symgov-pre-stage11-migration-20260907T150848Z.dump` into a
disposable `postgres:16-alpine` and ran the fixed chain as a single
`alembic upgrade`, exactly as production will:

| Assertion | Result |
| --- | --- |
| Restored baseline revision | `20260801_0026` |
| Revisions applied | 20, single command |
| Final revision | `20260905_0044` |
| `governed_symbols` / `published_pages` / `pack_entries` | 95 / 84 / 84, unchanged |
| Users preserved | 11 |
| Canonical identifiers allocated (`legacy_backfill`) | 95 |
| Symbols still lacking an identifier | 0 |
| Malformed identifiers (not `S-NNNNNN`) | 0 |
| `0031` invariant violations | 0 |

Allocated identifiers are human-readable and sequential, e.g.
`3-way-valve → S-000001`. The disposable container was removed
afterwards; `symgov-postgres` was never touched.

**Rehearsal prerequisite discovered:** a disposable database must have
the roles `symgov`, `symgov_app` and `symgov_migrator` created before
migrating, because `20260810_0028` `GRANT`s to `symgov_app` and a fresh
container has no such role. Production has all three — and the presence
of `symgov_migrator` independently confirms the migration-role design
behind `SYMGOV_ALEMBIC_USE_MIGRATION_DB=1`.

### Unrelated pre-existing failure, not introduced here

`tests/test_project_symbol_set_postgresql.py::test_public_eligibility_rejects_revision_owned_by_a_different_symbol`
fails with `relation "active_public_symbol_projections" does not exist`.
It fails **identically on untouched `47f25bc`** in the
`stage11-47f25bc` worktree, so it is not a consequence of this work.
The view is created by `20260902_0035`, while that module's fixture
leaves its database at a Stage 4 revision. Left alone; needs its own
fix.

## Open decisions for Chris

1. ~~New revision vs. prepending to `0031`~~ — resolved: new revision.
2. ~~All 95 symbols vs. only the 84 published~~ — resolved: all 95.
3. **Still open:** whether this lands on `main` directly or on a branch
   for review. Phase 4 cannot start until it is committed, because the
   release directory is a `git worktree` checkout of a SHA — the commit
   defines the new `$DEPLOY_SHA`.
