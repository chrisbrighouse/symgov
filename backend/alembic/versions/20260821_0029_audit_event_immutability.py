"""protect audit events as append-only history

Revision ID: 20260821_0029
Revises: 20260810_0028
Create Date: 2026-08-21 19:30:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260821_0029"
down_revision = "20260810_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION protect_audit_events_append_only()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit events are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION protect_audit_events_append_only();
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only_truncate
        BEFORE TRUNCATE ON audit_events
        FOR EACH STATEMENT EXECUTE FUNCTION protect_audit_events_append_only();
        """
    )
    op.execute("GRANT SELECT, INSERT ON audit_events TO symgov_app")
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM symgov_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_events_append_only_truncate ON audit_events"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS protect_audit_events_append_only()")
    op.execute("REVOKE SELECT, INSERT ON audit_events FROM symgov_app")
