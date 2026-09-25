"""add account-scoped Catalog workbench state

Revision ID: 20260925_0063
Revises: 20260925_0062
Create Date: 2026-09-25 00:00:00.000000

Catalog preferences, saved views and the Catalog clipboard were kept in the
browser's localStorage, so every account signed in on one browser shared them.
This stores them against the account instead.

Preferences and saved views follow the account everywhere, one row per user,
in the same shape `catalog_favourites` uses for favourites. The clipboard is
scoped to the session: one per user for the personal session
(`organization_id` null) and one per user per organization, so an
organization's private symbols never appear in a clipboard outside it. Two
partial unique indexes enforce one clipboard per scope, since a null
`organization_id` would otherwise never collide.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260925_0063"
down_revision: Union[str, None] = "20260925_0062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_workbench_states",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("preferences_json", postgresql.JSONB(), nullable=False),
        sa.Column("saved_views_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "catalog_workbench_clipboards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("items_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_catalog_workbench_clipboards_personal",
        "catalog_workbench_clipboards",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("organization_id is null"),
    )
    op.create_index(
        "uq_catalog_workbench_clipboards_organization",
        "catalog_workbench_clipboards",
        ["user_id", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("uq_catalog_workbench_clipboards_organization", table_name="catalog_workbench_clipboards")
    op.drop_index("uq_catalog_workbench_clipboards_personal", table_name="catalog_workbench_clipboards")
    op.drop_table("catalog_workbench_clipboards")
    op.drop_table("catalog_workbench_states")
