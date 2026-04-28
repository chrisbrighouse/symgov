from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "20260426_0004"
down_revision = "20260426_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "human_review_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_case_id", UUID(as_uuid=True), sa.ForeignKey("review_cases.id"), nullable=False),
        sa.Column("decision_code", sa.Text(), nullable=False),
        sa.Column("decision_summary", sa.Text(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decider_name", sa.Text(), nullable=False),
        sa.Column("decider_role", sa.Text(), nullable=False),
        sa.Column("from_stage", sa.Text(), nullable=False),
        sa.Column("to_stage", sa.Text(), nullable=True),
        sa.Column("decision_payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_human_review_decisions_case_created_at",
        "human_review_decisions",
        ["review_case_id", "created_at"],
    )

    op.create_table(
        "review_case_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("review_case_id", UUID(as_uuid=True), sa.ForeignKey("review_cases.id"), nullable=False),
        sa.Column("decision_id", UUID(as_uuid=True), sa.ForeignKey("human_review_decisions.id"), nullable=True),
        sa.Column("action_code", sa.Text(), nullable=False),
        sa.Column("action_status", sa.Text(), nullable=False),
        sa.Column("assigned_to", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("target_agent_slug", sa.Text(), nullable=True),
        sa.Column("target_stage", sa.Text(), nullable=True),
        sa.Column("action_payload_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_review_case_actions_case_status_created_at",
        "review_case_actions",
        ["review_case_id", "action_status", "created_at"],
    )
    op.create_index(
        "ix_review_case_actions_decision_created_at",
        "review_case_actions",
        ["decision_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_case_actions_decision_created_at", table_name="review_case_actions")
    op.drop_index("ix_review_case_actions_case_status_created_at", table_name="review_case_actions")
    op.drop_table("review_case_actions")
    op.drop_index("ix_human_review_decisions_case_created_at", table_name="human_review_decisions")
    op.drop_table("human_review_decisions")
