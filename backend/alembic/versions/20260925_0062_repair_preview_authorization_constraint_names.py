"""repair double-prefixed check constraint names on published_preview_authorizations

Revision ID: 20260925_0062
Revises: 20260919_0061
Create Date: 2026-09-25 00:00:00.000000

20260919_0061 passed already-prefixed names to `sa.CheckConstraint`, the defect
20260909_0050 repaired for the semantic model. `NAMING_CONVENTION` prepends
`ck_<table>_` again, and all three results passed 63 characters, so SQLAlchemy
truncated them with a hash. Production carries:

    ck_published_preview_authorizations_ck_published_previe_50b8
    ck_published_preview_authorizations_ck_published_previe_79c9
    ck_published_preview_authorizations_ck_published_previe_ca48

The ORM declared the same pre-prefixed names, so the two sides agreed and
nothing was broken -- only unreadable, and unmatchable by any code that reads a
check constraint's name. This renames the three to the singly-prefixed names
`schema.py` now declares. It changes no expression, column, index or row.

Constraints are located by their *definition*, as in 0050, so this applies to a
database built by 0061 and to one built from the ORM metadata alike, and
`INTO STRICT` aborts rather than renames the wrong constraint.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260925_0062"
down_revision: Union[str, None] = "20260919_0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "published_preview_authorizations"

# (a substring unique to that constraint's definition, the ORM's name).
_REPAIRS: tuple[tuple[str, str, str], ...] = (
    (_TABLE, "object_key <>", "ck_published_preview_authorizations_object_key_nonempty"),
    (_TABLE, "attachment_sha256 ~", "ck_published_preview_authorizations_sha256"),
    (_TABLE, "attachment_size_bytes >=", "ck_published_preview_authorizations_size_nonnegative"),
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
    """Deliberately a no-op, for 20260909_0050's reason.

    The names this replaced were defects: double-prefixed and hash-truncated.
    Restoring them would mean writing SQLAlchemy's truncation hashes into a
    migration by hand. Nothing reads these names, and `upgrade()` is
    idempotent -- on a re-upgrade every constraint already carries its target
    name and no rename is issued.
    """
