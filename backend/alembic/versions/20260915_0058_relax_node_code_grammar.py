"""Add durable ISO ICS taxonomy persistence.

Revision ID: 20260915_0058
Revises: 20260911_0057
Create Date: 2026-09-15 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260915_0058"
down_revision: Union[str, None] = "20260911_0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the historic grammar intact and add only the three literal ISO ICS
    # shapes. A trigger below prevents dotted codes in unrelated schemes.
    op.drop_constraint(op.f("ck_classification_nodes_node_code"), "classification_nodes", type_="check")
    op.create_check_constraint(
        op.f("ck_classification_nodes_node_code"),
        "classification_nodes",
        "node_code ~ '^([A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]|[0-9]{2}[.][0-9]{3}([.][0-9]{2})?)$'",
    )
    op.execute(
        """
        CREATE FUNCTION enforce_classification_node_code_grammar()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owning_scheme_code text;
        BEGIN
          SELECT scheme_code INTO STRICT owning_scheme_code
          FROM classification_schemes WHERE id = NEW.scheme_id;
          IF NEW.node_code ~ '^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$' THEN
            RETURN NEW;
          END IF;
          IF owning_scheme_code LIKE 'ISO-ICS-%'
             AND NEW.node_code ~ '^[0-9]{2}[.][0-9]{3}([.][0-9]{2})?$' THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'invalid classification node code grammar'
            USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_classification_node_code_grammar
        BEFORE INSERT OR UPDATE OF node_code, scheme_id ON classification_nodes
        FOR EACH ROW EXECUTE FUNCTION enforce_classification_node_code_grammar()
        """
    )

    op.create_table(
        "ics_taxonomy_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("edition", sa.Integer(), nullable=False),
        sa.Column("publication_year", sa.Integer(), nullable=False),
        sa.Column("source_update_year", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("browse_url", sa.Text(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=False),
        sa.Column("license_code", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("clarification", sa.Text(), nullable=False),
        sa.Column("limitation", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("source_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ics_taxonomy_imports"),
        sa.ForeignKeyConstraint(
            ["scheme_id"], ["classification_schemes.id"],
            ondelete="RESTRICT", name="fk_ics_taxonomy_imports_scheme_id_classification_schemes",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            ondelete="SET NULL", name="fk_ics_taxonomy_imports_created_by_user_id_users",
        ),
        sa.CheckConstraint("edition > 0", name="edition"),
        sa.CheckConstraint("publication_year >= 1900", name="publication_year"),
        sa.CheckConstraint("source_update_year >= publication_year", name="source_update_year"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256"),
        sa.UniqueConstraint(
            "scheme_id", "content_sha256", name="uq_ics_taxonomy_imports_scheme_id_content_sha256"
        ),
    )
    op.create_index(
        "ix_ics_taxonomy_imports_scheme_retrieved",
        "ics_taxonomy_imports",
        ["scheme_id", "retrieved_at"],
    )

    op.create_table(
        "ics_domain_crosswalks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_scheme_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("review_status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ics_domain_crosswalks"),
        sa.ForeignKeyConstraint(
            ["import_id"], ["ics_taxonomy_imports.id"],
            ondelete="RESTRICT", name="fk_ics_domain_crosswalks_import_id_ics_taxonomy_imports",
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id", "source_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT", name="fk_ics_domain_crosswalks_source_node_scheme",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id", "target_scheme_id"],
            ["classification_nodes.id", "classification_nodes.scheme_id"],
            ondelete="RESTRICT", name="fk_ics_domain_crosswalks_target_node_scheme",
        ),
        sa.CheckConstraint("relation in ('broader', 'candidate')", name="relation"),
        sa.CheckConstraint(
            "review_status in ('initial_broader', 'needs_review', 'approved', 'rejected')",
            name="review_status",
        ),
        sa.CheckConstraint(
            "btrim(reason) <> '' and char_length(reason) <= 4000", name="reason"
        ),
        sa.UniqueConstraint(
            "import_id", "source_node_id", "target_node_id",
            name="uq_ics_domain_crosswalks_import_source_target",
        ),
    )
    op.create_index(
        "ix_ics_domain_crosswalks_source_node",
        "ics_domain_crosswalks",
        ["source_scheme_id", "source_node_id"],
    )
    op.create_index(
        "ix_ics_domain_crosswalks_target_node",
        "ics_domain_crosswalks",
        ["target_scheme_id", "target_node_id"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_ics_taxonomy_import_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'ICS taxonomy import provenance is immutable'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ics_taxonomy_imports_immutable
        BEFORE UPDATE OR DELETE ON ics_taxonomy_imports
        FOR EACH ROW EXECUTE FUNCTION reject_ics_taxonomy_import_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "LOCK TABLE classification_nodes, ics_domain_crosswalks, "
        "ics_taxonomy_imports IN EXCLUSIVE MODE"
    )
    dotted = op.get_bind().execute(
        sa.text("SELECT count(*) FROM classification_nodes WHERE node_code LIKE '%.%'")
    ).scalar()
    imports = op.get_bind().execute(sa.text("SELECT count(*) FROM ics_taxonomy_imports")).scalar()
    if dotted or imports:
        raise RuntimeError("Cannot discard ICS identifiers or immutable import provenance")

    op.execute("DROP TRIGGER trg_ics_taxonomy_imports_immutable ON ics_taxonomy_imports")
    op.execute("DROP FUNCTION reject_ics_taxonomy_import_mutation()")
    op.drop_index("ix_ics_domain_crosswalks_target_node", table_name="ics_domain_crosswalks")
    op.drop_index("ix_ics_domain_crosswalks_source_node", table_name="ics_domain_crosswalks")
    op.drop_table("ics_domain_crosswalks")
    op.drop_index("ix_ics_taxonomy_imports_scheme_retrieved", table_name="ics_taxonomy_imports")
    op.drop_table("ics_taxonomy_imports")
    op.execute("DROP TRIGGER trg_classification_node_code_grammar ON classification_nodes")
    op.execute("DROP FUNCTION enforce_classification_node_code_grammar()")
    op.drop_constraint(op.f("ck_classification_nodes_node_code"), "classification_nodes", type_="check")
    op.create_check_constraint(
        op.f("ck_classification_nodes_node_code"),
        "classification_nodes",
        "node_code ~ '^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$'",
    )
