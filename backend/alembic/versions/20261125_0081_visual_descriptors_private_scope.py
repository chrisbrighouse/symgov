"""visual descriptors: an organization-private scope (D5)

Revision ID: 20261125_0081
Revises: 20261123_0080
Create Date: 2026-11-25 00:00:00.000000

Visual matching also ranks an organization's own organization-wide private
symbols for that organization's signed-in sessions (D3/D5, decided
2026-11-22; build decisions P1-P6 2026-11-25). Their descriptor rows live in
the same table, told apart by `scope` and the owning organization:

- `scope` may now be 'organization_private' as well as 'public'.
- `owner_organization_id` names the owning organization. It is set exactly
  when the scope is private (check `owner`), so a private row can never be
  read as public and a public row never belongs to an organization.
- Deleting the organization deletes its rows.

A row only ranks. Whether a symbol may be shown is still decided at query
time by the Catalog's own rules. Additive: existing rows are all public and
need no change. Downgrade deletes the private rows before restoring the
public-only check. Constraint names are bare (the naming convention adds the
prefix).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261125_0081"
down_revision = "20261123_0080"
branch_labels = None
depends_on = None

TABLE = "catalog_symbol_visual_descriptors"
OLD_INDEX = "ix_catalog_symbol_visual_descriptors_matcher"
NEW_INDEX = "ix_catalog_symbol_visual_descriptors_scope"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("owner_organization_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_catalog_symbol_visual_descriptors_owner", TABLE, "organizations",
        ["owner_organization_id"], ["id"], ondelete="CASCADE",
    )
    op.drop_constraint(op.f(f"ck_{TABLE}_scope"), TABLE, type_="check")
    op.create_check_constraint("scope", TABLE, "scope in ('public', 'organization_private')")
    op.create_check_constraint("owner", TABLE, "(scope = 'public') = (owner_organization_id IS NULL)")
    op.drop_index(OLD_INDEX, table_name=TABLE)
    op.create_index(NEW_INDEX, TABLE, ["matcher_version", "scope", "owner_organization_id"])


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE scope <> 'public'")
    op.drop_index(NEW_INDEX, table_name=TABLE)
    op.create_index(OLD_INDEX, TABLE, ["matcher_version", "scope"])
    op.drop_constraint(op.f(f"ck_{TABLE}_owner"), TABLE, type_="check")
    op.drop_constraint(op.f(f"ck_{TABLE}_scope"), TABLE, type_="check")
    op.create_check_constraint("scope", TABLE, "scope in ('public')")
    op.drop_constraint("fk_catalog_symbol_visual_descriptors_owner", TABLE, type_="foreignkey")
    op.drop_column(TABLE, "owner_organization_id")
