"""pin the search_path of the recursive stage4 CHECK-constraint function

Revision ID: 20260908_0046
Revises: 20260907_0045

`stage4_jsonb_max_depth` (20260822_0030) is a recursive SQL function that
calls itself *unqualified* and declares no `SET search_path`. `pg_dump`
emits `set_config('search_path', '', false)`, so during a restore the
`ck_projects_metadata_bounds` / `ck_symbol_set_items_provenance_bounds`
CHECK constraints cannot resolve the inlined self-call:

    COPY failed for table "projects": ERROR:  function
    stage4_jsonb_max_depth(jsonb) does not exist

The COPY aborts and the table restores **empty**, taking its dependent
foreign keys down with it. Confirmed against the 2026-09-08
pre-migration production dump: `projects` restored 0 of 1 rows while
`users` (11) and `governed_symbols` (95) came back intact, with nothing
but a `warning: errors ignored on restore: 3` line to say so.

Pinning the function's own `search_path` makes the self-call resolve
regardless of the caller's setting, so an ordinary `pg_restore` succeeds
with no operator intervention.

`stage4_string_array_bounds`, defined next to it and used by the same
kind of CHECK constraint, needs no equivalent change: it references only
`pg_catalog` builtins, and `pg_catalog` is always searched. This is the
only self-recursive function among the 30 the migrations define, and the
only one reached from a CHECK constraint.

See the 2026-09-08 execution record in
`docs/plans/2026-09-07-stage11-hermes-deployment-task.md`.
"""
from alembic import op

revision = "20260908_0046"
down_revision = "20260907_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER FUNCTION stage4_jsonb_max_depth(jsonb) "
        "SET search_path = public, pg_catalog"
    )


def downgrade() -> None:
    op.execute("ALTER FUNCTION stage4_jsonb_max_depth(jsonb) RESET search_path")
