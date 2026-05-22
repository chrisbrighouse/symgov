"""Add Scott source discovery memory.

Revision ID: 20260516_0009
Revises: 20260515_0008
Create Date: 2026-05-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260516_0009"
down_revision = "20260515_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scott_source_discovery_sites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("process", sa.Text(), nullable=True),
        sa.Column("organization_type", sa.Text(), nullable=True),
        sa.Column("symbol_formats_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("relevance_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_session_queue_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["last_session_queue_item_id"], ["agent_queue_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_scott_source_discovery_sites_domain",
        "scott_source_discovery_sites",
        [sa.text("lower(domain)")],
        unique=True,
    )
    op.create_index(
        "ix_scott_source_discovery_sites_status_score_seen",
        "scott_source_discovery_sites",
        ["status", "relevance_score", "last_seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_scott_source_discovery_sites_status_score_seen", table_name="scott_source_discovery_sites")
    op.drop_index("uq_scott_source_discovery_sites_domain", table_name="scott_source_discovery_sites")
    op.drop_table("scott_source_discovery_sites")
