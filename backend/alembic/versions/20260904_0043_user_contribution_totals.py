"""add user_contribution_totals (Stage 9 WP9.8)

Revision ID: 20260904_0043
Revises: 20260904_0042
Create Date: 2026-09-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260904_0043"
down_revision = "20260904_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_contribution_totals",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("accepted_count >= 0", name="ck_user_contribution_totals_accepted_non_negative"),
        sa.CheckConstraint("reversed_count >= 0", name="ck_user_contribution_totals_reversed_non_negative"),
        sa.CheckConstraint("reversed_count <= accepted_count", name="ck_user_contribution_totals_reversed_le_accepted"),
    )
    # UPDATE granted, mirroring organization_contribution_totals: each
    # accepted/reversed contribution re-touches its user's own single row via
    # INSERT ... ON CONFLICT DO UPDATE. No DELETE grant -- like the
    # organization-level counter, this table is kept indefinitely and is
    # never purged, even after the raw contribution_events ledger row that
    # produced it ages out at 90 days.
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_contribution_totals TO symgov_app")


def downgrade() -> None:
    op.drop_table("user_contribution_totals")
