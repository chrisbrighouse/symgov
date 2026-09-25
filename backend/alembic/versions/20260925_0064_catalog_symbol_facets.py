"""add the Catalog facet store

Revision ID: 20260925_0064
Revises: 20260925_0063
Create Date: 2026-09-25 00:00:00.000000

The Catalog page worked out every symbol's disciplines, categories, formats
and use cases in the browser, from the full list it downloaded on load. To
search, filter, count and page in the database instead, those derived values
are stored here, one row per symbol revision, computed in Python by
`catalog_facets.py` (a port of the browser's own rules, which are keyword and
text heuristics that plain SQL cannot express).

Nothing here decides visibility. Every search joins its candidates from the
live eligibility rules first, and only then reads this table, so a stale or
missing row can change which filter a symbol matches but never who can see it.

Staleness is ruled out by generation counters rather than by deleting rows.
A revision's payload is rewritten after creation (DXF preview backfill,
publication handoff, draft asset updates), and the derived values also read
the governed symbol's slug, name, category, discipline and Catalog ID. A
BEFORE UPDATE trigger on each table bumps `catalog_facet_generation` in the
same row version as the change, and a facet row is only used when both
generations it was computed from still match. A fill that reads an old
payload therefore can never be mistaken for a current one, whatever order
concurrent transactions commit in.

The generation columns are deliberately not mapped in the ORM models: many
disposable-PostgreSQL fixtures pin older heads, and an ORM column those
databases lack would break every `governed_symbols` query in them. The
server default keeps ORM inserts valid.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260925_0064"
down_revision: Union[str, None] = "20260925_0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "symbol_revisions",
        sa.Column("catalog_facet_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "governed_symbols",
        sa.Column("catalog_facet_generation", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "catalog_symbol_facets",
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "governed_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rules_version", sa.Integer(), nullable=False),
        sa.Column("revision_generation", sa.BigInteger(), nullable=False),
        sa.Column("symbol_generation", sa.BigInteger(), nullable=False),
        sa.Column("display_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("id_sort_key", sa.Text(), nullable=False),
        sa.Column("name_sort_key", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("symbol_family", sa.Text(), nullable=False),
        sa.Column("disciplines", postgresql.JSONB(), nullable=False),
        sa.Column("categories", postgresql.JSONB(), nullable=False),
        sa.Column("formats", postgresql.JSONB(), nullable=False),
        sa.Column("use_cases", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("symbol_revision_id"),
    )
    op.create_index("ix_catalog_symbol_facets_symbol", "catalog_symbol_facets", ["governed_symbol_id"])
    for column in ("disciplines", "categories", "formats", "use_cases"):
        op.create_index(
            f"ix_catalog_symbol_facets_{column}",
            "catalog_symbol_facets",
            [column],
            postgresql_using="gin",
        )

    op.execute(
        """
        CREATE FUNCTION bump_revision_catalog_facet_generation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.payload_json IS DISTINCT FROM OLD.payload_json THEN
                NEW.catalog_facet_generation := OLD.catalog_facet_generation + 1;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_symbol_revisions_catalog_facet_generation
        BEFORE UPDATE OF payload_json ON symbol_revisions
        FOR EACH ROW EXECUTE FUNCTION bump_revision_catalog_facet_generation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION bump_symbol_catalog_facet_generation()
        RETURNS trigger LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.slug IS DISTINCT FROM OLD.slug
               OR NEW.canonical_name IS DISTINCT FROM OLD.canonical_name
               OR NEW.category IS DISTINCT FROM OLD.category
               OR NEW.discipline IS DISTINCT FROM OLD.discipline
               OR NEW.catalog_symbol_id IS DISTINCT FROM OLD.catalog_symbol_id THEN
                NEW.catalog_facet_generation := OLD.catalog_facet_generation + 1;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_governed_symbols_catalog_facet_generation
        BEFORE UPDATE OF slug, canonical_name, category, discipline, catalog_symbol_id
        ON governed_symbols
        FOR EACH ROW EXECUTE FUNCTION bump_symbol_catalog_facet_generation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_governed_symbols_catalog_facet_generation ON governed_symbols")
    op.execute("DROP FUNCTION IF EXISTS bump_symbol_catalog_facet_generation()")
    op.execute("DROP TRIGGER IF EXISTS trg_symbol_revisions_catalog_facet_generation ON symbol_revisions")
    op.execute("DROP FUNCTION IF EXISTS bump_revision_catalog_facet_generation()")
    for column in ("use_cases", "formats", "categories", "disciplines"):
        op.drop_index(f"ix_catalog_symbol_facets_{column}", table_name="catalog_symbol_facets")
    op.drop_index("ix_catalog_symbol_facets_symbol", table_name="catalog_symbol_facets")
    op.drop_table("catalog_symbol_facets")
    op.drop_column("governed_symbols", "catalog_facet_generation")
    op.drop_column("symbol_revisions", "catalog_facet_generation")
