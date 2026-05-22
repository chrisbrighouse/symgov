"""Add Whitney market intelligence records.

Revision ID: 20260522_0011
Revises: 20260519_0010
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260522_0011"
down_revision = "20260519_0010"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "whitney_market_intelligence_reports",
        sa.Column("id", UUID, nullable=False),
        sa.Column("queue_item_id", UUID, nullable=True),
        sa.Column("report_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("signals_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("recommendations_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("evidence_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["queue_item_id"], ["agent_queue_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whitney_reports_queue_completed",
        "whitney_market_intelligence_reports",
        ["queue_item_id", "completed_at"],
    )

    op.create_table(
        "whitney_demand_signals",
        sa.Column("id", UUID, nullable=False),
        sa.Column("queue_item_id", UUID, nullable=True),
        sa.Column("report_id", UUID, nullable=True),
        sa.Column("symbol_id", UUID, nullable=True),
        sa.Column("published_page_id", UUID, nullable=True),
        sa.Column("signal_type", sa.Text(), nullable=False),
        sa.Column("market_segment", sa.Text(), nullable=True),
        sa.Column("discipline", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("demand_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("evidence_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["published_page_id"], ["published_pages.id"]),
        sa.ForeignKeyConstraint(["queue_item_id"], ["agent_queue_items.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["whitney_market_intelligence_reports.id"]),
        sa.ForeignKeyConstraint(["symbol_id"], ["governed_symbols.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_whitney_demand_signals_type_score_seen",
        "whitney_demand_signals",
        ["signal_type", "demand_score", "last_seen_at"],
    )
    op.create_index(
        "ix_whitney_demand_signals_segment_seen",
        "whitney_demand_signals",
        ["market_segment", "last_seen_at"],
    )
    op.create_index(
        "uq_whitney_demand_signals_source",
        "whitney_demand_signals",
        ["source_type", "source_ref", "signal_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_whitney_demand_signals_source", table_name="whitney_demand_signals")
    op.drop_index("ix_whitney_demand_signals_segment_seen", table_name="whitney_demand_signals")
    op.drop_index("ix_whitney_demand_signals_type_score_seen", table_name="whitney_demand_signals")
    op.drop_table("whitney_demand_signals")
    op.drop_index("ix_whitney_reports_queue_completed", table_name="whitney_market_intelligence_reports")
    op.drop_table("whitney_market_intelligence_reports")
