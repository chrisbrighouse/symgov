"""initial symgov schema

Revision ID: 20260409_0001
Revises:
Create Date: 2026-04-09 13:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260409_0001"
down_revision = None
branch_labels = None
depends_on = None


JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role in ('admin', 'standards_owner', 'methods_lead', 'qa_admin', 'reviewer')", name="ck_users_role"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "governed_symbols",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("current_revision_id", UUID, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_governed_symbols_slug", "governed_symbols", ["slug"], unique=True)

    op.create_table(
        "attachments",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("parent_type", sa.Text(), nullable=False),
        sa.Column("parent_id", UUID, nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_attachments_object_key", "attachments", ["object_key"], unique=True)

    op.create_table(
        "audit_events",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("payload_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "external_identities",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("identity_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "identity_type in ('engineer', 'contractor', 'submitter', 'external_reviewer', 'other')",
            name="ck_external_identities_identity_type",
        ),
        sa.CheckConstraint("status in ('active', 'inactive')", name="ck_external_identities_status"),
    )
    op.create_index(
        "uq_external_identities_email_lower",
        "external_identities",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("email is not null"),
    )

    op.create_table(
        "symbol_revisions",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("symbol_id", UUID, sa.ForeignKey("governed_symbols.id"), nullable=False),
        sa.Column("revision_label", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("author_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated')",
            name="ck_symbol_revisions_lifecycle_state",
        ),
    )
    op.create_index("uq_symbol_revisions_symbol_revision_label", "symbol_revisions", ["symbol_id", "revision_label"], unique=True)
    op.create_index("ix_symbol_revisions_symbol_created_at", "symbol_revisions", ["symbol_id", "created_at"])
    op.create_foreign_key(
        "fk_governed_symbols_current_revision_id_symbol_revisions",
        "governed_symbols",
        "symbol_revisions",
        ["current_revision_id"],
        ["id"],
    )

    op.create_table(
        "source_packages",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("package_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("package_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_source_packages_package_code", "source_packages", ["package_code"], unique=True)

    op.create_table(
        "standards",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("standard_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("issuing_body", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_standards_standard_code", "standards", ["standard_code"], unique=True)

    op.create_table(
        "standard_versions",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("standard_id", UUID, sa.ForeignKey("standards.id"), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_standard_versions_standard_version_label", "standard_versions", ["standard_id", "version_label"], unique=True)
    op.create_index("ix_standard_versions_standard_effective_date", "standard_versions", ["standard_id", "effective_date"])

    op.create_table(
        "source_package_entries",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("source_package_id", UUID, sa.ForeignKey("source_packages.id"), nullable=False),
        sa.Column("symbol_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("source_label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_source_package_entries_package_revision", "source_package_entries", ["source_package_id", "symbol_revision_id"], unique=True)
    op.create_index("ix_source_package_entries_package_sort_order", "source_package_entries", ["source_package_id", "sort_order"])
    op.create_index("ix_source_package_entries_revision_package", "source_package_entries", ["symbol_revision_id", "source_package_id"])

    op.create_table(
        "symbol_standard_links",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("symbol_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=False),
        sa.Column("standard_version_id", UUID, sa.ForeignKey("standard_versions.id"), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("clause_reference", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_symbol_standard_links_revision_standard_relationship_clause",
        "symbol_standard_links",
        ["symbol_revision_id", "standard_version_id", "relationship_type", "clause_reference"],
        unique=True,
    )
    op.create_index("ix_symbol_standard_links_revision_standard", "symbol_standard_links", ["symbol_revision_id", "standard_version_id"])

    op.create_table(
        "publication_packs",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("pack_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_publication_packs_pack_code", "publication_packs", ["pack_code"], unique=True)

    op.create_table(
        "published_pages",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("page_code", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("pack_id", UUID, sa.ForeignKey("publication_packs.id"), nullable=False),
        sa.Column("current_symbol_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_published_pages_page_code", "published_pages", ["page_code"], unique=True)

    op.create_table(
        "change_requests",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("symbol_id", UUID, sa.ForeignKey("governed_symbols.id"), nullable=False),
        sa.Column("proposed_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=False),
        sa.Column("base_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=True),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("revision_delta", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_change_requests_status_priority_due_date", "change_requests", ["status", "priority", "due_date"])
    op.create_index("ix_change_requests_proposed_revision_status_created_at", "change_requests", ["proposed_revision_id", "status", "created_at"])
    op.create_index("ix_change_requests_base_revision_created_at", "change_requests", ["base_revision_id", "created_at"])

    op.create_table(
        "review_decisions",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("change_request_id", UUID, sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("actor_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_decisions_change_request_created_at", "review_decisions", ["change_request_id", "created_at"])

    op.create_table(
        "pack_entries",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("pack_id", UUID, sa.ForeignKey("publication_packs.id"), nullable=False),
        sa.Column("symbol_revision_id", UUID, sa.ForeignKey("symbol_revisions.id"), nullable=False),
        sa.Column("published_page_id", UUID, sa.ForeignKey("published_pages.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_pack_entries_pack_revision_page", "pack_entries", ["pack_id", "symbol_revision_id", "published_page_id"], unique=True)
    op.create_index("ix_pack_entries_pack_sort_order", "pack_entries", ["pack_id", "sort_order"])

    op.create_table(
        "impacted_page_links",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("change_request_id", UUID, sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("published_page_id", UUID, sa.ForeignKey("published_pages.id"), nullable=False),
        sa.Column("impact_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_impacted_page_links_change_request_published_page", "impacted_page_links", ["change_request_id", "published_page_id"])

    op.create_table(
        "clarification_records",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("symbol_id", UUID, sa.ForeignKey("governed_symbols.id"), nullable=False),
        sa.Column("published_page_id", UUID, sa.ForeignKey("published_pages.id"), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("submitted_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("external_submitter_id", UUID, sa.ForeignKey("external_identities.id"), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "((submitted_by is not null and external_submitter_id is null) or (submitted_by is null and external_submitter_id is not null))",
            name="ck_clarification_records_one_submitter",
        ),
    )
    op.create_index("ix_clarification_records_symbol_page_created_at", "clarification_records", ["symbol_id", "published_page_id", "created_at"])
    op.create_index("ix_clarification_records_external_submitter_created_at", "clarification_records", ["external_submitter_id", "created_at"])

    op.create_table(
        "clarification_links",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("clarification_id", UUID, sa.ForeignKey("clarification_records.id"), nullable=False),
        sa.Column("change_request_id", UUID, sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "agent_definitions",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("queue_family", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_agent_definitions_slug", "agent_definitions", ["slug"], unique=True)

    op.create_table(
        "agent_queue_items",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("agent_id", UUID, sa.ForeignKey("agent_definitions.id"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_queue_items_agent_status_priority_created_at", "agent_queue_items", ["agent_id", "status", "priority", "created_at"])

    op.create_table(
        "agent_runs",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("tool_trace_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("result_status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_runs_queue_item_started_at", "agent_runs", ["queue_item_id", "started_at"])

    op.create_table(
        "agent_output_artifacts",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_output_artifacts_queue_type_created_at", "agent_output_artifacts", ["queue_item_id", "artifact_type", "created_at"])

    op.create_table(
        "intake_records",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("submitter", sa.Text(), nullable=False),
        sa.Column("submission_kind", sa.Text(), nullable=False),
        sa.Column("intake_status", sa.Text(), nullable=False),
        sa.Column("eligibility_status", sa.Text(), nullable=False),
        sa.Column("source_package_id", UUID, sa.ForeignKey("source_packages.id"), nullable=True),
        sa.Column("raw_object_key", sa.Text(), nullable=True),
        sa.Column("normalized_submission_json", JSONB, nullable=False),
        sa.Column("routing_recommendation_json", JSONB, nullable=False),
        sa.Column("report_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intake_records_status_eligibility_created_at", "intake_records", ["intake_status", "eligibility_status", "created_at"])

    op.create_table(
        "provenance_assessments",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=False),
        sa.Column("intake_record_id", UUID, sa.ForeignKey("intake_records.id"), nullable=False),
        sa.Column("rights_status", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_json", JSONB, nullable=False),
        sa.Column("report_json", JSONB, nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_provenance_assessments_intake_assessed_at", "provenance_assessments", ["intake_record_id", "assessed_at"])

    op.create_table(
        "validation_reports",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("validation_status", sa.Text(), nullable=False),
        sa.Column("defect_count", sa.Integer(), nullable=False),
        sa.Column("normalized_payload_json", JSONB, nullable=False),
        sa.Column("report_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_validation_reports_source_created_at", "validation_reports", ["source_type", "source_id", "created_at"])

    op.create_table(
        "classification_records",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("queue_item_id", UUID, sa.ForeignKey("agent_queue_items.id"), nullable=True),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("discipline", sa.Text(), nullable=False),
        sa.Column("aliases_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("search_terms_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "review_cases",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("source_entity_type", sa.Text(), nullable=False),
        sa.Column("source_entity_id", UUID, nullable=False),
        sa.Column("current_stage", sa.Text(), nullable=False),
        sa.Column("owner_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("escalation_level", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "publication_jobs",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("pack_id", UUID, sa.ForeignKey("publication_packs.id"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("requested_by", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("artifact_manifest_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "control_exceptions",
        sa.Column("id", UUID, primary_key=True, nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", UUID, nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.execute(
        """
        CREATE MATERIALIZED VIEW published_symbol_views AS
        SELECT
            gs.id AS symbol_id,
            pp.id AS page_id,
            pk.id AS pack_id,
            gs.slug AS slug,
            gs.canonical_name AS canonical_name,
            gs.category AS category,
            gs.discipline AS discipline,
            sr.revision_label AS revision_label,
            pp.effective_date AS effective_date,
            pp.page_code AS current_page_code,
            pk.pack_code AS current_pack_code,
            FALSE::boolean AS export_available
        FROM published_pages pp
        JOIN publication_packs pk ON pk.id = pp.pack_id
        JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
        JOIN governed_symbols gs ON gs.id = sr.symbol_id;
        """
    )
    op.create_index("ix_published_symbol_views_symbol_page_pack", "published_symbol_views", ["symbol_id", "page_id", "pack_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_published_symbol_views_symbol_page_pack", table_name="published_symbol_views")
    op.execute("DROP MATERIALIZED VIEW published_symbol_views")
    op.drop_table("control_exceptions")
    op.drop_table("publication_jobs")
    op.drop_table("review_cases")
    op.drop_table("classification_records")
    op.drop_index("ix_validation_reports_source_created_at", table_name="validation_reports")
    op.drop_table("validation_reports")
    op.drop_index("ix_provenance_assessments_intake_assessed_at", table_name="provenance_assessments")
    op.drop_table("provenance_assessments")
    op.drop_index("ix_intake_records_status_eligibility_created_at", table_name="intake_records")
    op.drop_table("intake_records")
    op.drop_index("ix_agent_output_artifacts_queue_type_created_at", table_name="agent_output_artifacts")
    op.drop_table("agent_output_artifacts")
    op.drop_index("ix_agent_runs_queue_item_started_at", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_agent_queue_items_agent_status_priority_created_at", table_name="agent_queue_items")
    op.drop_table("agent_queue_items")
    op.drop_index("uq_agent_definitions_slug", table_name="agent_definitions")
    op.drop_table("agent_definitions")
    op.drop_table("clarification_links")
    op.drop_index("ix_clarification_records_external_submitter_created_at", table_name="clarification_records")
    op.drop_index("ix_clarification_records_symbol_page_created_at", table_name="clarification_records")
    op.drop_table("clarification_records")
    op.drop_index("ix_impacted_page_links_change_request_published_page", table_name="impacted_page_links")
    op.drop_table("impacted_page_links")
    op.drop_index("ix_pack_entries_pack_sort_order", table_name="pack_entries")
    op.drop_index("uq_pack_entries_pack_revision_page", table_name="pack_entries")
    op.drop_table("pack_entries")
    op.drop_index("ix_review_decisions_change_request_created_at", table_name="review_decisions")
    op.drop_table("review_decisions")
    op.drop_index("ix_change_requests_base_revision_created_at", table_name="change_requests")
    op.drop_index("ix_change_requests_proposed_revision_status_created_at", table_name="change_requests")
    op.drop_index("ix_change_requests_status_priority_due_date", table_name="change_requests")
    op.drop_table("change_requests")
    op.drop_index("uq_published_pages_page_code", table_name="published_pages")
    op.drop_table("published_pages")
    op.drop_index("uq_publication_packs_pack_code", table_name="publication_packs")
    op.drop_table("publication_packs")
    op.drop_index("ix_symbol_standard_links_revision_standard", table_name="symbol_standard_links")
    op.drop_index("uq_symbol_standard_links_revision_standard_relationship_clause", table_name="symbol_standard_links")
    op.drop_table("symbol_standard_links")
    op.drop_index("ix_source_package_entries_revision_package", table_name="source_package_entries")
    op.drop_index("ix_source_package_entries_package_sort_order", table_name="source_package_entries")
    op.drop_index("uq_source_package_entries_package_revision", table_name="source_package_entries")
    op.drop_table("source_package_entries")
    op.drop_index("ix_standard_versions_standard_effective_date", table_name="standard_versions")
    op.drop_index("uq_standard_versions_standard_version_label", table_name="standard_versions")
    op.drop_table("standard_versions")
    op.drop_index("uq_standards_standard_code", table_name="standards")
    op.drop_table("standards")
    op.drop_index("uq_source_packages_package_code", table_name="source_packages")
    op.drop_table("source_packages")
    op.drop_constraint("fk_governed_symbols_current_revision_id_symbol_revisions", "governed_symbols", type_="foreignkey")
    op.drop_index("ix_symbol_revisions_symbol_created_at", table_name="symbol_revisions")
    op.drop_index("uq_symbol_revisions_symbol_revision_label", table_name="symbol_revisions")
    op.drop_table("symbol_revisions")
    op.drop_index("uq_external_identities_email_lower", table_name="external_identities")
    op.drop_table("external_identities")
    op.drop_table("audit_events")
    op.drop_index("uq_attachments_object_key", table_name="attachments")
    op.drop_table("attachments")
    op.drop_index("uq_governed_symbols_slug", table_name="governed_symbols")
    op.drop_table("governed_symbols")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
