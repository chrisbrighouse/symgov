"""catalog category additions: categories Public Reviewers add to the picklist

Revision ID: 20261025_0079
Revises: 20261024_0078
Create Date: 2026-10-25 00:00:00.000000

The Catalog's built-in categories live in code (`catalog_taxonomy.py`). A
Public Reviewer may add one while reviewing, so the public Catalog can grow
without a release; each addition is a row here, and the picklist is the
built-in list plus the active rows (Chris's decisions, 2026-10-25).

- `normalized_key` is unique across every row, retired ones included, so a
  name a Platform Admin retired cannot come back unseen.
- Retiring stops a name being offered; symbols that carry it keep it.
- Nothing is seeded. Values reviewers typed before the picklist stay in
  `review_symbol_property_options`, where Platform Admin reviews them.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261025_0079"
down_revision = "20261024_0078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_category_additions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.Text(), nullable=False),
        sa.Column(
            "added_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "review_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retired_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("char_length(name) between 2 and 80", name="name_length"),
    )
    op.create_index(
        "uq_catalog_category_additions_key",
        "catalog_category_additions",
        ["normalized_key"],
        unique=True,
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON catalog_category_additions TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON catalog_category_additions FROM symgov_app")
    op.drop_index("uq_catalog_category_additions_key", table_name="catalog_category_additions")
    op.drop_table("catalog_category_additions")
