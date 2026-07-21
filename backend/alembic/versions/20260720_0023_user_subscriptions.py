"""add user subscriptions and soft deletion

Revision ID: 20260720_0023
Revises: 20260718_0022
Create Date: 2026-07-20 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0023"
down_revision: Union[str, None] = "20260718_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
OWNER_EMAIL = "chris.brighouse@hotmail.co.uk"
SERVICE_EMAILS = ("ed@symgov.local", "symgov-publication-service@symgov.local")
DISABLED_SERVICE_PIN_HASH = "disabled-service-account"


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_deleted_display_name", "users", ["deleted_at", "display_name"])

    op.create_table(
        "user_subscriptions",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("started_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("anchor_day", sa.Integer(), nullable=False),
        sa.Column("is_protected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tier in ('free', 'plus')", name="ck_user_subscriptions_tier"),
        sa.CheckConstraint("(tier = 'free' and expires_on is null and is_protected = false) or (tier = 'plus' and ((is_protected = true and expires_on is null) or expires_on is not null))", name="ck_user_subscriptions_tier_expiry"),
        sa.CheckConstraint("expires_on is null or expires_on > started_on", name="ck_user_subscriptions_dates"),
        sa.CheckConstraint("anchor_day between 1 and 31", name="ck_user_subscriptions_anchor_day"),
    )
    op.create_index("ix_user_subscriptions_tier_expiry", "user_subscriptions", ["tier", "expires_on"])
    op.create_table(
        "subscription_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("previous_tier", sa.Text(), nullable=True),
        sa.Column("new_tier", sa.Text(), nullable=False),
        sa.Column("previous_expires_on", sa.Date(), nullable=True),
        sa.Column("new_expires_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("action in ('created', 'upgraded', 'adjusted', 'cancelled', 'expired', 'user_removed', 'owner_repaired')", name="ck_subscription_events_action"),
    )
    op.create_index("ix_subscription_events_user_created", "subscription_events", ["user_id", "created_at"])
    op.execute(sa.text("""
        INSERT INTO user_subscriptions (user_id, tier, started_on, expires_on, anchor_day, is_protected, version, created_at, updated_at)
        SELECT id, CASE WHEN lower(email) = :owner_email THEN 'plus' ELSE 'free' END,
               created_at::date, NULL, extract(day from created_at)::integer, lower(email) = :owner_email, 1, now(), now()
        FROM users
    """).bindparams(owner_email=OWNER_EMAIL))
    op.execute(sa.text("DELETE FROM user_roles WHERE user_id NOT IN (SELECT id FROM users WHERE lower(email) = :owner_email)").bindparams(owner_email=OWNER_EMAIL))
    op.execute(sa.text("""
        UPDATE users
        SET is_active = false,
            must_change_pin = false,
            pin_hash = :disabled_pin_hash,
            pin_set_at = now(),
            updated_at = now()
        WHERE lower(email) IN (:ed_email, :publication_email)
    """).bindparams(
        disabled_pin_hash=DISABLED_SERVICE_PIN_HASH,
        ed_email=SERVICE_EMAILS[0],
        publication_email=SERVICE_EMAILS[1],
    ))
    op.execute(sa.text("""
        INSERT INTO user_roles (user_id, role, created_at)
        SELECT id, 'admin', now() FROM users WHERE lower(email) = :owner_email
        ON CONFLICT DO NOTHING
    """).bindparams(owner_email=OWNER_EMAIL))
    op.execute(sa.text("UPDATE users SET is_active = true, deleted_at = null WHERE lower(email) = :owner_email").bindparams(owner_email=OWNER_EMAIL))


def downgrade() -> None:
    raise RuntimeError(
        "20260720_0023 is intentionally irreversible because the approved cutover permanently removes non-owner roles."
    )
