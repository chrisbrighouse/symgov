"""tracy rights state separation

Revision ID: 20260619_0016
Revises: 13ae79a44f79
Create Date: 2026-06-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260619_0016'
down_revision: Union[str, None] = '13ae79a44f79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add columns as nullable first
    op.add_column('provenance_assessments', sa.Column('rights_disposition', sa.Text(), nullable=True))
    op.add_column('provenance_assessments', sa.Column('processing_outcome', sa.Text(), nullable=True))

    # 2. Backfill data
    # rights_disposition
    op.execute("""
        UPDATE provenance_assessments
        SET rights_disposition = CASE
            WHEN rights_status = 'cleared' THEN 'cleared'
            WHEN rights_status = 'unknown' THEN 'unknown_warning'
            WHEN rights_status = 'restricted' THEN 'restricted'
            WHEN rights_status = 'conflict' THEN 'conflict'
            ELSE 'failed'
        END
    """)
    # processing_outcome
    op.execute("""
        UPDATE provenance_assessments
        SET processing_outcome = CASE
            WHEN report_json->>'decision' = 'pass' THEN 'pass'
            WHEN rights_status IN ('restricted', 'conflict') THEN 'failed'
            WHEN report_json->>'decision' = 'fail' THEN 'failed'
            ELSE 'review_required'
        END
    """)

    # 3. Set NOT NULL and add constraints
    op.alter_column('provenance_assessments', 'rights_disposition', nullable=False)
    op.alter_column('provenance_assessments', 'processing_outcome', nullable=False)

    op.create_check_constraint(
        'ck_provenance_assessments_rights_disposition',
        'provenance_assessments',
        sa.column('rights_disposition').in_(['cleared', 'unknown_warning', 'restricted', 'conflict', 'failed'])
    )
    op.create_check_constraint(
        'ck_provenance_assessments_processing_outcome',
        'provenance_assessments',
        sa.column('processing_outcome').in_(['pass', 'review_required', 'failed'])
    )


def downgrade() -> None:
    op.drop_constraint('ck_provenance_assessments_processing_outcome', 'provenance_assessments')
    op.drop_constraint('ck_provenance_assessments_rights_disposition', 'provenance_assessments')
    op.drop_column('provenance_assessments', 'processing_outcome')
    op.drop_column('provenance_assessments', 'rights_disposition')
