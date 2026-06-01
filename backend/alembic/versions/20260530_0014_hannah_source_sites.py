"""Add Hannah curation source sites.

Revision ID: 20260530_0014
Revises: 20260523_0013
Create Date: 2026-05-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260530_0014"
down_revision = "20260523_0013"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_hannah_curation_source_sites_status_score", table_name="hannah_curation_source_sites")
    op.drop_index("uq_hannah_curation_source_sites_domain", table_name="hannah_curation_source_sites")
    op.drop_table("hannah_curation_source_sites")
