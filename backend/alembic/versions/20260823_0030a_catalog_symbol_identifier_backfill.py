"""backfill canonical catalog identifiers for pre-existing governed symbols

Revision ID: 20260823_0030a
Revises: 20260822_0030

20260802_0026 introduces `catalog_symbol_identifiers` empty and adds a
nullable `governed_symbols.catalog_symbol_id`, populating neither.
20260826_0031 then enforces that every published symbol already carries a
matching canonical identifier. Nothing bridged the two, so any database
holding published symbols could not cross 20260826_0031 -- its preflight
raised `published symbol lacks matching canonical catalog identifier`.

This revision is that bridge. It allocates a canonical identifier for
every governed symbol that lacks one, using the same sequence and the
same `S-%06d` format as `catalog_symbol_ids.format_allocated_catalog_symbol_id`,
recorded with `allocation_source = 'legacy_backfill'` -- the source value
that exists precisely for symbols predating the registry.
"""
from alembic import op

revision = "20260823_0030a"
down_revision = "20260822_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL holds these locks until the migration transaction ends, so no
    # writer can allocate an identifier or publish a revision while the
    # backfill is deciding which symbols still need one.
    op.execute("LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE")

    # Every symbol is backfilled, not only the currently-published ones: the
    # 20260826_0031 invariant fires on publication, so a draft promoted later
    # would otherwise trip a trigger that this migration could have satisfied.
    op.execute(
        """
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
        $$
        """
    )

    # The 20260802_0026 consistency triggers are DEFERRABLE INITIALLY
    # DEFERRED, so the rows touched above leave pending trigger events that
    # would otherwise survive to the end of the transaction. `alembic
    # upgrade` runs the whole chain in one transaction (env.py sets no
    # transaction_per_migration), and 20260829_0033 does
    # `ALTER TABLE governed_symbols ADD COLUMN owner_organization_id` --
    # PostgreSQL refuses to alter a table that has pending trigger events.
    # Firing them now both clears that and validates this backfill against
    # the same invariant the triggers enforce at runtime.
    op.execute(
        "SET CONSTRAINTS "
        "trg_governed_symbols_validate_catalog_symbol_consistency, "
        "trg_catalog_symbol_identifiers_validate_consistency IMMEDIATE"
    )

    # Fail here rather than deferring to 20260826_0031's preflight, so a
    # shortfall is attributed to the backfill that caused it.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM governed_symbols gs
                LEFT JOIN catalog_symbol_identifiers csi
                  ON csi.identifier = gs.catalog_symbol_id
                 AND csi.role = 'canonical'
                 AND csi.governed_symbol_id = gs.id
                WHERE gs.catalog_symbol_id IS NULL OR csi.identifier IS NULL
            ) THEN
                RAISE EXCEPTION 'catalog identifier backfill incomplete: governed symbol lacks matching canonical catalog identifier'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    # Retain locks through the transaction so the teardown cannot race a
    # publication or a fresh allocation.
    op.execute("LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE")

    # Unlink before deleting: fk_governed_symbols_catalog_symbol_id is
    # ON DELETE RESTRICT, so the registry rows cannot go first. Only
    # legacy_backfill rows are touched, leaving identifiers allocated by
    # normal operation intact.
    op.execute(
        """
        UPDATE governed_symbols
           SET catalog_symbol_id = NULL
         WHERE catalog_symbol_id IN (
             SELECT identifier
             FROM catalog_symbol_identifiers
             WHERE allocation_source = 'legacy_backfill'
               AND role = 'canonical'
         )
        """
    )
    op.execute(
        "DELETE FROM catalog_symbol_identifiers WHERE allocation_source = 'legacy_backfill'"
    )

    # Same reasoning as upgrade(): clear the deferred trigger events before
    # this transaction continues into any later table alteration.
    op.execute(
        "SET CONSTRAINTS "
        "trg_governed_symbols_validate_catalog_symbol_consistency, "
        "trg_catalog_symbol_identifiers_validate_consistency IMMEDIATE"
    )
