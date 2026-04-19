"""expand classification records for libby

Revision ID: 20260419_0002
Revises: 20260409_0001
Create Date: 2026-04-19 12:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260419_0002"
down_revision = "20260409_0001"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("classification_records", sa.Column("intake_record_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("validation_report_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("provenance_assessment_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("review_case_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("origin_attachment_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("origin_object_key", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("origin_file_name", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("origin_batch_id", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("parent_review_case_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("symbol_key", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("symbol_region_index", sa.Integer(), nullable=True))
    op.add_column(
        "classification_records",
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'current'")),
    )
    op.add_column(
        "classification_records",
        sa.Column("classification_status", sa.Text(), nullable=False, server_default=sa.text("'provisional'")),
    )
    op.add_column("classification_records", sa.Column("supersedes_classification_id", UUID, nullable=True))
    op.add_column("classification_records", sa.Column("format", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("industry", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("symbol_family", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("process_category", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("parent_equipment_class", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("standards_source", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("library_provenance_class", sa.Text(), nullable=True))
    op.add_column("classification_records", sa.Column("source_classification", sa.Text(), nullable=True))
    op.add_column(
        "classification_records",
        sa.Column("source_refs_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "classification_records",
        sa.Column("evidence_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "classification_records",
        sa.Column("taxonomy_terms_created_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("classification_records", sa.Column("review_summary", sa.Text(), nullable=True))
    op.add_column(
        "classification_records",
        sa.Column("libby_approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "classification_records",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_foreign_key(
        "fk_clsrec_intake_record",
        "classification_records",
        "intake_records",
        ["intake_record_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_validation_report",
        "classification_records",
        "validation_reports",
        ["validation_report_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_provenance_assessment",
        "classification_records",
        "provenance_assessments",
        ["provenance_assessment_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_review_case",
        "classification_records",
        "review_cases",
        ["review_case_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_origin_attachment",
        "classification_records",
        "attachments",
        ["origin_attachment_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_parent_review_case",
        "classification_records",
        "review_cases",
        ["parent_review_case_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_clsrec_supersedes_clsrec",
        "classification_records",
        "classification_records",
        ["supersedes_classification_id"],
        ["id"],
    )

    op.create_index(
        "ix_classification_records_symbol_status_created_at",
        "classification_records",
        ["symbol_key", "status", "created_at"],
    )
    op.create_index(
        "ix_classification_records_review_case_created_at",
        "classification_records",
        ["review_case_id", "created_at"],
    )
    op.create_index(
        "ix_classification_records_validation_report_created_at",
        "classification_records",
        ["validation_report_id", "created_at"],
    )
    op.create_index(
        "ix_classification_records_provenance_assessment_created_at",
        "classification_records",
        ["provenance_assessment_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_classification_records_provenance_assessment_created_at", table_name="classification_records")
    op.drop_index("ix_classification_records_validation_report_created_at", table_name="classification_records")
    op.drop_index("ix_classification_records_review_case_created_at", table_name="classification_records")
    op.drop_index("ix_classification_records_symbol_status_created_at", table_name="classification_records")

    op.drop_constraint("fk_clsrec_supersedes_clsrec", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_parent_review_case", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_origin_attachment", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_review_case", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_provenance_assessment", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_validation_report", "classification_records", type_="foreignkey")
    op.drop_constraint("fk_clsrec_intake_record", "classification_records", type_="foreignkey")

    op.drop_column("classification_records", "updated_at")
    op.drop_column("classification_records", "libby_approved")
    op.drop_column("classification_records", "review_summary")
    op.drop_column("classification_records", "taxonomy_terms_created_json")
    op.drop_column("classification_records", "evidence_json")
    op.drop_column("classification_records", "source_refs_json")
    op.drop_column("classification_records", "source_classification")
    op.drop_column("classification_records", "library_provenance_class")
    op.drop_column("classification_records", "standards_source")
    op.drop_column("classification_records", "parent_equipment_class")
    op.drop_column("classification_records", "process_category")
    op.drop_column("classification_records", "symbol_family")
    op.drop_column("classification_records", "industry")
    op.drop_column("classification_records", "format")
    op.drop_column("classification_records", "supersedes_classification_id")
    op.drop_column("classification_records", "classification_status")
    op.drop_column("classification_records", "status")
    op.drop_column("classification_records", "symbol_region_index")
    op.drop_column("classification_records", "symbol_key")
    op.drop_column("classification_records", "parent_review_case_id")
    op.drop_column("classification_records", "origin_batch_id")
    op.drop_column("classification_records", "origin_file_name")
    op.drop_column("classification_records", "origin_object_key")
    op.drop_column("classification_records", "origin_attachment_id")
    op.drop_column("classification_records", "review_case_id")
    op.drop_column("classification_records", "provenance_assessment_id")
    op.drop_column("classification_records", "validation_report_id")
    op.drop_column("classification_records", "intake_record_id")
