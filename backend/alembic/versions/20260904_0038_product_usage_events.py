"""add product_usage_events ledger table (Stage 9 WP9.1)

Revision ID: 20260904_0038
Revises: 20260902_0037
Create Date: 2026-09-04 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260904_0038"
down_revision = "20260902_0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_mode", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("symbol_set_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("symbol_sets.id"), nullable=True),
        sa.Column("governed_symbol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_symbols.id"), nullable=True),
        sa.Column("symbol_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("symbol_revisions.id"), nullable=True),
        sa.Column("symbol_source", sa.Text(), nullable=True),
        sa.Column("format", sa.Text(), nullable=True),
        sa.Column("favourite_action", sa.Text(), nullable=True),
        sa.Column("context_resolution_basis", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "event_type in ("
            "'personal_session_started', 'organization_selected', 'context_resolved', "
            "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed'"
            ")",
            name="ck_product_usage_events_event_type",
        ),
        sa.CheckConstraint("session_mode in ('personal', 'organization')", name="ck_product_usage_events_session_mode"),
        sa.CheckConstraint(
            "(session_mode = 'personal' and organization_id is null) or (session_mode = 'organization' and organization_id is not null)",
            name="ck_product_usage_events_session_mode_organization",
        ),
        sa.CheckConstraint(
            "symbol_source is null or symbol_source in ('public', 'organization_private')",
            name="ck_product_usage_events_symbol_source",
        ),
        sa.CheckConstraint(
            "favourite_action is null or favourite_action in ('added', 'removed')",
            name="ck_product_usage_events_favourite_action",
        ),
        sa.CheckConstraint(
            "context_resolution_basis is null or context_resolution_basis in "
            "('explicit', 'user_preference', 'project_default', 'organization_default', 'none')",
            name="ck_product_usage_events_context_resolution_basis",
        ),
        sa.CheckConstraint(
            "(event_type = 'symbol_downloaded') = (format is not null)",
            name="ck_product_usage_events_format_only_on_download",
        ),
        sa.CheckConstraint(
            "(event_type = 'favorite_changed') = (favourite_action is not null)",
            name="ck_product_usage_events_favourite_action_only_on_favorite_changed",
        ),
        sa.CheckConstraint(
            "(event_type in ('context_resolved', 'set_selected')) = (context_resolution_basis is not null)",
            name="ck_product_usage_events_context_basis_only_on_context_events",
        ),
    )

    op.create_index("ix_product_usage_events_occurred_at", "product_usage_events", ["occurred_at"])
    op.create_index(
        "ix_product_usage_events_org_event_occurred", "product_usage_events", ["organization_id", "event_type", "occurred_at"]
    )
    op.create_index("ix_product_usage_events_event_occurred", "product_usage_events", ["event_type", "occurred_at"])
    op.create_index("ix_product_usage_events_user_occurred", "product_usage_events", ["user_id", "occurred_at"])
    op.create_index(
        "ix_product_usage_events_governed_symbol_occurred", "product_usage_events", ["governed_symbol_id", "occurred_at"]
    )

    # Immutable once inserted -- corrections are not supported for a
    # product-usage row (there is nothing analogous to LLMUsageEvent's own
    # cost/error-provenance fields that would ever need in-place repair).
    # Unlike LLMUsageEvent's own trigger, DELETE remains permitted at the
    # database level: the Stage 9 plan's confirmed 90-day raw-row retention
    # (product_usage_retention.purge_expired_product_usage_events) depends
    # on being able to delete expired rows.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_product_usage_events_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'product_usage_events rows are immutable once inserted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_product_usage_events_update
        BEFORE UPDATE ON product_usage_events
        FOR EACH ROW EXECUTE FUNCTION prevent_product_usage_events_update();
        """
    )

    # No UPDATE grant -- the trigger above blocks it at the row level
    # regardless, but the app role should not even attempt one.
    op.execute("GRANT SELECT, INSERT, DELETE ON product_usage_events TO symgov_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_product_usage_events_update ON product_usage_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_product_usage_events_update()")
    op.drop_table("product_usage_events")
