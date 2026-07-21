"""add profile subscription audit origin and email outbox

Revision ID: 20260721_0024
Revises: 20260720_0023
Create Date: 2026-07-21 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0024"
down_revision: Union[str, None] = "20260720_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_events",
        sa.Column("origin", sa.Text(), nullable=False, server_default=sa.text("'system'")),
    )
    op.create_check_constraint(
        "ck_subscription_events_origin",
        "subscription_events",
        "origin in ('admin', 'self_service', 'system', 'expiry')",
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "subscription_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("subscription_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient_kind", sa.Text(), nullable=False),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('pending', 'sent')", name="ck_email_outbox_status"),
        sa.CheckConstraint(
            "recipient_kind in ('customer', 'admin')", name="ck_email_outbox_recipient_kind"
        ),
    )
    op.create_index(
        "uq_email_outbox_event_recipient",
        "email_outbox",
        ["subscription_event_id", "recipient_kind"],
        unique=True,
    )
    op.create_index(
        "ix_email_outbox_pending",
        "email_outbox",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_outbox_pending", table_name="email_outbox")
    op.drop_index("uq_email_outbox_event_recipient", table_name="email_outbox")
    op.drop_table("email_outbox")
    op.drop_constraint("ck_subscription_events_origin", "subscription_events", type_="check")
    op.drop_column("subscription_events", "origin")