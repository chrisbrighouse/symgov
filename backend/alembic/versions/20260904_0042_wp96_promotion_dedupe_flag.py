"""add promotion_requests.possible_duplicate_governed_symbol_id
(Stage 9 WP9.6 anti-gaming: dedupe-before-review flag)

Revision ID: 20260904_0042
Revises: 20260904_0041
Create Date: 2026-09-04 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260904_0042"
down_revision = "20260904_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promotion_requests",
        sa.Column("possible_duplicate_governed_symbol_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_promotion_requests_possible_duplicate_governed_symbol_id",
        "promotion_requests",
        "governed_symbols",
        ["possible_duplicate_governed_symbol_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_promotion_requests_possible_duplicate_governed_symbol_id",
        "promotion_requests",
        ["possible_duplicate_governed_symbol_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_promotion_requests_possible_duplicate_governed_symbol_id", table_name="promotion_requests")
    op.drop_constraint(
        "fk_promotion_requests_possible_duplicate_governed_symbol_id", "promotion_requests", type_="foreignkey"
    )
    op.drop_column("promotion_requests", "possible_duplicate_governed_symbol_id")
