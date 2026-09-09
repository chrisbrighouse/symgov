"""add symbol semantic assignments

Revision ID: 20260909_0048
Revises: 20260909_0047
Create Date: 2026-09-09 00:00:00.000000

SM-P0-02 of the Semantic Model & Classification Change Specification: the
bridge from a graphical symbol revision to the engineering meaning it carries,
with primary/qualifier/component roles and a governed review state. Purely
additive -- no existing table, column or behaviour changes.

Both foreign keys to the pre-existing tables are named explicitly: the naming
convention would generate 66- and 68-character identifiers, past PostgreSQL's
63-character limit.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0048"
down_revision: Union[str, None] = "20260909_0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbol_semantic_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_symbol_revision_id"),
            nullable=False,
        ),
        sa.Column(
            "semantic_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_symbol_semantic_assignments_semantic_concept_id"),
            nullable=False,
        ),
        sa.Column("assignment_role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("proposed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_symbol_semantic_assignments"),
        sa.CheckConstraint(
            "assignment_role in ('primary', 'qualifier', 'component')",
            name="ck_symbol_semantic_assignments_assignment_role",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="ck_symbol_semantic_assignments_status",
        ),
        sa.CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted')",
            name="ck_symbol_semantic_assignments_method",
        ),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="ck_symbol_semantic_assignments_confidence",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="ck_symbol_semantic_assignments_review_decision",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="ck_symbol_semantic_assignments_evidence_json_object",
        ),
    )

    # Specification section 7.9: at most one verified primary concept per symbol
    # revision. Proposals are deliberately left unconstrained so competing
    # candidates can sit side by side awaiting review.
    op.create_index(
        "uq_symbol_semantic_assignments_verified_primary",
        "symbol_semantic_assignments",
        ["symbol_revision_id"],
        unique=True,
        postgresql_where=sa.text("assignment_role = 'primary' and status = 'verified'"),
    )
    op.create_index(
        "ix_symbol_semantic_assignments_revision_status",
        "symbol_semantic_assignments",
        ["symbol_revision_id", "status"],
    )
    op.create_index(
        "ix_symbol_semantic_assignments_concept_status",
        "symbol_semantic_assignments",
        ["semantic_concept_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_symbol_semantic_assignments_concept_status", table_name="symbol_semantic_assignments")
    op.drop_index("ix_symbol_semantic_assignments_revision_status", table_name="symbol_semantic_assignments")
    op.drop_index("uq_symbol_semantic_assignments_verified_primary", table_name="symbol_semantic_assignments")
    op.drop_table("symbol_semantic_assignments")
