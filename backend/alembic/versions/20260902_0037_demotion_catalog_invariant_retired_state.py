"""fix catalog publication invariant to ignore retired page/entry rows (Stage 7 WP7.4)

Revision ID: 20260902_0037
Revises: 20260902_0036
Create Date: 2026-09-02 02:00:00.000000

`validate_catalog_symbol_publication_invariant()` (`20260826_0031`) requires
a canonical `catalog_symbol_identifiers` row to exist whenever a
`symbol_revisions` row is `published`, OR whenever ANY `published_pages`/
`pack_entries` row exists referencing that revision -- written before
`publication_state` existed, so "exists" meant "is live." Demotion (WP7.4)
never deletes a page/entry row (per programme plan §13 task 10: "never
delete... revision history"), it only marks it `publication_state='retired'`
and releases the governed symbol's canonical catalog identifier (moving the
registry row to `historical_alias`, per the same "retain history" rule).
Without this fix, that combination is structurally impossible: the
invariant would still demand a canonical identifier for a revision whose
only page/entry rows are retired, even though the symbol is no longer
public and correctly has no live canonical identifier.

This migration only narrows the trigger's own `EXISTS` checks to
`publication_state = 'active'` rows; it does not relax the invariant for
anything currently public. A currently-active published_pages/pack_entries
row still requires a canonical identifier exactly as before.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0037"
down_revision: Union[str, None] = "20260902_0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_FUNCTION = """
    CREATE OR REPLACE FUNCTION validate_catalog_symbol_publication_invariant()
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
                      AND pp.publication_state = 'active'
                )
                OR EXISTS (
                    SELECT 1 FROM pack_entries pe
                    WHERE pe.symbol_revision_id = sr.id
                      AND pe.publication_state = 'active'
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

_OLD_FUNCTION = """
    CREATE OR REPLACE FUNCTION validate_catalog_symbol_publication_invariant()
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


def upgrade() -> None:
    op.execute(_NEW_FUNCTION)


def downgrade() -> None:
    op.execute(_OLD_FUNCTION)
