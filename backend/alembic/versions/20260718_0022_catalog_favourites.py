"""add account-scoped Catalog favourites

Revision ID: 20260718_0022
Revises: 20260717_0021
Create Date: 2026-07-18 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260718_0022"
down_revision: Union[str, None] = "20260717_0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_favourites",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "symbol_id"),
    )


def downgrade() -> None:
    op.drop_table("catalog_favourites")