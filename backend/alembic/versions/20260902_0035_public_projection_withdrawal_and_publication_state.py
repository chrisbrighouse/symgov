"""add withdrawn lifecycle state, publication_state on pages/entries, and a stricter active_public_symbol_projections view

Revision ID: 20260902_0035
Revises: 20260901_0034
Create Date: 2026-09-02 00:00:00.000000

Stage 7 (WP7.1) public-projection migration, per the programme plan §13:
- `symbol_revisions.lifecycle_state` gains `withdrawn` (demotion sets every
  `published` revision of a demoted governed symbol to `withdrawn`; re-promotion
  requires a fresh public request/review for a newly approved target revision).
- `published_pages`/`pack_entries` gain checked `publication_state = active | retired`
  plus nullable retirement actor/time/reason. Existing rows are backfilled to
  `active`; the column keeps a server default of `active` so the current
  pre-Stage-7 publication writer (`execute_publication_handoff`/`runtime.py`,
  which does not yet set this column) remains compatible during a rolling
  deployment. A Stage 7 writer that performs a real demotion must set the
  retirement columns explicitly rather than relying on the default.
- `active_public_symbol_projections` is replaced with a stricter definition
  that additionally requires the page and pack-entry projection to still be
  `active` (not merely present) alongside the existing public-visibility/
  published-lifecycle/public-audience/published-package predicates. Demotion
  never deletes page/entry/package/revision history (per §13 task 10 and the
  spec's §10.3), so this view is the sole mechanism that excludes a retired
  projection from every downstream public reader that joins it.

Note on `symbol_revisions.lifecycle_state`'s existing check constraint name:
the constraint the initial migration (20260409_0001) created inline inside
`op.create_table(...)` was given the already-prefixed literal name
`ck_symbol_revisions_lifecycle_state`; because Alembic re-applies this
project's `ck_%(table_name)s_%(constraint_name)s` naming convention even to
an explicitly-supplied name, the constraint that actually exists in the
database today is `ck_symbol_revisions_ck_symbol_revisions_lifecycle_state`
(confirmed empirically against a disposable Postgres instance migrated to
`20260901_0034`, not merely inferred from the ORM model, which declares a
non-matching name and was never the source of truth here). This migration
drops that constraint by its real name and recreates it with a short name
(`lifecycle_state`), which the same convention resolves to the single-
prefixed `ck_symbol_revisions_lifecycle_state` — matching what the ORM model
should have produced all along. `models/schema.py`'s `SymbolRevision`
`__table_args__` is updated in the same change to use the short name so the
model and the database agree going forward; this does not touch the other
pre-existing double-prefixed `governed_symbols` constraints
(`ck_governed_symbols_ck_governed_symbols_visibility` and
`..._organization_wide_scope`), which are out of Stage 7's scope.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260902_0035"
down_revision: Union[str, None] = "20260901_0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- symbol_revisions.lifecycle_state gains 'withdrawn' ---
    op.drop_constraint(
        op.f("ck_symbol_revisions_ck_symbol_revisions_lifecycle_state"),
        "symbol_revisions",
        type_="check",
    )
    op.create_check_constraint(
        "lifecycle_state",
        "symbol_revisions",
        "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn')",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE symbol_revisions VALIDATE CONSTRAINT ck_symbol_revisions_lifecycle_state"
    )

    # --- published_pages: publication_state + retirement metadata ---
    op.add_column(
        "published_pages",
        sa.Column(
            "publication_state",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "published_pages",
        sa.Column("retired_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "published_pages",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "published_pages",
        sa.Column("retirement_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "publication_state",
        "published_pages",
        "publication_state in ('active', 'retired')",
    )
    op.create_check_constraint(
        "retirement_metadata",
        "published_pages",
        "(publication_state = 'active' and retired_by is null and retired_at is null) "
        "or (publication_state = 'retired' and retired_at is not null)",
    )

    # --- pack_entries: publication_state + retirement metadata ---
    op.add_column(
        "pack_entries",
        sa.Column(
            "publication_state",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "pack_entries",
        sa.Column("retired_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "pack_entries",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pack_entries",
        sa.Column("retirement_reason", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "publication_state",
        "pack_entries",
        "publication_state in ('active', 'retired')",
    )
    op.create_check_constraint(
        "retirement_metadata",
        "pack_entries",
        "(publication_state = 'active' and retired_by is null and retired_at is null) "
        "or (publication_state = 'retired' and retired_at is not null)",
    )

    # --- active_public_symbol_projections: stricter definition ---
    op.execute("REVOKE SELECT ON active_public_symbol_projections FROM symgov_app")
    op.execute("DROP VIEW active_public_symbol_projections")
    op.execute(
        """
        CREATE VIEW active_public_symbol_projections AS
        SELECT
            gs.id AS governed_symbol_id,
            gs.catalog_symbol_id,
            sr.id AS symbol_revision_id,
            page.id AS published_page_id,
            entry.id AS pack_entry_id,
            pack.id AS publication_pack_id,
            gs.owner_organization_id,
            gs.visibility,
            gs.organization_wide
        FROM governed_symbols gs
        JOIN symbol_revisions sr
          ON sr.id = gs.current_revision_id
         AND sr.symbol_id = gs.id
        JOIN published_pages page
          ON page.current_symbol_revision_id = sr.id
        JOIN pack_entries entry
          ON entry.symbol_revision_id = sr.id
         AND entry.published_page_id = page.id
         AND entry.pack_id = page.pack_id
        JOIN publication_packs pack
          ON pack.id = page.pack_id
        WHERE gs.visibility = 'public'
          AND sr.lifecycle_state = 'published'
          AND page.publication_state = 'active'
          AND entry.publication_state = 'active'
          AND pack.audience = 'public'
          AND pack.status = 'published'
        """
    )
    op.execute("GRANT SELECT ON active_public_symbol_projections TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON active_public_symbol_projections FROM symgov_app")
    op.execute("DROP VIEW active_public_symbol_projections")
    op.execute(
        """
        CREATE VIEW active_public_symbol_projections AS
        SELECT
            gs.id AS governed_symbol_id,
            gs.catalog_symbol_id,
            sr.id AS symbol_revision_id,
            page.id AS published_page_id,
            entry.id AS pack_entry_id,
            pack.id AS publication_pack_id,
            gs.owner_organization_id,
            gs.visibility,
            gs.organization_wide
        FROM governed_symbols gs
        JOIN symbol_revisions sr
          ON sr.id = gs.current_revision_id
         AND sr.symbol_id = gs.id
        JOIN published_pages page
          ON page.current_symbol_revision_id = sr.id
        JOIN pack_entries entry
          ON entry.symbol_revision_id = sr.id
         AND entry.published_page_id = page.id
         AND entry.pack_id = page.pack_id
        JOIN publication_packs pack
          ON pack.id = page.pack_id
        WHERE gs.visibility = 'public'
          AND sr.lifecycle_state = 'published'
          AND pack.audience = 'public'
          AND pack.status = 'published'
        """
    )
    op.execute("GRANT SELECT ON active_public_symbol_projections TO symgov_app")

    op.drop_constraint("retirement_metadata", "pack_entries", type_="check")
    op.drop_constraint("publication_state", "pack_entries", type_="check")
    op.drop_column("pack_entries", "retirement_reason")
    op.drop_column("pack_entries", "retired_at")
    op.drop_column("pack_entries", "retired_by")
    op.drop_column("pack_entries", "publication_state")

    op.drop_constraint("retirement_metadata", "published_pages", type_="check")
    op.drop_constraint("publication_state", "published_pages", type_="check")
    op.drop_column("published_pages", "retirement_reason")
    op.drop_column("published_pages", "retired_at")
    op.drop_column("published_pages", "retired_by")
    op.drop_column("published_pages", "publication_state")

    op.drop_constraint("lifecycle_state", "symbol_revisions", type_="check")
    op.create_check_constraint(
        op.f("ck_symbol_revisions_ck_symbol_revisions_lifecycle_state"),
        "symbol_revisions",
        "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated')",
    )
