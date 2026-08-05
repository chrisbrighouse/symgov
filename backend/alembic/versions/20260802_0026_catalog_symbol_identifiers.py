"""add durable catalog symbol identifiers

Revision ID: 20260802_0026
Revises: 20260730_0025
Create Date: 2026-08-02 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260802_0026"
down_revision: Union[str, None] = "20260730_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_symbol_identifiers",
        sa.Column("identifier", sa.Text(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("governed_symbol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_symbols.id", ondelete="SET NULL"), nullable=True),
        sa.Column("allocation_source", sa.Text(), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("identifier", name="pk_catalog_symbol_identifiers"),
        sa.CheckConstraint(
            "role in ('canonical', 'historical_alias', 'tombstone')",
            name="ck_catalog_symbol_identifiers_role",
        ),
        sa.CheckConstraint(
            "allocation_source in ('legacy_backfill', 'global_sequence', 'reviewed_correction')",
            name="ck_catalog_symbol_identifiers_allocation_source",
        ),
        sa.CheckConstraint(
            "(role = 'tombstone' and governed_symbol_id is null) or (role in ('canonical', 'historical_alias') and governed_symbol_id is not null)",
            name="ck_catalog_symbol_identifiers_role_target",
        ),
        sa.CheckConstraint(
            "identifier = upper(identifier) and identifier ~ '^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$'",
            name="ck_catalog_symbol_identifiers_grammar",
        ),
    )
    op.create_index("uq_catalog_symbol_identifiers_canonical_governed_symbol", "catalog_symbol_identifiers", ["governed_symbol_id"], unique=True, postgresql_where=sa.text("role = 'canonical'"))

    op.add_column("governed_symbols", sa.Column("catalog_symbol_id", sa.Text(), nullable=True))
    op.create_index("uq_governed_symbols_catalog_symbol_id", "governed_symbols", ["catalog_symbol_id"], unique=True)
    op.create_foreign_key("fk_governed_symbols_catalog_symbol_id", "governed_symbols", "catalog_symbol_identifiers", ["catalog_symbol_id"], ["identifier"], ondelete="RESTRICT")

    op.execute("CREATE SEQUENCE catalog_symbol_id_seq START 1 NO CYCLE")
    op.execute(
        """
        CREATE FUNCTION validate_catalog_symbol_identifier_consistency()
        RETURNS TRIGGER AS $$
        DECLARE
            affected_identifiers TEXT[];
            affected_governed_symbol_ids UUID[];
        BEGIN
            IF TG_TABLE_NAME = 'catalog_symbol_identifiers' THEN
                affected_identifiers := ARRAY[NEW.identifier];
                affected_governed_symbol_ids := ARRAY[NEW.governed_symbol_id];
                IF TG_OP = 'UPDATE' THEN
                    affected_identifiers := affected_identifiers || OLD.identifier;
                    affected_governed_symbol_ids := affected_governed_symbol_ids || OLD.governed_symbol_id;
                END IF;
            ELSIF TG_TABLE_NAME = 'governed_symbols' THEN
                affected_identifiers := ARRAY[NEW.catalog_symbol_id];
                affected_governed_symbol_ids := ARRAY[NEW.id];
                IF TG_OP = 'UPDATE' THEN
                    affected_identifiers := affected_identifiers || OLD.catalog_symbol_id;
                    affected_governed_symbol_ids := affected_governed_symbol_ids || OLD.id;
                END IF;
            END IF;

            affected_identifiers := array_remove(affected_identifiers, NULL);
            affected_governed_symbol_ids := array_remove(affected_governed_symbol_ids, NULL);

            IF EXISTS (
                SELECT 1 FROM governed_symbols AS gs
                WHERE gs.catalog_symbol_id = ANY (affected_identifiers)
                  AND NOT EXISTS (
                      SELECT 1 FROM catalog_symbol_identifiers AS csi
                      WHERE csi.identifier = gs.catalog_symbol_id
                        AND csi.role = 'canonical'
                        AND csi.governed_symbol_id = gs.id
                  )
            ) OR EXISTS (
                SELECT 1 FROM governed_symbols AS gs
                WHERE gs.id = ANY (affected_governed_symbol_ids)
                  AND gs.catalog_symbol_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM catalog_symbol_identifiers AS csi
                      WHERE csi.identifier = gs.catalog_symbol_id
                        AND csi.role = 'canonical'
                        AND csi.governed_symbol_id = gs.id
                  )
            ) THEN
                RAISE EXCEPTION 'governed symbol catalog identifier is not its canonical registry identifier';
            END IF;

            IF EXISTS (
                SELECT 1 FROM catalog_symbol_identifiers AS csi
                WHERE csi.identifier = ANY (affected_identifiers)
                  AND csi.role = 'canonical'
                  AND NOT EXISTS (
                      SELECT 1 FROM governed_symbols AS gs
                      WHERE gs.id = csi.governed_symbol_id
                        AND gs.catalog_symbol_id = csi.identifier
                  )
            ) OR EXISTS (
                SELECT 1 FROM catalog_symbol_identifiers AS csi
                WHERE csi.governed_symbol_id = ANY (affected_governed_symbol_ids)
                  AND csi.role = 'canonical'
                  AND NOT EXISTS (
                      SELECT 1 FROM governed_symbols AS gs
                      WHERE gs.id = csi.governed_symbol_id
                        AND gs.catalog_symbol_id = csi.identifier
                  )
            ) THEN
                RAISE EXCEPTION 'canonical catalog identifier is not linked by its governed symbol';
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_catalog_symbol_identifiers_validate_consistency
        AFTER INSERT OR UPDATE ON catalog_symbol_identifiers
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION validate_catalog_symbol_identifier_consistency()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_governed_symbols_validate_catalog_symbol_consistency
        AFTER INSERT OR UPDATE ON governed_symbols
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION validate_catalog_symbol_identifier_consistency()
        """
    )


def downgrade() -> None:
    # PostgreSQL holds these locks until the migration transaction ends, so no
    # writer can race either emptiness guard or the subsequent teardown.
    op.execute("LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM catalog_symbol_identifiers LIMIT 1) THEN
                RAISE EXCEPTION 'cannot downgrade while catalog symbol identifiers exist';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM governed_symbols WHERE catalog_symbol_id IS NOT NULL LIMIT 1) THEN
                RAISE EXCEPTION 'cannot downgrade while governed symbols have catalog identifiers';
            END IF;
        END;
        $$
        """
    )

    op.execute("DROP TRIGGER IF EXISTS trg_catalog_symbol_identifiers_validate_consistency ON catalog_symbol_identifiers")
    op.execute("DROP TRIGGER IF EXISTS trg_governed_symbols_validate_catalog_symbol_consistency ON governed_symbols")
    op.execute("DROP FUNCTION IF EXISTS validate_catalog_symbol_identifier_consistency()")
    op.drop_constraint(
        "fk_governed_symbols_catalog_symbol_id",
        "governed_symbols",
        type_="foreignkey",
    )
    op.drop_index("uq_governed_symbols_catalog_symbol_id", table_name="governed_symbols")
    op.drop_column("governed_symbols", "catalog_symbol_id")
    op.execute("DROP SEQUENCE catalog_symbol_id_seq")
    op.drop_index(
        "uq_catalog_symbol_identifiers_canonical_governed_symbol",
        table_name="catalog_symbol_identifiers",
    )
    op.drop_table("catalog_symbol_identifiers")
