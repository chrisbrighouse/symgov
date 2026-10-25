"""user notifications: in-app notices for the private symbol workflow

Revision ID: 20261024_0078
Revises: 20261024_0077
Create Date: 2026-10-24 00:00:00.000000

WP6 of docs/plans/2026-10-24-unified-symbol-submission-review-implementation-plan.md
(spec draft 2 FR17, decision D9: in-app first, email later). One row per
recipient per event:

- a private symbol submitted for review -> the organization's Org Admins and
  Org Reviewers, except the submitter;
- a submission approved, rejected or sent back for changes -> its submitter;
- a submission withdrawn -> the organization's Org Admins and Org Reviewers.

There is no "review assigned" notice, because there is no assignment: the
organization shares one queue. Rows are written only for active members of the
owning organization, and every read re-checks that membership.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261024_0078"
down_revision = "20261024_0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "governed_symbol_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("governed_symbols.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organization_symbol_review_submissions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind in ('org_symbol.submitted', 'org_symbol.approved', 'org_symbol.rejected', "
            "'org_symbol.changes_requested', 'org_symbol.withdrawn')",
            name="ck_user_notifications_kind",
        ),
    )
    op.create_index(
        "ix_user_notifications_recipient",
        "user_notifications",
        ["user_id", "organization_id", "created_at"],
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON user_notifications TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON user_notifications FROM symgov_app")
    op.drop_index("ix_user_notifications_recipient", table_name="user_notifications")
    op.drop_table("user_notifications")
