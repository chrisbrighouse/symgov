"""add integrator user role

Revision ID: 20260717_0021
Revises: 20260714_0020
Create Date: 2026-07-17 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260717_0021"
down_revision: Union[str, None] = "20260714_0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_user_roles_role", "user_roles", type_="check")
    op.create_check_constraint(
        "ck_user_roles_role",
        "user_roles",
        "role in ('admin', 'integrator', 'submitter', 'reviewer')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM user_roles WHERE role = 'integrator'")
    op.drop_constraint("ck_user_roles_role", "user_roles", type_="check")
    op.create_check_constraint(
        "ck_user_roles_role",
        "user_roles",
        "role in ('admin', 'submitter', 'reviewer')",
    )