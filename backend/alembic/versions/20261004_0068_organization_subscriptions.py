"""add fixed-seat organization subscriptions

Revision ID: 20261004_0068
Revises: 20260930_0067
Create Date: 2026-10-04 00:00:00.000000

Every existing non-protected organization is seeded with the default plan (25
seats, 12 months from the migration date, the values in
organization_subscriptions.DEFAULT_*). The protected symgov organization gets
no row and stays unmetered. Seat limits are enforced in organization_service
(add_organization_member, reactivate_membership). Constraint names
are passed bare: the naming convention adds the ck_<table>_ prefix, and two of
the longer names would otherwise be truncated at PostgreSQL's 63 characters.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0068"
down_revision: Union[str, None] = "20260930_0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_subscriptions",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("seat_limit", sa.Integer(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("seat_limit >= 1", name="seat_limit"),
        sa.CheckConstraint("expires_on > started_on", name="dates"),
    )
    op.create_index("ix_organization_subscriptions_expires_on", "organization_subscriptions", ["expires_on"])
    op.create_table(
        "organization_subscription_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("previous_seat_limit", sa.Integer(), nullable=True),
        sa.Column("new_seat_limit", sa.Integer(), nullable=False),
        sa.Column("previous_expires_on", sa.Date(), nullable=True),
        sa.Column("new_expires_on", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action in ('created', 'updated')", name="action"),
    )
    op.create_index(
        "ix_org_subscription_events_org_created",
        "organization_subscription_events",
        ["organization_id", "created_at"],
    )


    op.execute(
        sa.text(
            "INSERT INTO organization_subscriptions"
            " (organization_id, seat_limit, started_on, expires_on, version, created_at, updated_at)"
            " SELECT id, 25, current_date, (current_date + interval '12 months')::date, 1, now(), now()"
            " FROM organizations WHERE is_protected = false"
            " ON CONFLICT (organization_id) DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO organization_subscription_events"
            " (id, organization_id, actor_id, action, previous_seat_limit, new_seat_limit,"
            "  previous_expires_on, new_expires_on, reason, created_at)"
            " SELECT gen_random_uuid(), organization_id, NULL, 'created', NULL, seat_limit,"
            "  NULL, expires_on, 'Default plan seeded by migration 20261004_0068.', now()"
            " FROM organization_subscriptions"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_org_subscription_events_org_created", table_name="organization_subscription_events")
    op.drop_table("organization_subscription_events")
    op.drop_index("ix_organization_subscriptions_expires_on", table_name="organization_subscriptions")
    op.drop_table("organization_subscriptions")
