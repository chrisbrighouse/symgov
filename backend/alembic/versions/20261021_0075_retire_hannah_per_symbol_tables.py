"""retire Hannah's per-symbol photo tables

Revision ID: 20261021_0075
Revises: 20261020_0074
Create Date: 2026-10-21 00:00:00.000000

WP-H7 of docs/plans/2026-10-19-hannah-concept-photo-curation-plan.md. Hannah
now curates photos per semantic concept (`20261020_0074`), and the per-symbol
path is gone from the code, so its three tables go too:

- `hannah_photo_candidates` and `hannah_symbol_curation_states` were emptied
  by `purge-hannah-photos` in production on 2026-10-19;
- `hannah_curation_source_sites` was never read by any code. Production held
  two seed rows from 2026-05-30 (`commons.wikimedia.org` and `duckduckgo.com`,
  both `active`, never searched), which are not kept.

No other table references any of them. The downgrade rebuilds all three as
`20260519_0010` and `20260530_0014` created them, empty.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261021_0075"
down_revision = "20261020_0074"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.drop_table("hannah_curation_source_sites")
    op.drop_table("hannah_photo_candidates")
    op.drop_table("hannah_symbol_curation_states")


def downgrade() -> None:
    op.create_table(
        "hannah_symbol_curation_states",
        sa.Column("id", UUID, nullable=False),
        sa.Column("symbol_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("photo_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["governed_symbols.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_hannah_symbol_curation_states_symbol",
        "hannah_symbol_curation_states",
        ["symbol_id"],
        unique=True,
    )
    op.create_index(
        "ix_hannah_symbol_curation_states_status_attempt",
        "hannah_symbol_curation_states",
        ["status", "last_attempt_at"],
    )

    op.create_table(
        "hannah_photo_candidates",
        sa.Column("id", UUID, nullable=False),
        sa.Column("symbol_id", UUID, nullable=False),
        sa.Column("symbol_revision_id", UUID, nullable=True),
        sa.Column("published_page_id", UUID, nullable=True),
        sa.Column("queue_item_id", UUID, nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rights_status", sa.Text(), nullable=False),
        sa.Column("license_label", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("relevance_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("attachment_id", UUID, nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("evidence_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"]),
        sa.ForeignKeyConstraint(["published_page_id"], ["published_pages.id"]),
        sa.ForeignKeyConstraint(["queue_item_id"], ["agent_queue_items.id"]),
        sa.ForeignKeyConstraint(["symbol_id"], ["governed_symbols.id"]),
        sa.ForeignKeyConstraint(["symbol_revision_id"], ["symbol_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hannah_photo_candidates_symbol_status_score",
        "hannah_photo_candidates",
        ["symbol_id", "status", "relevance_score"],
    )
    op.create_index("ix_hannah_photo_candidates_last_seen", "hannah_photo_candidates", ["last_seen_at"])
    op.create_index(
        "uq_hannah_photo_candidates_image_symbol",
        "hannah_photo_candidates",
        ["symbol_id", "image_url"],
        unique=True,
    )

    op.create_table(
        "hannah_curation_source_sites",
        sa.Column("id", UUID, nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("search_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("usefulness_score", sa.Numeric(3, 2), nullable=False, server_default=sa.text("1.00")),
        sa.Column("reliability_score", sa.Numeric(3, 2), nullable=False, server_default=sa.text("1.00")),
        sa.Column("feedback_notes", sa.Text(), nullable=True),
        sa.Column("config_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("last_search_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_hannah_curation_source_sites_domain",
        "hannah_curation_source_sites",
        [sa.text("lower(domain)")],
        unique=True,
    )
    op.create_index(
        "ix_hannah_curation_source_sites_status_score",
        "hannah_curation_source_sites",
        ["status", "usefulness_score"],
    )
