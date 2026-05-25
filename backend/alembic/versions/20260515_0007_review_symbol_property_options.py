from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision = "20260515_0007"
down_revision = "20260512_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_symbol_property_options",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("display_value", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "field_name in ('category', 'discipline')",
            name="review_symbol_property_options_field_name",
        ),
    )
    op.create_index(
        "uq_review_symbol_property_options_field_key",
        "review_symbol_property_options",
        ["field_name", "normalized_key"],
        unique=True,
    )
    op.create_index(
        "ix_review_symbol_property_options_field_value",
        "review_symbol_property_options",
        ["field_name", "display_value"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_symbol_property_options_field_value", table_name="review_symbol_property_options")
    op.drop_index("uq_review_symbol_property_options_field_key", table_name="review_symbol_property_options")
    op.drop_table("review_symbol_property_options")
