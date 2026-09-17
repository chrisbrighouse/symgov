"""Give specification section 7.3 `SemanticConceptRelationship` a table (SM-P1-03 WP3.2).

Section 7.3 describes a governed, directed relationship between two semantic
concepts -- `broader`, `component_of`, `function_of` and their inverses -- and
it is the only section 7 entity that reached no section 15.1 work package. The
visible consequence was that `classification_mapping.py` could only ever record
`parentEquipmentClass` as a gap whose reason was *"the table does not exist"*.
Decision D6 (2026-09-17) ruled that a specification omission, not a deferral,
so this revision creates the table.

Two shape decisions carry governance weight:

* **A row is one directed assertion.** Section 7.3 ships both directions of
  each pair, so the unique index is on `(source, target, relationship_type)`
  and nothing derives, mints or refuses an inverse. `broader(A, B)` and
  `narrower(B, A)` are two assertions with two reviews.
* **`method` and `confidence` are carried** although section 7.3's field list
  omits them, so section 8.4's explicit auto-verification policy has a column
  to inspect. `legacy_backfill` is deliberately absent from the vocabulary:
  section 12.1 phase M2 backfills classifications, and no relationship
  backfill exists to write it.

Cycle prevention is *not* attempted. A `broader` chain can close a loop across
several rows and no check constraint can see more than one row; the single-row
case is refused by `distinct_concepts`, and the rest is a review question.

No `GRANT` is issued, following SM-P0-04..-08: the narrow `symgov_app` role
reaches these tables through ownership, not through per-table privileges.

Identifier lengths: all four foreign keys are named explicitly (the convention
appends the referred table and would generate 69 to 76 characters against
PostgreSQL's 63-character limit). Check constraints are given *bare* names and
left to `NAMING_CONVENTION` to prefix -- an already-prefixed name passed to
`sa.CheckConstraint` is prefixed twice and hash-truncated silently.

Revision ID: 20260917_0060
Revises: 20260916_0059
Create Date: 2026-09-17 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260917_0060"
down_revision: Union[str, None] = "20260916_0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "semantic_concept_relationships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # RESTRICT on both ends: a concept that something asserts a
        # relationship about is not deletable out from under the assertion.
        sa.Column(
            "source_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_semantic_concept_relationships_source_concept_id"),
            nullable=False,
        ),
        sa.Column(
            "target_concept_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("semantic_concepts.id", ondelete="RESTRICT", name="fk_semantic_concept_relationships_target_concept_id"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_semantic_concept_relationships_proposed_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_semantic_concept_relationships_reviewed_by_user_id"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_semantic_concept_relationships"),
        # Section 7.3's controlled vocabulary, entire.
        sa.CheckConstraint(
            "relationship_type in ('broader', 'narrower', 'related', 'component_of', "
            "'has_component', 'function_of', 'has_function', 'equivalent_internal')",
            name="relationship_type",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'verified', 'rejected', 'retired')",
            name="status",
        ),
        # Section 7.9's vocabulary minus `legacy_backfill`; see the module
        # docstring for why the omission is deliberate.
        sa.CheckConstraint(
            "method in ('manual', 'source_mapping', 'rule', 'ai_assisted')",
            name="method",
        ),
        sa.CheckConstraint(
            "confidence is null or (confidence >= 0 and confidence <= 1)",
            name="confidence",
        ),
        sa.CheckConstraint(
            "status in ('proposed', 'retired') or reviewed_at is not null",
            name="review_decision",
        ),
        # A concept is not broader than, nor a component of, itself.
        sa.CheckConstraint(
            "source_concept_id <> target_concept_id",
            name="distinct_concepts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
    )
    # Section 14.3: a concept's own assertions, and the assertions about it.
    op.create_index(
        "ix_semantic_concept_relationships_source_status",
        "semantic_concept_relationships",
        ["source_concept_id", "status"],
    )
    op.create_index(
        "ix_semantic_concept_relationships_target_status",
        "semantic_concept_relationships",
        ["target_concept_id", "status"],
    )
    # One live assertion per (source, target, type). Rejected and retired rows
    # stay out so a relationship's governance history survives its successor.
    op.create_index(
        "uq_semantic_concept_relationships_active",
        "semantic_concept_relationships",
        ["source_concept_id", "target_concept_id", "relationship_type"],
        unique=True,
        postgresql_where=sa.text("status in ('proposed', 'verified')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_semantic_concept_relationships_active",
        table_name="semantic_concept_relationships",
    )
    op.drop_index(
        "ix_semantic_concept_relationships_target_status",
        table_name="semantic_concept_relationships",
    )
    op.drop_index(
        "ix_semantic_concept_relationships_source_status",
        table_name="semantic_concept_relationships",
    )
    op.drop_table("semantic_concept_relationships")
