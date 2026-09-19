"""Add immutable published preview authorizations.

Revision ID: 20260919_0061
Revises: 20260917_0060
Create Date: 2026-09-19 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260919_0061"
down_revision: Union[str, None] = "20260917_0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "published_preview_authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attachments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("attachment_parent_type", sa.Text(), nullable=False),
        sa.Column("attachment_parent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_content_type", sa.Text(), nullable=False),
        sa.Column("attachment_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("attachment_sha256", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "symbol_revision_id",
            "object_key",
            name="uq_published_preview_authorizations_revision_object_key",
        ),
        sa.UniqueConstraint("object_key", name="uq_published_preview_authorizations_object_key"),
        sa.CheckConstraint("object_key <> ''", name="ck_published_preview_authorizations_object_key_nonempty"),
        sa.CheckConstraint(
            "attachment_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_published_preview_authorizations_sha256",
        ),
        sa.CheckConstraint(
            "attachment_size_bytes >= 0",
            name="ck_published_preview_authorizations_size_nonnegative",
        ),
    )


def downgrade() -> None:
    op.drop_table("published_preview_authorizations")
