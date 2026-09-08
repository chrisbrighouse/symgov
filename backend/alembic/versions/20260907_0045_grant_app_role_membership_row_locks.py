"""grant the app role the row-lock privilege its authorization path needs

Revision ID: 20260907_0045
Revises: 20260905_0044

20260810_0028 made the membership and role-assignment history tables
append-only for `symgov_app` (`GRANT SELECT, INSERT` then
`REVOKE UPDATE, DELETE, TRUNCATE`). But `require_stage4_principal`
(`stage4_authorization.py`) resolves every organization-scoped request by
taking `SELECT ... FOR SHARE` on `users`, `organizations`,
`organization_memberships` and `organization_role_assignments`, and
PostgreSQL requires UPDATE (or DELETE) privilege for *any* row-locking
clause -- `FOR SHARE` included. `users` and `organizations` already carry
UPDATE; the two history tables did not.

The result in production (2026-09-07) was that every organization-scoped
operation -- creating a project, a symbol set, an organization-private
symbol, or an organization via POST /platform/organizations -- failed
with `psycopg.errors.InsufficientPrivilege: permission denied for table
organization_memberships`, while unauthenticated login still worked. No
test caught it because every test connects as a single superuser for
both the app and migration URLs, so the two-role privilege model these
migrations construct was never exercised. See
`tests/test_two_role_privilege_model_postgresql.py`, added alongside
this revision.

DELETE and TRUNCATE remain revoked, so history still cannot be erased by
the application; this grants the minimum the row locks require. The
longer-term alternative -- dropping the share locks from the
authorization read path, which are arguably redundant against tables the
application cannot UPDATE anyway -- is recorded in
`docs/plans/2026-09-07-stage11-hermes-deployment-task.md` and would
supersede this grant.
"""
from alembic import op

revision = "20260907_0045"
down_revision = "20260905_0044"
branch_labels = None
depends_on = None

_ROW_LOCK_TABLES = """
    organization_memberships,
    organization_role_assignments,
    organization_member_capabilities,
    platform_role_assignments
"""


def upgrade() -> None:
    op.execute(f"GRANT UPDATE ON {_ROW_LOCK_TABLES} TO symgov_app")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE ON {_ROW_LOCK_TABLES} FROM symgov_app")
