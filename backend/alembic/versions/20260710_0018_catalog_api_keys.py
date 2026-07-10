"""catalog api keys

Revision ID: 20260710_0018
Revises: 20260624_0017
Create Date: 2026-07-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260710_0018"
down_revision: Union[str, None] = "20260624_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)

ALLOWED_CATALOG_API_SCOPES = (
    "catalog.read",
    "catalog.preview",
    "catalog.ed.query",
    "catalog.feedback.write",
    "catalog.usage.read",
)


def upgrade() -> None:
    op.create_table(
        "catalog_api_keys",
        sa.Column("id", UUID, nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("integration_name", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("scopes_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("allowed_origins_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("status in ('active', 'disabled', 'revoked')", name="status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_catalog_api_keys_key_hash", "catalog_api_keys", ["key_hash"], unique=True)
    op.create_index("ix_catalog_api_keys_key_prefix", "catalog_api_keys", ["key_prefix"])
    op.create_index(
        "ix_catalog_api_keys_customer_integration",
        "catalog_api_keys",
        ["customer_name", "integration_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_api_keys_customer_integration", table_name="catalog_api_keys")
    op.drop_index("ix_catalog_api_keys_key_prefix", table_name="catalog_api_keys")
    op.drop_index("uq_catalog_api_keys_key_hash", table_name="catalog_api_keys")
    op.drop_table("catalog_api_keys")
