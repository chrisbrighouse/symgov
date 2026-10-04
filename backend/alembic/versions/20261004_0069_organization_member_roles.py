"""add organization-scoped member roles

Revision ID: 20261004_0069
Revises: 20261004_0068
Create Date: 2026-10-04 00:00:00.000000

A role held within one organization, effective only in that organization's
session and only while its subscription is active. Nothing is seeded, so no
existing user's access changes. Constraint names are passed bare for the
naming convention to prefix.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20261004_0069"
down_revision: Union[str, None] = "20261004_0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_member_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "membership_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "assigned_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assign_reason", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("role in ('integrator', 'submitter', 'reviewer')", name="role"),
        sa.CheckConstraint(
            "(is_active = true and revoked_at is null) or (is_active = false and revoked_at is not null)",
            name="active_revoked",
        ),
    )
    op.create_index(
        "uq_org_member_role_active",
        "organization_member_roles",
        ["membership_id", "role"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index(
        "ix_org_member_roles_membership_active",
        "organization_member_roles",
        ["membership_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_org_member_roles_membership_active", table_name="organization_member_roles")
    op.drop_index("uq_org_member_role_active", table_name="organization_member_roles")
    op.drop_table("organization_member_roles")
