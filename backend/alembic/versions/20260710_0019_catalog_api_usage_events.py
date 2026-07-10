"""catalog api usage events

Revision ID: 20260710_0019
Revises: 20260710_0018
Create Date: 2026-07-10 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260710_0019"
down_revision: Union[str, None] = "20260710_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "catalog_api_usage_events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("api_key_id", UUID, sa.ForeignKey("catalog_api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_name_snapshot", sa.Text(), nullable=False),
        sa.Column("integration_name_snapshot", sa.Text(), nullable=False),
        sa.Column("scope_used", sa.Text(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("route_name", sa.Text(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column("symbol_ref", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("ed_query_type", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("client_ip_hash", sa.Text(), nullable=True),
        sa.Column("application_name", sa.Text(), nullable=True),
        sa.Column("application_version", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_api_usage_events_api_key_created",
        "catalog_api_usage_events",
        ["api_key_id", "created_at"],
    )
    op.create_index(
        "ix_catalog_api_usage_events_customer_created",
        "catalog_api_usage_events",
        ["customer_name_snapshot", "integration_name_snapshot", "created_at"],
    )
    op.create_index(
        "ix_catalog_api_usage_events_route_created",
        "catalog_api_usage_events",
        ["route_name", "status_code", "created_at"],
    )
    op.create_index(
        "ix_catalog_api_usage_events_symbol_created",
        "catalog_api_usage_events",
        ["symbol_ref", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_catalog_api_usage_events_symbol_created", table_name="catalog_api_usage_events")
    op.drop_index("ix_catalog_api_usage_events_route_created", table_name="catalog_api_usage_events")
    op.drop_index("ix_catalog_api_usage_events_customer_created", table_name="catalog_api_usage_events")
    op.drop_index("ix_catalog_api_usage_events_api_key_created", table_name="catalog_api_usage_events")
    op.drop_table("catalog_api_usage_events")
