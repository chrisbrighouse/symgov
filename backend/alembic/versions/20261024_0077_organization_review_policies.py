"""organization review policy: self-approval of private symbols

Revision ID: 20261024_0077
Revises: 20261024_0076
Create Date: 2026-10-24 00:00:00.000000

WP3 of docs/plans/2026-10-24-unified-symbol-submission-review-implementation-plan.md
(spec draft 2 FR8). One row per organization that has changed its review policy;
an organization with no row has the defaults, so nothing is seeded.

- `allow_self_approval` (default true): a submitter may decide their own private
  submission. When false, the decider must be an Org Reviewer or Org Admin who
  is not the submitter. It never applies to public submissions or promotion.

A required-approvals count is deliberately not added yet (decision D5); this
table is where it would go.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261024_0077"
down_revision = "20261024_0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_review_policies",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("allow_self_approval", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON organization_review_policies TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON organization_review_policies FROM symgov_app")
    op.drop_table("organization_review_policies")
