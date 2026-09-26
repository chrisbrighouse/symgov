"""store every governed symbol's discipline under its standard name

Revision ID: 20260926_0065
Revises: 20260925_0064
Create Date: 2026-09-26 00:00:00.000000

User testing X-04 found eleven spellings in `governed_symbols.discipline`
(`Piping`, `general`, `instrumentation`, `Process_instrumentation`, ...). The
Catalog filters already fold them onto the standard list, but the Set builder,
the drafts screens and the detail pane show the stored value, and the review
editor kept offering and re-saving the old spellings. Every writer now stores
the standard name (`catalog_facets.canonical_discipline`); this rewrites the
rows written before that.

Decisions (2026-09-26): `process_instrumentation` becomes Instrumentation &
Controls; `general` becomes General / Annotation, which classification and the
automation gate still treat as a placeholder, because publication wrote
`general` whenever it had no discipline at all.

Only `governed_symbols` is rewritten. `classification_records` is agent
evidence and keeps what was recorded; publication converts it on the way
through. Each changed row gets an `audit_events` entry holding the previous
value, and the facet trigger from `20260925_0064` bumps the row's facet
generation, so the Catalog facets recompute. `updated_at` is left alone: this
is a spelling correction, not a change anyone made to the symbol.

The mapping is frozen here rather than imported, so this migration keeps
meaning what it meant when it ran. A value it does not recognise is left as it
is.

Downgrade restores nothing: the previous values are in the audit entries and
in the pre-deploy dump, and no schema changed.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260926_0065"
down_revision: Union[str, None] = "20260925_0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Stored spelling, keyed as lower case with runs of whitespace and hyphens
# made `_` (catalog_facets `_normalized_key`), to its standard name.
STANDARD_DISCIPLINES = {
    "architectural": "Architectural",
    "civil": "Civil / Structural",
    "civil_/_structural": "Civil / Structural",
    "controls": "Instrumentation & Controls",
    "elec": "Electrical",
    "electrical": "Electrical",
    "fire": "Fire & Life Safety",
    "fire_&_life_safety": "Fire & Life Safety",
    "fire_alarm": "Fire & Life Safety",
    "fire_alarms": "Fire & Life Safety",
    "fire_life_safety": "Fire & Life Safety",
    "general": "General / Annotation",
    "general_/_annotation": "General / Annotation",
    "hvac": "HVAC",
    "instrumentation": "Instrumentation & Controls",
    "instrumentation_&_controls": "Instrumentation & Controls",
    "instrumentation_controls": "Instrumentation & Controls",
    "mech": "Mechanical",
    "mechanical": "Mechanical",
    "p_id": "Piping / P&ID",
    "pid": "Piping / P&ID",
    "piping": "Piping / P&ID",
    "piping_/_p&id": "Piping / P&ID",
    "process": "Process",
    "process_instrumentation": "Instrumentation & Controls",
    "safety": "Safety / Signage",
    "safety_/_signage": "Safety / Signage",
    "signage": "Safety / Signage",
    "structural": "Civil / Structural",
    "unknown_discipline": "General / Annotation",
}


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    mapping = ",\n                ".join(
        f"({_sql_literal(key)}, {_sql_literal(name)})" for key, name in sorted(STANDARD_DISCIPLINES.items())
    )
    op.execute(
        f"""
        WITH standard(key, name) AS (
            VALUES
                {mapping}
        ),
        targets AS (
            SELECT g.id, g.discipline AS previous, s.name AS updated
            FROM governed_symbols g
            JOIN standard s
              ON lower(regexp_replace(btrim(g.discipline), '[[:space:]-]+', '_', 'g')) = s.key
            WHERE g.discipline IS DISTINCT FROM s.name
        ),
        changed AS (
            UPDATE governed_symbols g
            SET discipline = t.updated
            FROM targets t
            WHERE g.id = t.id
            RETURNING g.id
        )
        INSERT INTO audit_events (id, entity_type, entity_id, action, actor_id, payload_json, created_at)
        SELECT
            gen_random_uuid(),
            'governed_symbol',
            t.id,
            'discipline_standardized',
            NULL,
            jsonb_build_object(
                'migration', '20260926_0065',
                'decision', 'X-04',
                'previous', t.previous,
                'updated', t.updated
            ),
            now()
        FROM targets t
        JOIN changed c ON c.id = t.id
        """
    )


def downgrade() -> None:
    # Nothing to undo in the schema, and the previous spellings are not
    # restored: they are in the audit entries this upgrade wrote.
    pass
