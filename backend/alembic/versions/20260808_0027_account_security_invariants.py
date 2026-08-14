"""add F0.5 account security session, throttle, and audit storage

Revision ID: 20260808_0027
Revises: 20260802_0026
Create Date: 2026-08-08 21:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260808_0027"
down_revision = "20260802_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_sessions",
        sa.Column("purpose", sa.Text(), server_default=sa.text("'application'"), nullable=False),
    )
    # Existing unrevoked sessions for users awaiting a mandatory PIN change remain
    # usable only for the credential-change allowlist after this upgrade.
    op.execute(
        """
        UPDATE user_sessions AS sessions
        SET purpose = 'credential_change'
        FROM users
        WHERE sessions.auth_user_id = users.id
          AND sessions.revoked_at IS NULL
          AND users.must_change_pin = true
        """
    )
    op.create_check_constraint(
        "ck_user_sessions_purpose",
        "user_sessions",
        "purpose in ('application', 'credential_change')",
    )

    op.create_table(
        "auth_login_throttle_buckets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("bucket_key_hash", sa.Text(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope in ('account', 'ip')", name="ck_scope"),
        sa.CheckConstraint("failure_count >= 0", name="ck_failure_count"),
    )
    op.create_index(
        "uq_auth_login_throttle_scope_key",
        "auth_login_throttle_buckets",
        ["scope", "bucket_key_hash"],
        unique=True,
    )
    op.create_index(
        "ix_auth_login_throttle_blocked_until",
        "auth_login_throttle_buckets",
        ["blocked_until"],
    )

    op.create_table(
        "auth_login_attempt_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("email_key_hash", sa.Text(), nullable=False),
        sa.Column(
            "resolved_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_ip_hash", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("request_metadata_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "outcome in ('success', 'failure', 'throttled')",
            name="ck_outcome",
        ),
        sa.CheckConstraint(
            "failure_reason is null or failure_reason in "
            "('invalid_credentials', 'inactive_or_deleted', 'throttled_account', 'throttled_ip')",
            name="ck_failure_reason",
        ),
    )
    op.create_index("ix_auth_login_attempt_occurred", "auth_login_attempt_events", ["occurred_at"])
    op.create_index(
        "ix_auth_login_attempt_user_occurred",
        "auth_login_attempt_events",
        ["resolved_user_id", "occurred_at"],
    )
    op.create_index(
        "ix_auth_login_attempt_email_occurred",
        "auth_login_attempt_events",
        ["email_key_hash", "occurred_at"],
    )

    op.create_table(
        "auth_throttle_recovery_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("target_key_hash", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("cleared_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope in ('account', 'ip')", name="ck_scope"),
    )
    op.create_index(
        "ix_auth_throttle_recovery_created",
        "auth_throttle_recovery_events",
        ["created_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_auth_security_event_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'authentication security event tables are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("auth_login_attempt_events", "auth_throttle_recovery_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION prevent_auth_security_event_mutation();
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_prevent_truncate
            BEFORE TRUNCATE ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_auth_security_event_mutation();
            """
        )


def downgrade() -> None:
    for table in ("auth_throttle_recovery_events", "auth_login_attempt_events"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_prevent_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS prevent_auth_security_event_mutation()")
    op.drop_table("auth_throttle_recovery_events")
    op.drop_table("auth_login_attempt_events")
    op.drop_table("auth_login_throttle_buckets")
    op.drop_constraint("ck_user_sessions_purpose", "user_sessions", type_="check")
    op.drop_column("user_sessions", "purpose")
