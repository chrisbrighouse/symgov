from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision = "20260512_0006"
down_revision = "20260503_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_symbol_properties",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_case_id", UUID(as_uuid=True), sa.ForeignKey("review_cases.id"), nullable=False),
        sa.Column("review_split_item_id", UUID(as_uuid=True), sa.ForeignKey("review_split_items.id"), nullable=True),
        sa.Column("symbol_record_key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("discipline", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'agent_initial'")),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_review_symbol_properties_case_key",
        "review_symbol_properties",
        ["review_case_id", "symbol_record_key"],
        unique=True,
    )
    op.create_index(
        "ix_review_symbol_properties_split_item",
        "review_symbol_properties",
        ["review_split_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_symbol_properties_split_item", table_name="review_symbol_properties")
    op.drop_index("uq_review_symbol_properties_case_key", table_name="review_symbol_properties")
    op.drop_table("review_symbol_properties")
