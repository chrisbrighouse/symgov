"""add product_usage_daily_rollups aggregate table (Stage 9 WP9.4)

Revision ID: 20260904_0040
Revises: 20260904_0039
Create Date: 2026-09-04 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260904_0040"
down_revision = "20260904_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_usage_daily_rollups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("distinct_user_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "event_type", "occurred_on", name="uq_product_usage_daily_rollups_cell"),
        sa.CheckConstraint(
            "event_type in ("
            "'personal_session_started', 'organization_selected', 'context_resolved', "
            "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed', "
            "'organization_review_submitted', 'organization_review_decided', 'organization_wide_changed', "
            "'publication_submitted', 'publication_decided', 'public_symbol_demoted', "
            "'project_created', 'project_updated', 'project_archived', 'project_selected', "
            "'set_created', 'set_updated', 'set_archived', 'set_project_availability_changed', "
            "'organization_role_changed', 'platform_admin_assigned', 'platform_admin_removed', "
            "'organization_icon_uploaded', 'organization_icon_removed'"
            ")",
            name="ck_product_usage_daily_rollups_event_type",
        ),
        sa.CheckConstraint("event_count >= 0", name="ck_product_usage_daily_rollups_event_count_non_negative"),
        sa.CheckConstraint(
            "distinct_user_count >= 0", name="ck_product_usage_daily_rollups_distinct_user_count_non_negative"
        ),
        sa.CheckConstraint(
            "distinct_user_count <= event_count", name="ck_product_usage_daily_rollups_distinct_le_event_count"
        ),
    )

    op.create_index(
        "ix_product_usage_daily_rollups_org_occurred", "product_usage_daily_rollups", ["organization_id", "occurred_on"]
    )

    # No DELETE grant -- unlike the raw `product_usage_events` retention
    # purge, rollup rows are kept indefinitely (Stage 9 plan §4 Q7); only
    # `refresh_product_usage_rollups`'s own INSERT ... ON CONFLICT DO UPDATE
    # ever touches existing rows, so UPDATE is granted (unlike the raw
    # table's own trigger-blocked immutability -- a rollup cell must be
    # re-computable in place as later raw events land on the same day).
    op.execute("GRANT SELECT, INSERT, UPDATE ON product_usage_daily_rollups TO symgov_app")


def downgrade() -> None:
    op.drop_table("product_usage_daily_rollups")
