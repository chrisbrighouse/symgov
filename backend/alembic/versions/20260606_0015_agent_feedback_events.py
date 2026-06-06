"""Add observational agent feedback events.

Revision ID: 20260606_0015
Revises: 20260530_0014
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260606_0015"
down_revision = "20260530_0014"
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "agent_feedback_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("agent_slug", sa.Text(), nullable=False),
        sa.Column("feedback_type", sa.Text(), nullable=False),
        sa.Column("source_entity_type", sa.Text(), nullable=False),
        sa.Column("source_entity_id", UUID, nullable=False),
        sa.Column("original_value_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("corrected_value_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reviewer_name", sa.Text(), nullable=True),
        sa.Column("reviewer_role", sa.Text(), nullable=True),
        sa.Column("evidence_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("applied_to_rules_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_to_prompt_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_feedback_events_agent_created", "agent_feedback_events", ["agent_slug", "created_at"])
    op.create_index("ix_agent_feedback_events_source", "agent_feedback_events", ["source_entity_type", "source_entity_id", "created_at"])
    op.create_index("ix_agent_feedback_events_type_created", "agent_feedback_events", ["feedback_type", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_feedback_events_type_created", table_name="agent_feedback_events")
    op.drop_index("ix_agent_feedback_events_source", table_name="agent_feedback_events")
    op.drop_index("ix_agent_feedback_events_agent_created", table_name="agent_feedback_events")
    op.drop_table("agent_feedback_events")
