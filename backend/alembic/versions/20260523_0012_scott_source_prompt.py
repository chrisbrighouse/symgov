"""Add Scott source access prompts.

Revision ID: 20260523_0012
Revises: 20260522_0011
Create Date: 2026-05-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260523_0012"
down_revision = "20260522_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scott_source_discovery_sites", sa.Column("source_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("scott_source_discovery_sites", "source_prompt")
