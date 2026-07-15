"""catalog feedback attribution

Revision ID: 20260714_0020
Revises: 20260710_0019
Create Date: 2026-07-14 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260714_0020"
down_revision: Union[str, None] = "20260710_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)

EXACTLY_ONE_SUBMITTER = (
    "(submitted_by is not null)::int + "
    "(external_submitter_id is not null)::int + "
    "(catalog_api_key_id is not null)::int = 1"
)
ORIGINAL_ONE_SUBMITTER = (
    "((submitted_by is not null and external_submitter_id is null) or "
    "(submitted_by is null and external_submitter_id is not null))"
)


def upgrade() -> None:
    op.add_column(
        "clarification_records",
        sa.Column("catalog_api_key_id", UUID, nullable=True),
    )
    op.add_column(
        "clarification_records",
        sa.Column("context_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.drop_constraint("ck_clarification_records_one_submitter", "clarification_records", type_="check")
    op.create_check_constraint(
        op.f("ck_clarification_records_exactly_one_submitter"),
        "clarification_records",
        EXACTLY_ONE_SUBMITTER,
    )
    op.create_foreign_key(
        "fk_clarification_records_catalog_api_key_id",
        "clarification_records",
        "catalog_api_keys",
        ["catalog_api_key_id"],
        ["id"],
    )
    op.create_index(
        "ix_clarification_records_catalog_api_key_created_at",
        "clarification_records",
        ["catalog_api_key_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_clarification_records_catalog_api_key_created_at", table_name="clarification_records")
    op.drop_constraint(
        "fk_clarification_records_catalog_api_key_id",
        "clarification_records",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("ck_clarification_records_exactly_one_submitter"),
        "clarification_records",
        type_="check",
    )
    op.create_check_constraint(
        "ck_clarification_records_one_submitter",
        "clarification_records",
        ORIGINAL_ONE_SUBMITTER,
    )
    op.drop_column("clarification_records", "context_json")
    op.drop_column("clarification_records", "catalog_api_key_id")
