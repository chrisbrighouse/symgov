"""enforce canonical identity for every published Catalog symbol

Revision ID: 20260826_0031
Revises: 20260822_0030
Create Date: 2026-08-26 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260826_0031"
down_revision: Union[str, None] = "20260822_0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PUBLICATION_INCONSISTENCY = """
    SELECT 1
    FROM symbol_revisions sr
    JOIN governed_symbols gs ON gs.id = sr.symbol_id
    LEFT JOIN catalog_symbol_identifiers csi
      ON csi.identifier = gs.catalog_symbol_id
     AND csi.role = 'canonical'
     AND csi.governed_symbol_id = gs.id
    WHERE (
        sr.lifecycle_state = 'published'
        OR EXISTS (
            SELECT 1 FROM published_pages pp
            WHERE pp.current_symbol_revision_id = sr.id
        )
        OR EXISTS (
            SELECT 1 FROM pack_entries pe
            WHERE pe.symbol_revision_id = sr.id
        )
    )
      AND (gs.catalog_symbol_id IS NULL OR csi.identifier IS NULL)
    LIMIT 1
"""


def _assert_publication_consistency(message: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS ({_PUBLICATION_INCONSISTENCY}) THEN
                RAISE EXCEPTION '{message}' USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )


def upgrade() -> None:
    _assert_publication_consistency(
        "catalog publication invariant preflight failed: published symbol lacks matching canonical catalog identifier"
    )
    op.execute(
        """
        CREATE FUNCTION validate_catalog_symbol_publication_invariant()
        RETURNS TRIGGER AS $$
        DECLARE
            affected_revision_ids UUID[] := ARRAY[]::UUID[];
            affected_symbol_ids UUID[] := ARRAY[]::UUID[];
        BEGIN
            IF TG_TABLE_NAME = 'symbol_revisions' THEN
                affected_revision_ids := ARRAY[NEW.id];
                affected_symbol_ids := ARRAY[NEW.symbol_id];
                IF TG_OP = 'UPDATE' THEN
                    affected_revision_ids := affected_revision_ids || OLD.id;
                    affected_symbol_ids := affected_symbol_ids || OLD.symbol_id;
                END IF;
            ELSIF TG_TABLE_NAME = 'published_pages' THEN
                affected_revision_ids := ARRAY[NEW.current_symbol_revision_id];
                IF TG_OP = 'UPDATE' THEN
                    affected_revision_ids := affected_revision_ids || OLD.current_symbol_revision_id;
                END IF;
            ELSIF TG_TABLE_NAME = 'pack_entries' THEN
                affected_revision_ids := ARRAY[NEW.symbol_revision_id];
                IF TG_OP = 'UPDATE' THEN
                    affected_revision_ids := affected_revision_ids || OLD.symbol_revision_id;
                END IF;
            ELSIF TG_TABLE_NAME = 'governed_symbols' THEN
                affected_symbol_ids := ARRAY[NEW.id];
                IF TG_OP = 'UPDATE' THEN
                    affected_symbol_ids := affected_symbol_ids || OLD.id;
                END IF;
            ELSIF TG_TABLE_NAME = 'catalog_symbol_identifiers' THEN
                affected_symbol_ids := ARRAY[NEW.governed_symbol_id];
                IF TG_OP = 'UPDATE' THEN
                    affected_symbol_ids := affected_symbol_ids || OLD.governed_symbol_id;
                END IF;
            END IF;

            affected_revision_ids := array_remove(affected_revision_ids, NULL);
            affected_symbol_ids := array_remove(affected_symbol_ids, NULL);

            IF EXISTS (
                SELECT 1
                FROM symbol_revisions sr
                JOIN governed_symbols gs ON gs.id = sr.symbol_id
                WHERE (
                    sr.id = ANY (affected_revision_ids)
                    OR sr.symbol_id = ANY (affected_symbol_ids)
                )
                  AND (
                    sr.lifecycle_state = 'published'
                    OR EXISTS (
                        SELECT 1 FROM published_pages pp
                        WHERE pp.current_symbol_revision_id = sr.id
                    )
                    OR EXISTS (
                        SELECT 1 FROM pack_entries pe
                        WHERE pe.symbol_revision_id = sr.id
                    )
                  )
                  AND NOT EXISTS (
                    SELECT 1
                    FROM catalog_symbol_identifiers csi
                    WHERE csi.identifier = gs.catalog_symbol_id
                      AND csi.role = 'canonical'
                      AND csi.governed_symbol_id = gs.id
                  )
            ) THEN
                RAISE EXCEPTION 'published symbol lacks matching canonical catalog identifier'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_symbol_revisions_validate_catalog_publication
        AFTER INSERT OR UPDATE ON symbol_revisions DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_catalog_symbol_publication_invariant()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_published_pages_validate_catalog_publication
        AFTER INSERT OR UPDATE ON published_pages DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_catalog_symbol_publication_invariant()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_pack_entries_validate_catalog_publication
        AFTER INSERT OR UPDATE ON pack_entries DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_catalog_symbol_publication_invariant()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_governed_symbols_validate_catalog_publication
        AFTER INSERT OR UPDATE ON governed_symbols DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_catalog_symbol_publication_invariant()
    """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_catalog_symbol_identifiers_validate_publication
        AFTER INSERT OR UPDATE ON catalog_symbol_identifiers DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_catalog_symbol_publication_invariant()
    """)


def downgrade() -> None:
    # Retain locks through the migration transaction so the safety check cannot
    # race a publication or identifier change while the invariant is removed.
    op.execute("LOCK TABLE catalog_symbol_identifiers IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE symbol_revisions IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE published_pages IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE pack_entries IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM symbol_revisions WHERE lifecycle_state = 'published'
            ) OR EXISTS (
                SELECT 1 FROM published_pages
            ) OR EXISTS (
                SELECT 1 FROM pack_entries
            ) THEN
                RAISE EXCEPTION 'catalog publication invariant downgrade refused: published rows exist'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )
    _assert_publication_consistency(
        "catalog publication invariant downgrade refused: published symbol lacks matching canonical catalog identifier"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_symbol_revisions_validate_catalog_publication ON symbol_revisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_published_pages_validate_catalog_publication ON published_pages"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_pack_entries_validate_catalog_publication ON pack_entries"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_governed_symbols_validate_catalog_publication ON governed_symbols"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_catalog_symbol_identifiers_validate_publication ON catalog_symbol_identifiers"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_catalog_symbol_publication_invariant()"
    )
