"""add DB-level barrier between catalog_symbol_id and organization_private visibility

Revision ID: 20260901_0034
Revises: 20260829_0033
Create Date: 2026-09-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260901_0034"
down_revision: Union[str, None] = "20260829_0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "catalog_symbol_visibility_barrier",
        "governed_symbols",
        "catalog_symbol_id is null or visibility = 'public'",
        postgresql_not_valid=True,
    )
    op.execute(
        "ALTER TABLE governed_symbols "
        "VALIDATE CONSTRAINT ck_governed_symbols_catalog_symbol_visibility_barrier"
    )


def downgrade() -> None:
    op.drop_constraint(
        "catalog_symbol_visibility_barrier",
        "governed_symbols",
        type_="check",
    )
