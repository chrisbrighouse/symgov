"""add_scott_auth_fields

Revision ID: 13ae79a44f79
Revises: 20260606_0015
Create Date: 2026-06-12 14:29:46.714604

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '13ae79a44f79'
down_revision: Union[str, None] = '20260606_0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('scott_source_discovery_sites', sa.Column('requires_auth', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('scott_source_discovery_sites', sa.Column('auth_status', sa.Text(), nullable=False, server_default='no_auth'))
    op.add_column('scott_source_discovery_sites', sa.Column('auth_secret_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('scott_source_discovery_sites', 'auth_secret_key')
    op.drop_column('scott_source_discovery_sites', 'auth_status')
    op.drop_column('scott_source_discovery_sites', 'requires_auth')
