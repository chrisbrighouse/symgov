"""add contribution_events ledger, organization_badges and
organization_contribution_totals (Stage 9 WP9.5)

Revision ID: 20260904_0041
Revises: 20260904_0040
Create Date: 2026-09-04 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260904_0041"
down_revision = "20260904_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contribution_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promotion_requests.id"), nullable=False),
        sa.Column("governed_symbol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_symbols.id"), nullable=True),
        sa.Column("symbol_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("symbol_revisions.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        # Deliberately not a foreign key -- see ContributionEvent's own
        # docstring: an enforced ON DELETE SET NULL here would collide with
        # this table's own UPDATE-blocking immutability trigger below.
        sa.Column("reversed_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type in ('contribution_awarded', 'contribution_reversed')",
            name="ck_contribution_events_event_type",
        ),
        sa.CheckConstraint(
            "(event_type = 'contribution_reversed') = (reason is not null)",
            name="ck_contribution_events_reason_only_on_reversal",
        ),
    )
    op.create_index("ix_contribution_events_organization_occurred", "contribution_events", ["organization_id", "occurred_at"])
    op.create_index("ix_contribution_events_occurred_at", "contribution_events", ["occurred_at"])
    op.create_index("ix_contribution_events_submission_id", "contribution_events", ["submission_id"])
    op.create_index(
        "ix_contribution_events_governed_symbol_occurred", "contribution_events", ["governed_symbol_id", "occurred_at"]
    )
    op.create_index("ix_contribution_events_reversed_event_id", "contribution_events", ["reversed_event_id"])

    # Immutable once inserted, mirroring product_usage_events' own
    # UPDATE-blocking (not UPDATE-or-DELETE-blocking) trigger: DELETE
    # remains permitted at the database level for this table's own
    # confirmed 90-day retention purge
    # (contribution_retention.purge_expired_contribution_events).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_contribution_events_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'contribution_events rows are immutable once inserted';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_contribution_events_update
        BEFORE UPDATE ON contribution_events
        FOR EACH ROW EXECUTE FUNCTION prevent_contribution_events_update();
        """
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON contribution_events TO symgov_app")

    op.create_table(
        "organization_badges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("badge_type", sa.Text(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contribution_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("organization_id", "badge_type", name="uq_organization_badges_org_badge"),
        sa.CheckConstraint(
            "badge_type in ('first_contribution', 'contributor_organization')",
            name="ck_organization_badges_badge_type",
        ),
    )
    # No UPDATE/DELETE grant: badges are written once via INSERT ... ON
    # CONFLICT DO NOTHING and never mutated by this package. (Not immutable
    # by trigger, unlike contribution_events -- a later anti-gaming package
    # may need to annotate/revoke a badge, and this table has no
    # self-referential-FK/trigger collision to avoid.)
    op.execute("GRANT SELECT, INSERT ON organization_badges TO symgov_app")

    op.create_table(
        "organization_contribution_totals",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("accepted_count >= 0", name="ck_organization_contribution_totals_accepted_non_negative"),
        sa.CheckConstraint("reversed_count >= 0", name="ck_organization_contribution_totals_reversed_non_negative"),
        sa.CheckConstraint("reversed_count <= accepted_count", name="ck_organization_contribution_totals_reversed_le_accepted"),
    )
    # UPDATE granted (unlike contribution_events' trigger-blocked
    # immutability): each accepted/reversed contribution re-touches its
    # organization's own single row via INSERT ... ON CONFLICT DO UPDATE.
    op.execute("GRANT SELECT, INSERT, UPDATE ON organization_contribution_totals TO symgov_app")


def downgrade() -> None:
    op.drop_table("organization_contribution_totals")
    op.drop_table("organization_badges")
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_contribution_events_update ON contribution_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_contribution_events_update()")
    op.drop_table("contribution_events")
