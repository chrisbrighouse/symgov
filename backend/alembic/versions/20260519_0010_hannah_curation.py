"""Add Hannah curation state and photo candidates.

Revision ID: 20260519_0010
Revises: 20260516_0009
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260519_0010"
down_revision = "20260516_0009"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("uq_hannah_photo_candidates_image_symbol", table_name="hannah_photo_candidates")
    op.drop_index("ix_hannah_photo_candidates_last_seen", table_name="hannah_photo_candidates")
    op.drop_index("ix_hannah_photo_candidates_symbol_status_score", table_name="hannah_photo_candidates")
    op.drop_table("hannah_photo_candidates")
    op.drop_index("ix_hannah_symbol_curation_states_status_attempt", table_name="hannah_symbol_curation_states")
    op.drop_index("uq_hannah_symbol_curation_states_symbol", table_name="hannah_symbol_curation_states")
    op.drop_table("hannah_symbol_curation_states")
