"""extend product_usage_events event_type with governance-lifecycle types (Stage 9 WP9.2)

Revision ID: 20260904_0039
Revises: 20260904_0038
Create Date: 2026-09-04 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260904_0039"
down_revision = "20260904_0038"
branch_labels = None
depends_on = None

NEW_EVENT_TYPE_CHECK = (
    "event_type in ("
    "'personal_session_started', 'organization_selected', 'context_resolved', "
    "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed', "
    "'organization_review_submitted', 'organization_review_decided', 'organization_wide_changed', "
    "'publication_submitted', 'publication_decided', 'public_symbol_demoted', "
    "'project_created', 'project_updated', 'project_archived', 'project_selected', "
    "'set_created', 'set_updated', 'set_archived', 'set_project_availability_changed', "
    "'organization_role_changed', 'platform_admin_assigned', 'platform_admin_removed', "
    "'organization_icon_uploaded', 'organization_icon_removed'"
    ")"
)

OLD_EVENT_TYPE_CHECK = (
    "event_type in ("
    "'personal_session_started', 'organization_selected', 'context_resolved', "
    "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed'"
    ")"
)


def upgrade() -> None:
    op.drop_constraint("ck_product_usage_events_event_type", "product_usage_events", type_="check")
    op.create_check_constraint("ck_product_usage_events_event_type", "product_usage_events", NEW_EVENT_TYPE_CHECK)


def downgrade() -> None:
    # Narrowing this CheckConstraint back down is only safe if no row
    # already carries one of the governance-lifecycle event types this
    # migration added. Rather than let that constraint-creation fail with an
    # opaque violation (or silently keep the wider constraint forever), the
    # rows this narrower vocabulary cannot represent are deleted first --
    # this mirrors WP9.1's own 90-day retention purge in spirit (bounded,
    # intentional deletion of raw usage-event rows is already an accepted
    # operation on this specific table, unlike on any audit/governance
    # table), and matches the common case a downgrade this far back is
    # actually used for: discarding this stage's schema entirely, not
    # preserving its data.
    op.execute(
        sa.text(
            "DELETE FROM product_usage_events WHERE event_type NOT IN ("
            "'personal_session_started', 'organization_selected', 'context_resolved', "
            "'set_selected', 'symbol_previewed', 'symbol_downloaded', 'favorite_changed'"
            ")"
        )
    )
    op.drop_constraint("ck_product_usage_events_event_type", "product_usage_events", type_="check")
    op.create_check_constraint("ck_product_usage_events_event_type", "product_usage_events", OLD_EVENT_TYPE_CHECK)
