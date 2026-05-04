from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "20260503_0005"
down_revision = "20260426_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_split_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_case_id", UUID(as_uuid=True), sa.ForeignKey("review_cases.id"), nullable=False),
        sa.Column("child_key", sa.Text(), nullable=False),
        sa.Column("proposed_symbol_id", sa.Text(), nullable=False),
        sa.Column("proposed_symbol_name", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("parent_file_name", sa.Text(), nullable=False),
        sa.Column("name_source", sa.Text(), nullable=True),
        sa.Column("attachment_object_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'awaiting_decision'")),
        sa.Column("latest_action", sa.Text(), nullable=True),
        sa.Column("latest_note", sa.Text(), nullable=True),
        sa.Column("latest_details", sa.Text(), nullable=True),
        sa.Column("latest_decision_id", UUID(as_uuid=True), sa.ForeignKey("human_review_decisions.id"), nullable=True),
        sa.Column("latest_action_id", UUID(as_uuid=True), sa.ForeignKey("review_case_actions.id"), nullable=True),
        sa.Column("downstream_agent_slug", sa.Text(), nullable=True),
        sa.Column("downstream_queue_item_id", sa.Text(), nullable=True),
        sa.Column("payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_review_split_items_case_child",
        "review_split_items",
        ["review_case_id", "child_key"],
        unique=True,
    )
    op.create_index(
        "ix_review_split_items_case_status",
        "review_split_items",
        ["review_case_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_split_items_case_status", table_name="review_split_items")
    op.drop_index("uq_review_split_items_case_child", table_name="review_split_items")
    op.drop_table("review_split_items")
