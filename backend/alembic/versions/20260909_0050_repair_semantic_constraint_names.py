"""repair double-prefixed check constraint names on the semantic model tables

Revision ID: 20260909_0050
Revises: 20260909_0049
Create Date: 2026-09-09 00:00:00.000000

Migrations 20260909_0047 (SM-P0-01) and 20260909_0048 (SM-P0-02) passed
already-prefixed names to `sa.CheckConstraint` inside `op.create_table`.
`NAMING_CONVENTION` in `models/base.py` prepends `ck_<table>_` to whatever it
is given, so those names were emitted twice over and -- where the result
passed 63 characters -- silently truncated by SQLAlchemy with a 4-character
hash suffix. PostgreSQL never saw an over-long name, so nothing failed and the
length guard in `test_semantic_concept_core.py` could not see it either: the
truncated name never appears in the source.

The result is 16 constraints whose database names disagree with the ORM:

    ck_semantic_concepts_ck_semantic_concepts_concept_kind
    ck_semantic_concept_revisions_ck_semantic_concept_revis_c1bc
    ck_symbol_semantic_assignments_ck_symbol_semantic_assig_ccb4   ... and so on

Nothing dispatches on those names yet, so this is latent rather than broken --
but `semantic_concepts.py` already catches an `IntegrityError` by constraint
name for the concept-code index, and the next module to do that for a check
constraint would silently never match. This migration renames all 16 to the
names the ORM declares. It changes no expression, column, index or row.

Constraints are located by their *definition* rather than their current name,
so this applies equally to a database built by those migrations and one built
from the ORM metadata. `INTO STRICT` makes an ambiguous or missing match abort
the migration instead of renaming the wrong constraint.

SM-P0-03's tables (20260909_0049) already pass bare names and are untouched
here. `20260904_0038`'s nine `product_usage_events` constraints have the same
defect and are deliberately left alone: they are deployed, unrelated to the
semantic model, and renaming them is a separate decision.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260909_0050"
down_revision: Union[str, None] = "20260909_0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (table, a substring unique to that constraint's definition, the ORM's name).
# Markers avoid the current name entirely, because seven of the sixteen carry a
# hash suffix that only exists because the name was too long.
_REPAIRS: tuple[tuple[str, str, str], ...] = (
    ("semantic_concepts", "concept_code", "ck_semantic_concepts_concept_code_grammar"),
    ("semantic_concepts", "concept_kind", "ck_semantic_concepts_concept_kind"),
    ("semantic_concepts", "status", "ck_semantic_concepts_status"),
    ("semantic_concept_revisions", "lifecycle_state", "ck_semantic_concept_revisions_lifecycle_state"),
    ("semantic_concept_revisions", "revision_label", "ck_semantic_concept_revisions_revision_label"),
    ("semantic_concept_revisions", "preferred_name", "ck_semantic_concept_revisions_preferred_name"),
    ("semantic_concept_revisions", "definition", "ck_semantic_concept_revisions_definition"),
    ("semantic_concept_revisions", "aliases_json", "ck_semantic_concept_revisions_aliases_json_array"),
    ("semantic_concept_revisions", "notes", "ck_semantic_concept_revisions_notes"),
    ("semantic_concept_revisions", "rationale", "ck_semantic_concept_revisions_rationale"),
    ("symbol_semantic_assignments", "assignment_role", "ck_symbol_semantic_assignments_assignment_role"),
    # 'verified', not 'status': two constraints on this table mention status.
    ("symbol_semantic_assignments", "verified", "ck_symbol_semantic_assignments_status"),
    ("symbol_semantic_assignments", "source_mapping", "ck_symbol_semantic_assignments_method"),
    ("symbol_semantic_assignments", "confidence", "ck_symbol_semantic_assignments_confidence"),
    ("symbol_semantic_assignments", "reviewed_at", "ck_symbol_semantic_assignments_review_decision"),
    ("symbol_semantic_assignments", "evidence_json", "ck_symbol_semantic_assignments_evidence_json_object"),
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


def upgrade() -> None:
    values = ",\n            ".join(
        f"({_quote(table)}, {_quote(marker)}, {_quote(target)})"
        for table, marker, target in _REPAIRS
    )
    op.execute(_REPAIR_SQL.format(values=values))


def downgrade() -> None:
    """Deliberately a no-op.

    The names this migration replaced were defects: double-prefixed, and seven
    of them hash-truncated. Restoring them would mean writing SQLAlchemy's
    truncation hashes into a migration by hand and reintroducing the drift.
    Nothing reads these names, so leaving the corrected ones in place across a
    downgrade is safe, and `upgrade()` is idempotent -- on a re-upgrade every
    constraint already carries its target name and no rename is issued.
    """
