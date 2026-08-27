"""add immutable publication approval targets

Revision ID: 20260826_0032
Revises: 20260826_0031
Create Date: 2026-08-26 20:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260826_0032"
down_revision: Union[str, None] = "20260826_0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION lock_human_review_decision_authority()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            target_review_case_id uuid;
        BEGIN
            target_review_case_id := COALESCE(NEW.review_case_id, OLD.review_case_id);
            PERFORM pg_advisory_xact_lock(
                hashtextextended('publication-authority:' || target_review_case_id::text, 0)
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_human_review_decisions_authority_lock
        BEFORE INSERT OR UPDATE OR DELETE ON human_review_decisions
        FOR EACH ROW EXECUTE FUNCTION lock_human_review_decision_authority()
        """
    )
    op.create_table(
        "publication_approval_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "review_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("human_review_decisions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "review_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("review_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision_targets_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(revision_targets_json) = 'array' "
            "AND jsonb_array_length(revision_targets_json) > 0",
            name="publication_approval_targets_nonempty_revisions",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="publication_approval_targets_sha256",
        ),
    )
    op.create_index(
        "ix_publication_approval_targets_case_created_at",
        "publication_approval_targets",
        ["review_case_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_publication_approval_target_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'publication approval targets are immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publication_approval_targets_immutable
        BEFORE UPDATE OR DELETE ON publication_approval_targets
        FOR EACH ROW EXECUTE FUNCTION reject_publication_approval_target_mutation()
        """
    )


def downgrade() -> None:
    op.execute("LOCK TABLE publication_approval_targets IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM publication_approval_targets) THEN
                RAISE EXCEPTION 'publication approval target downgrade refused: immutable approvals exist'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_publication_approval_targets_immutable "
        "ON publication_approval_targets"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_publication_approval_target_mutation()")
    op.drop_index(
        "ix_publication_approval_targets_case_created_at",
        table_name="publication_approval_targets",
    )
    op.drop_table("publication_approval_targets")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_human_review_decisions_authority_lock "
        "ON human_review_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS lock_human_review_decision_authority()")
