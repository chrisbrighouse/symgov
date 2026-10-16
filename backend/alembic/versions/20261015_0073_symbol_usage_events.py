"""symbol usage events and their daily rollups

Revision ID: 20261015_0073
Revises: 20261014_0072
Create Date: 2026-10-15 00:00:00.000000

`symbol_usage_events` records what is done with a symbol (viewed, downloaded,
added to a Set or the clipboard, fetched through the API, searched for), by
whom, and through which door, so an organization admin can see how their
people use the Catalog and the platform can see totals across organizations.
The CHECKs carry the privacy rules: an organization's rows always name that
organization, a personal row (including a personal API key's) never does, and
search text is kept for organization rows only.

`symbol_usage_daily_rollups` keeps the counts after the raw rows are purged,
with no user identity. Two partial unique indexes stand in for one unique
index over a nullable organization column.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261015_0073"
down_revision = "20261014_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "symbol_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", name="fk_symbol_usage_events_user_id_users")),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", name="fk_symbol_usage_events_organization_id_organizations"),
        ),
        sa.Column(
            "api_key_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catalog_api_keys.id", ondelete="SET NULL", name="fk_symbol_usage_events_api_key_id_catalog_api_keys"),
        ),
        sa.Column("key_kind", sa.Text()),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", name="fk_symbol_usage_events_project_id_projects")),
        sa.Column("symbol_set_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("symbol_sets.id", name="fk_symbol_usage_events_symbol_set_id_symbol_sets")),
        sa.Column(
            "governed_symbol_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", name="fk_symbol_usage_events_governed_symbol_id_governed_symbols"),
        ),
        sa.Column(
            "symbol_revision_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", name="fk_symbol_usage_events_symbol_revision_id_symbol_revisions"),
        ),
        sa.Column("pack_code", sa.Text()),
        sa.Column("format", sa.Text()),
        sa.Column("query_text", sa.Text()),
        sa.Column("result_count", sa.Integer()),
        sa.CheckConstraint(
            "event_type in ('symbol_viewed', 'symbol_downloaded', 'symbol_added_to_set', "
            "'symbol_added_to_clipboard', 'symbol_api_fetched', 'catalog_searched')",
            name="event_type",
        ),
        sa.CheckConstraint("channel in ('web', 'api')", name="channel"),
        sa.CheckConstraint("scope_kind in ('personal', 'organization')", name="scope_kind"),
        sa.CheckConstraint("(scope_kind = 'personal') = (organization_id is null)", name="scope_matches_organization"),
        sa.CheckConstraint("key_kind is null or key_kind in ('personal', 'organization')", name="key_kind"),
        sa.CheckConstraint("key_kind is distinct from 'personal' or organization_id is null", name="personal_key_has_no_organization"),
        sa.CheckConstraint("channel = 'api' or api_key_id is null", name="key_only_on_api_rows"),
        sa.CheckConstraint("query_text is null or (scope_kind = 'organization' and char_length(query_text) <= 200)", name="query_text"),
        sa.CheckConstraint("result_count is null or result_count >= 0", name="result_count"),
    )
    op.create_index("ix_symbol_usage_events_organization_occurred", "symbol_usage_events", ["organization_id", "occurred_at"])
    op.create_index("ix_symbol_usage_events_symbol_occurred", "symbol_usage_events", ["governed_symbol_id", "occurred_at"])
    op.create_index("ix_symbol_usage_events_occurred", "symbol_usage_events", ["occurred_at"])

    op.create_table(
        "symbol_usage_daily_rollups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("scope_kind", sa.Text(), nullable=False),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", name="fk_symbol_usage_daily_rollups_organization_id_organizations"),
        ),
        sa.Column("pack_code", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(scope_kind = 'personal') = (organization_id is null)", name="scope_matches_organization"),
        sa.CheckConstraint("event_count >= 0", name="event_count"),
    )
    op.create_index(
        "uq_symbol_usage_daily_rollups_organization_cell", "symbol_usage_daily_rollups",
        ["occurred_on", "event_type", "channel", "organization_id", "pack_code"],
        unique=True, postgresql_where=sa.text("organization_id is not null"),
    )
    op.create_index(
        "uq_symbol_usage_daily_rollups_personal_cell", "symbol_usage_daily_rollups",
        ["occurred_on", "event_type", "channel", "pack_code"],
        unique=True, postgresql_where=sa.text("organization_id is null"),
    )
    op.create_index("ix_symbol_usage_daily_rollups_organization_day", "symbol_usage_daily_rollups", ["organization_id", "occurred_on"])

    # The audit reader pages by time and by entity.
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("symbol_usage_daily_rollups")
    op.drop_table("symbol_usage_events")
