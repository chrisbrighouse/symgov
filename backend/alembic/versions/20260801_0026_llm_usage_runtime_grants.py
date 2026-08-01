"""grant append-only ledger access to the runtime role

Revision ID: 20260801_0026
Revises: 20260730_0025
Create Date: 2026-08-01 16:30:00.000000
"""

from alembic import op


revision = "20260801_0026"
down_revision = "20260730_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT SELECT, INSERT ON TABLE llm_usage_events TO symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON TABLE llm_usage_events FROM symgov_app")
