from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260515_0008"
down_revision = "20260515_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("review_symbol_properties", sa.Column("format", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_symbol_properties", "format")
