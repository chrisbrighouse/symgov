"""canonical Catalog symbol IDs without leading zeros: S-000001 becomes S-1

Revision ID: 20260926_0066
Revises: 20260926_0065
Create Date: 2026-09-26 00:00:00.000000

Decided 2026-09-26: every canonical Catalog symbol ID is `S-<n>` with no zero
padding, so the number grows without a width limit, and the padded IDs are
retired, not kept as aliases. The allocator already writes the new form
(`catalog_symbol_ids.format_allocated_catalog_symbol_id`); this renumbers the
IDs issued before it.

This is the specification's reviewed data correction (section 4.4), applied to
every padded canonical ID at once:

* the new `S-<n>` keeps the same number and becomes the symbol's canonical
  registry row, with allocation source `reviewed_correction`;
* the old `S-0...` row becomes a tombstone with no target, so it stops
  resolving and can never be issued again;
* both rows record the change time and reason. `changed_by` is null: this is a
  migration, not a person.

`governed_symbols.catalog_symbol_id` moves to the new value, and the facet
trigger from `20260925_0064` bumps each changed row's facet generation. Saved
Catalog clipboards keep a snapshot of each item's ID label, so those labels
are rewritten too. Historical aliases (left by demotion) are not canonical
IDs and are left as they are.

The upgrade refuses to run if any new `S-<n>` is already in the registry,
rather than guessing, and fires the registry's consistency and publication
checks before it finishes. Downgrade restores nothing: a tombstone is permanent by
design, and the old values are named in each row's change reason.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260926_0066"
down_revision: Union[str, None] = "20260926_0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REASON = "Canonical Catalog symbol IDs without leading zeros (decision 2026-09-26)"

# The registry's cross-table checks. Retiring a row before its replacement is
# inserted is only consistent at the end, so they are deferred for the
# renumbering -- an earlier migration in the same run (`20260823_0030a`) may
# have made them immediate -- and fired at the end, which both validates the
# result and clears the pending events, as 0030a does.
CONSISTENCY_TRIGGERS = (
    "trg_governed_symbols_validate_catalog_symbol_consistency, "
    "trg_catalog_symbol_identifiers_validate_consistency, "
    "trg_governed_symbols_validate_catalog_publication, "
    "trg_catalog_symbol_identifiers_validate_publication"
)


def upgrade() -> None:
    op.execute(f"SET CONSTRAINTS {CONSISTENCY_TRIGGERS} DEFERRED")
    op.execute(
        """
        CREATE TEMP TABLE catalog_symbol_id_renumbering ON COMMIT DROP AS
        SELECT
            identifier AS old_identifier,
            'S-' || ltrim(substr(identifier, 3), '0') AS new_identifier,
            governed_symbol_id
        FROM catalog_symbol_identifiers
        WHERE role = 'canonical'
          AND identifier ~ '^S-0+[1-9][0-9]*$'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM catalog_symbol_id_renumbering r
                JOIN catalog_symbol_identifiers existing ON existing.identifier = r.new_identifier
            ) THEN
                RAISE EXCEPTION 'unpadded catalog symbol ID already in the registry; refusing to renumber';
            END IF;
        END
        $$
        """
    )
    # Retire the old value first: one canonical row per symbol is a unique index.
    op.execute(
        f"""
        UPDATE catalog_symbol_identifiers csi
        SET role = 'tombstone',
            governed_symbol_id = NULL,
            changed_at = now(),
            changed_by = NULL,
            change_reason = '{REASON}; replaced by ' || r.new_identifier
        FROM catalog_symbol_id_renumbering r
        WHERE csi.identifier = r.old_identifier
        """
    )
    op.execute(
        f"""
        INSERT INTO catalog_symbol_identifiers
            (identifier, role, governed_symbol_id, allocation_source, allocated_at, changed_at, changed_by, change_reason)
        SELECT
            r.new_identifier, 'canonical', r.governed_symbol_id, 'reviewed_correction', now(), now(), NULL,
            '{REASON}; replaces ' || r.old_identifier
        FROM catalog_symbol_id_renumbering r
        """
    )
    op.execute(
        """
        UPDATE governed_symbols gs
        SET catalog_symbol_id = r.new_identifier
        FROM catalog_symbol_id_renumbering r
        WHERE gs.id = r.governed_symbol_id
        """
    )
    op.execute(
        """
        UPDATE catalog_workbench_clipboards c
        SET items_json = (
            SELECT jsonb_agg(
                CASE WHEN r.new_identifier IS NULL THEN item
                     ELSE jsonb_set(item, '{displayName}', to_jsonb(r.new_identifier))
                END
                ORDER BY position
            )
            FROM jsonb_array_elements(c.items_json) WITH ORDINALITY AS items(item, position)
            LEFT JOIN catalog_symbol_id_renumbering r ON r.old_identifier = item->>'displayName'
        )
        WHERE jsonb_typeof(c.items_json) = 'array'
          AND jsonb_array_length(c.items_json) > 0
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(c.items_json) AS items(item)
              JOIN catalog_symbol_id_renumbering r ON r.old_identifier = item->>'displayName'
          )
        """
    )
    op.execute(f"SET CONSTRAINTS {CONSISTENCY_TRIGGERS} IMMEDIATE")


def downgrade() -> None:
    # A tombstone is permanent: the padded IDs are never reissued.
    pass
