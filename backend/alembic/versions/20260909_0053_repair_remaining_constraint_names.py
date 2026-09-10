"""repair the remaining check constraint names that disagree with the ORM

Revision ID: 20260909_0053
Revises: 20260909_0052
Create Date: 2026-09-09 00:00:00.000000

Completes the repair 20260909_0050 started on the semantic model, and empties
`PRE_EXISTING_NAME_DRIFT` in
`tests/test_semantic_constraint_name_repair_postgresql.py`.

That allowlist held ten tables under one label, but an audit showed three
unrelated causes behind them. Only the first two are fixed by DDL:

**Cosmetic name drift -- six tables, thirteen constraints.** The expression in
PostgreSQL is identical to the ORM's; only the name differs, because the
original migration passed an already-prefixed name to `sa.CheckConstraint`
inside `op.create_table` and `NAMING_CONVENTION` prefixed it a second time --
then SQLAlchemy silently truncated the result with a 4-character hash wherever
it passed 63 characters. Renamed here to the names the ORM declares. Nothing
in the application dispatches on any of them, so this is latent rather than
broken; the point is that the next module to catch an `IntegrityError` by
check-constraint name would silently never match, exactly as 20260909_0050's
docstring warns.

**Undeclared constraints -- `provenance_assessments`, two constraints.** These
are not a naming defect at all. PostgreSQL has enforced `processing_outcome`
and `rights_disposition` enumerations on that table since it was created, and
`schema.py` never declared them. The database was strictly stronger than the
model, which means anything built from `Base.metadata` -- notably
`Base.metadata.create_all()` in `tests/test_f0_4_review_without_unpublication.py`
-- got a *laxer* schema than production, so a test could pass on data
production rejects. Now declared in the model, and renamed here to match.

**Intentional expression divergence -- `projects`, `symbol_sets`,
`symbol_set_items`.** No DDL, and none is wanted. On these three the model and
the database deliberately enforce *different rules*: the database uses the
`stage4_jsonb_max_depth` and `stage4_string_array_bounds` PL/pgSQL functions
from 20260822_0030, and the ORM carries a weaker builtin-only approximation
because a `CheckConstraint` referencing a custom function would make
`create_all()` fail against a database that has never been migrated.
`test_orm_metadata_keeps_create_all_safe_provenance_boundary` already asserts
that, so the divergence is load-bearing and must not be "fixed". What was
wrong was only the *name*: the model had drifted to `metadata_size`,
`provenance_size`, `disciplines_array_bounds` and `use_cases_array_bounds`
against the deployed `..._bounds` names. Corrected in `schema.py`, so those
four now agree by name while still differing by expression -- which is why the
allowlist is replaced by an explicit divergence register rather than deleted.
Those three tables stay double-prefixed throughout, consistently in both model
and database, and are left that way: sixty-seven constraints across fourteen
tables share that cosmetic defect, and renaming deployed constraints for
appearance alone is a separate decision.

Constraints are located by their *definition* rather than their current name,
because seven of the fifteen carry a truncation hash that exists only as an
artefact of the over-long name. `INTO STRICT` aborts the migration rather than
renaming the wrong constraint if a marker matches none or more than one.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260909_0053"
down_revision: Union[str, None] = "20260909_0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, a substring unique to that constraint's definition, the ORM's name,
# the deployed name to restore on downgrade).
#
# The fourth element is what makes this migration reversible, and it is not
# optional. Alembic applies `NAMING_CONVENTION` to `op.drop_constraint` as well
# as to `op.create_table`, so an older migration that drops one of these by its
# logical name asks PostgreSQL for the *doubled* name. 20260829_0033's
# downgrade drops `ck_governed_symbols_organization_wide_scope` and
# `ck_governed_symbols_visibility`; 20260808_0027's drops
# `ck_user_sessions_purpose`. Renaming without restoring breaks those rollback
# paths, which is why 20260909_0050's no-op downgrade is not a precedent here:
# its constraints were created by the migration immediately before it, and no
# older migration references them.
#
# Ten of the fifteen restore names carry a 4-character SQLAlchemy truncation
# hash. They are reproduced verbatim from the deployed schema rather than
# recomputed, and the round-trip test in
# tests/test_semantic_constraint_name_repair_postgresql.py proves every one by
# cycling a real database through upgrade, downgrade and upgrade.
_REPAIRS: tuple[tuple[str, str, str, str], ...] = (
    (
        "catalog_symbol_identifiers",
        'upper(identifier)',
        "ck_catalog_symbol_identifiers_grammar",
        "ck_catalog_symbol_identifiers_ck_catalog_symbol_identif_0a97",
    ),
    (
        "catalog_symbol_identifiers",
        'governed_symbol_id IS NULL',
        "ck_catalog_symbol_identifiers_role_target",
        "ck_catalog_symbol_identifiers_ck_catalog_symbol_identif_7fd3",
    ),
    (
        "catalog_symbol_identifiers",
        'allocation_source',
        "ck_catalog_symbol_identifiers_allocation_source",
        "ck_catalog_symbol_identifiers_ck_catalog_symbol_identif_dd20",
    ),
    (
        "catalog_symbol_identifiers",
        "'historical_alias'::text, 'tombstone'",
        "ck_catalog_symbol_identifiers_role",
        "ck_catalog_symbol_identifiers_ck_catalog_symbol_identif_f6ef",
    ),
    (
        "external_identities",
        'identity_type',
        "ck_external_identities_identity_type",
        "ck_external_identities_ck_external_identities_identity_type",
    ),
    (
        "external_identities",
        "'inactive'",
        "ck_external_identities_status",
        "ck_external_identities_ck_external_identities_status",
    ),
    (
        "governed_symbols",
        'NOT organization_wide',
        "ck_governed_symbols_organization_wide_scope",
        "ck_governed_symbols_ck_governed_symbols_organization_wide_scope",
    ),
    (
        "governed_symbols",
        "'organization_private'",
        "ck_governed_symbols_visibility",
        "ck_governed_symbols_ck_governed_symbols_visibility",
    ),
    (
        "organization_symbol_review_decisions",
        'decision = ANY',
        "ck_organization_symbol_review_decisions_decision",
        "ck_organization_symbol_review_decisions_ck_organization_6661",
    ),
    (
        "organization_symbol_review_decisions",
        'rationale',
        "ck_organization_symbol_review_decisions_rationale",
        "ck_organization_symbol_review_decisions_ck_organization_9d95",
    ),
    (
        "organization_symbol_review_submissions",
        'closed_at',
        "ck_organization_symbol_review_submissions_status",
        "ck_organization_symbol_review_submissions_ck_organizati_54e6",
    ),
    (
        "organization_symbol_review_submissions",
        'rationale',
        "ck_organization_symbol_review_submissions_rationale",
        "ck_organization_symbol_review_submissions_ck_organizati_18f9",
    ),
    (
        "user_sessions",
        'purpose',
        "ck_user_sessions_purpose",
        "ck_user_sessions_ck_user_sessions_purpose",
    ),
    (
        "provenance_assessments",
        'processing_outcome',
        "ck_provenance_assessments_processing_outcome",
        "ck_provenance_assessments_ck_provenance_assessments_pro_79e3",
    ),
    (
        "provenance_assessments",
        'rights_disposition',
        "ck_provenance_assessments_rights_disposition",
        "ck_provenance_assessments_ck_provenance_assessments_rig_82b8",
    ),
)


_REPAIR_SQL = """
DO $$
DECLARE
    repair record;
    existing_name text;
BEGIN
    FOR repair IN
        SELECT * FROM (VALUES
            {values}
        ) AS r(table_name, definition_marker, target_name)
    LOOP
        -- STRICT: abort rather than rename the wrong constraint if a marker
        -- matches no constraint, or more than one.
        SELECT c.conname
          INTO STRICT existing_name
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = current_schema()
           AND t.relname = repair.table_name
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%' || repair.definition_marker || '%';

        IF existing_name <> repair.target_name THEN
            EXECUTE format(
                'ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                repair.table_name, existing_name, repair.target_name
            );
        END IF;
    END LOOP;
END
$$;
"""


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _rename(*, restore: bool) -> None:
    values = ",\n            ".join(
        f"({_quote(table)}, {_quote(marker)}, {_quote(previous if restore else target)})"
        for table, marker, target, previous in _REPAIRS
    )
    op.execute(_REPAIR_SQL.format(values=values))


def upgrade() -> None:
    _rename(restore=False)


def downgrade() -> None:
    """Restore the deployed names, so older rollback paths keep working.

    Not a no-op, unlike 20260909_0050's. These fifteen constraints were created
    by migrations well before this one, and two of those migrations drop them
    by name in their own `downgrade()`. Alembic applies `NAMING_CONVENTION` to
    `op.drop_constraint`, so those drops ask for the doubled name; leaving the
    repaired names in place made `alembic downgrade` past 20260829_0033 fail
    with

        constraint "ck_governed_symbols_ck_governed_symbols_organization_wide_scope"
        of relation "governed_symbols" does not exist

    Restoring the previous names keeps every rollback path intact and makes
    this migration properly reversible. Idempotent in both directions: a rename
    is skipped when the constraint already carries its target name.
    """
    _rename(restore=True)
