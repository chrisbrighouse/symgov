"""Add Scott source include-next-run flag.

Revision ID: 20260523_0013
Revises: 20260523_0012
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260523_0013"
down_revision = "20260523_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scott_source_discovery_sites",
        sa.Column("include_next_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("scott_source_discovery_sites", "include_next_run")
