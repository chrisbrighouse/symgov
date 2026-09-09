"""add semantic concept core identity and revisions

Revision ID: 20260909_0047
Revises: 20260908_0046
Create Date: 2026-09-09 00:00:00.000000

Introduces the governed semantic concept layer from SM-P0-01 of the Semantic
Model & Classification Change Specification. Purely additive: no existing table,
column or behaviour changes, so the catalogue keeps running on category and
discipline untouched.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260909_0047"
down_revision: Union[str, None] = "20260908_0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "semantic_concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("concept_code", sa.Text(), nullable=False),
        sa.Column("concept_kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        # Circular reference to semantic_concept_revisions; the foreign key is
        # added once that table exists.
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_concepts"),
        sa.CheckConstraint(
            "concept_code ~ '^SGC-[0-9]{8}$'",
            name="ck_semantic_concepts_concept_code_grammar",
        ),
        sa.CheckConstraint(
            "concept_kind in ('physical_equipment', 'function', 'property', 'state', 'action', 'annotation', 'connection', 'safety_function', 'other')",
            name="ck_semantic_concepts_concept_kind",
        ),
        sa.CheckConstraint(
            "status in ('draft', 'active', 'deprecated', 'withdrawn')",
            name="ck_semantic_concepts_status",
        ),
    )
    op.create_index("uq_semantic_concepts_concept_code", "semantic_concepts", ["concept_code"], unique=True)
    op.create_index("ix_semantic_concepts_status_concept_code", "semantic_concepts", ["status", "concept_code"])

    op.create_table(
        "semantic_concept_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_label", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.Text(), nullable=False),
        sa.Column("preferred_name", sa.Text(), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("aliases_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_concept_revisions"),
        sa.CheckConstraint(
            "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn')",
            name="ck_semantic_concept_revisions_lifecycle_state",
        ),
        sa.CheckConstraint(
            "btrim(revision_label) <> '' and char_length(revision_label) <= 64",
            name="ck_semantic_concept_revisions_revision_label",
        ),
        sa.CheckConstraint(
            "btrim(preferred_name) <> '' and char_length(preferred_name) <= 256",
            name="ck_semantic_concept_revisions_preferred_name",
        ),
        sa.CheckConstraint(
            "btrim(definition) <> '' and char_length(definition) <= 8000",
            name="ck_semantic_concept_revisions_definition",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(aliases_json) = 'array'",
            name="ck_semantic_concept_revisions_aliases_json_array",
        ),
        sa.CheckConstraint(
            "notes is null or (btrim(notes) <> '' and char_length(notes) <= 4000)",
            name="ck_semantic_concept_revisions_notes",
        ),
        sa.CheckConstraint(
            "rationale is null or (btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="ck_semantic_concept_revisions_rationale",
        ),
    )
    op.create_index(
        "uq_semantic_concept_revisions_concept_revision_label",
        "semantic_concept_revisions",
        ["concept_id", "revision_label"],
        unique=True,
    )
    op.create_index(
        "ix_semantic_concept_revisions_concept_created_at",
        "semantic_concept_revisions",
        ["concept_id", "created_at"],
    )
    op.create_index(
        "ix_semantic_concept_revisions_lifecycle_state",
        "semantic_concept_revisions",
        ["lifecycle_state"],
    )

    op.create_foreign_key(
        "fk_semantic_concepts_current_revision_id",
        "semantic_concepts",
        "semantic_concept_revisions",
        ["current_revision_id"],
        ["id"],
    )

    # MAXVALUE mirrors the SGC-######## grammar check so the sequence can never
    # hand out a value that the concept_code constraint would reject.
    op.execute("CREATE SEQUENCE semantic_concept_code_seq START 1 MAXVALUE 99999999 NO CYCLE")


def downgrade() -> None:
    op.execute("DROP SEQUENCE semantic_concept_code_seq")
    op.drop_constraint(
        "fk_semantic_concepts_current_revision_id",
        "semantic_concepts",
        type_="foreignkey",
    )
    op.drop_index("ix_semantic_concept_revisions_lifecycle_state", table_name="semantic_concept_revisions")
    op.drop_index("ix_semantic_concept_revisions_concept_created_at", table_name="semantic_concept_revisions")
    op.drop_index("uq_semantic_concept_revisions_concept_revision_label", table_name="semantic_concept_revisions")
    op.drop_table("semantic_concept_revisions")
    op.drop_index("ix_semantic_concepts_status_concept_code", table_name="semantic_concepts")
    op.drop_index("uq_semantic_concepts_concept_code", table_name="semantic_concepts")
    op.drop_table("semantic_concepts")
