"""user auth roles

Revision ID: 20260624_0017
Revises: 20260619_0016
Create Date: 2026-06-24 00:00:00.000000

"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260624_0017"
down_revision: Union[str, None] = "20260619_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 260_000


def default_pin_hash() -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", b"4590", salt, PIN_HASH_ITERATIONS)
    return "$".join(
        [
            PIN_HASH_ALGORITHM,
            str(PIN_HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def upgrade() -> None:
    op.add_column("users", sa.Column("pin_hash", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("pin_set_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("must_change_pin", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE users
            SET pin_hash = :pin_hash,
                pin_set_at = created_at,
                updated_at = created_at,
                must_change_pin = true,
                is_active = true
            WHERE pin_hash IS NULL
            """
        ).bindparams(pin_hash=default_pin_hash())
    )

    op.alter_column("users", "pin_hash", nullable=False)
    op.alter_column("users", "pin_set_at", nullable=False)
    op.alter_column("users", "updated_at", nullable=False)
    op.create_index("uq_users_display_name_lower", "users", [sa.text("lower(display_name)")], unique=True)

    op.create_table(
        "user_roles",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("role", sa.Text(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role in ('admin', 'submitter', 'reviewer')", name="ck_user_roles_role"),
    )

    op.execute(
        """
        INSERT INTO user_roles (user_id, role, created_at)
        SELECT id,
               CASE
                   WHEN role = 'reviewer' THEN 'reviewer'
                   ELSE 'admin'
               END AS role,
               created_at
        FROM users
        ON CONFLICT DO NOTHING
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_role') THEN
                ALTER TABLE users DROP CONSTRAINT ck_users_role;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_role') THEN
                ALTER TABLE users DROP CONSTRAINT users_role;
            END IF;
        END $$;
        """
    )
    op.drop_column("users", "role")

    op.create_table(
        "user_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("auth_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)


def downgrade() -> None:
    op.add_column("users", sa.Column("role", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE users
        SET role = COALESCE(
            (SELECT CASE WHEN ur.role = 'reviewer' THEN 'reviewer' ELSE 'admin' END
             FROM user_roles ur
             WHERE ur.user_id = users.id
             ORDER BY CASE ur.role WHEN 'admin' THEN 0 WHEN 'reviewer' THEN 1 ELSE 2 END
             LIMIT 1),
            'reviewer'
        )
        """
    )
    op.alter_column("users", "role", nullable=False)
    op.create_check_constraint(
        "ck_users_role",
        "users",
        "role in ('admin', 'standards_owner', 'methods_lead', 'qa_admin', 'reviewer')",
    )

    op.drop_index("uq_user_sessions_token_hash", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_table("user_roles")
    op.drop_index("uq_users_display_name_lower", table_name="users")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "is_active")
    op.drop_column("users", "must_change_pin")
    op.drop_column("users", "pin_set_at")
    op.drop_column("users", "pin_hash")
