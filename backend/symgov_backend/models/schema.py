from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role in ('admin', 'standards_owner', 'methods_lead', 'qa_admin', 'reviewer')",
            name="users_role",
        ),
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class GovernedSymbol(Base):
    __tablename__ = "governed_symbols"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    discipline: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_type: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        CheckConstraint(
            "identity_type in ('engineer', 'contractor', 'submitter', 'external_reviewer', 'other')",
            name="external_identities_identity_type",
        ),
        CheckConstraint("status in ('active', 'inactive')", name="external_identities_status"),
        Index(
            "uq_external_identities_email_lower",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email is not null"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolRevision(Base):
    __tablename__ = "symbol_revisions"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated')",
            name="symbol_revisions_lifecycle_state",
        ),
        Index("uq_symbol_revisions_symbol_revision_label", "symbol_id", "revision_label", unique=True),
        Index("ix_symbol_revisions_symbol_created_at", "symbol_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    revision_label: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePackage(Base):
    __tablename__ = "source_packages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class Standard(Base):
    __tablename__ = "standards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    standard_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    issuing_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class StandardVersion(Base):
    __tablename__ = "standard_versions"
    __table_args__ = (
        Index("uq_standard_versions_standard_version_label", "standard_id", "version_label", unique=True),
        Index("ix_standard_versions_standard_effective_date", "standard_id", "effective_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    standard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False)
    version_label: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePackageEntry(Base):
    __tablename__ = "source_package_entries"
    __table_args__ = (
        Index("uq_source_package_entries_package_revision", "source_package_id", "symbol_revision_id", unique=True),
        Index("ix_source_package_entries_package_sort_order", "source_package_id", "sort_order"),
        Index("ix_source_package_entries_revision_package", "symbol_revision_id", "source_package_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("source_packages.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SymbolStandardLink(Base):
    __tablename__ = "symbol_standard_links"
    __table_args__ = (
        Index(
            "uq_symbol_standard_links_revision_standard_relationship_clause",
            "symbol_revision_id",
            "standard_version_id",
            "relationship_type",
            "clause_reference",
            unique=True,
        ),
        Index("ix_symbol_standard_links_revision_standard", "symbol_revision_id", "standard_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    standard_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("standard_versions.id"), nullable=False)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    clause_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationPack(Base):
    __tablename__ = "publication_packs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[object] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PublishedPage(Base):
    __tablename__ = "published_pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    current_symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    effective_date: Mapped[object] = mapped_column(Date, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    __table_args__ = (
        Index("ix_change_requests_status_priority_due_date", "status", "priority", "due_date"),
        Index("ix_change_requests_proposed_revision_status_created_at", "proposed_revision_id", "status", "created_at"),
        Index("ix_change_requests_base_revision_created_at", "base_revision_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    proposed_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    base_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    revision_delta: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    due_date: Mapped[object | None] = mapped_column(Date, nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_change_request_created_at", "change_request_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PackEntry(Base):
    __tablename__ = "pack_entries"
    __table_args__ = (
        Index("uq_pack_entries_pack_revision_page", "pack_id", "symbol_revision_id", "published_page_id", unique=True),
        Index("ix_pack_entries_pack_sort_order", "pack_id", "sort_order"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ImpactedPageLink(Base):
    __tablename__ = "impacted_page_links"
    __table_args__ = (Index("ix_impacted_page_links_change_request_published_page", "change_request_id", "published_page_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    impact_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClarificationRecord(Base):
    __tablename__ = "clarification_records"
    __table_args__ = (
        CheckConstraint(
            "((submitted_by is not null and external_submitter_id is null) or (submitted_by is null and external_submitter_id is not null))",
            name="clarification_records_one_submitter",
        ),
        Index("ix_clarification_records_symbol_page_created_at", "symbol_id", "published_page_id", "created_at"),
        Index("ix_clarification_records_external_submitter_created_at", "external_submitter_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    external_submitter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("external_identities.id"), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClarificationLink(Base):
    __tablename__ = "clarification_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    clarification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clarification_records.id"), nullable=False)
    change_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False)
    linked_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    queue_family: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentQueueItem(Base):
    __tablename__ = "agent_queue_items"
    __table_args__ = (Index("ix_agent_queue_items_agent_status_priority_created_at", "agent_id", "status", "priority", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_queue_item_started_at", "queue_item_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    tool_trace_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentOutputArtifact(Base):
    __tablename__ = "agent_output_artifacts"
    __table_args__ = (Index("ix_agent_output_artifacts_queue_type_created_at", "queue_item_id", "artifact_type", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class IntakeRecord(Base):
    __tablename__ = "intake_records"
    __table_args__ = (Index("ix_intake_records_status_eligibility_created_at", "intake_status", "eligibility_status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    submitter: Mapped[str] = mapped_column(Text, nullable=False)
    submission_kind: Mapped[str] = mapped_column(Text, nullable=False)
    intake_status: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_status: Mapped[str] = mapped_column(Text, nullable=False)
    source_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("source_packages.id"), nullable=True)
    raw_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_submission_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    routing_recommendation_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ProvenanceAssessment(Base):
    __tablename__ = "provenance_assessments"
    __table_args__ = (Index("ix_provenance_assessments_intake_assessed_at", "intake_record_id", "assessed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    intake_record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_records.id"), nullable=False)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assessed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ValidationReport(Base):
    __tablename__ = "validation_reports"
    __table_args__ = (Index("ix_validation_reports_source_created_at", "source_type", "source_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False)
    defect_count: Mapped[int] = mapped_column(Integer, nullable=False)
    normalized_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationRecord(Base):
    __tablename__ = "classification_records"
    __table_args__ = (
        Index("ix_classification_records_symbol_status_created_at", "symbol_key", "status", "created_at"),
        Index("ix_classification_records_review_case_created_at", "review_case_id", "created_at"),
        Index("ix_classification_records_validation_report_created_at", "validation_report_id", "created_at"),
        Index("ix_classification_records_provenance_assessment_created_at", "provenance_assessment_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    intake_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("intake_records.id"), nullable=True)
    validation_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("validation_reports.id"), nullable=True)
    provenance_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("provenance_assessments.id"), nullable=True)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=True)
    origin_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"), nullable=True)
    origin_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin_batch_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=True)
    symbol_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_region_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'current'"))
    classification_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'provisional'"))
    supersedes_classification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classification_records.id"),
        nullable=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    discipline: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_family: Mapped[str | None] = mapped_column(Text, nullable=True)
    process_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_equipment_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    standards_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    library_provenance_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    search_terms_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    source_refs_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    taxonomy_terms_created_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    review_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    libby_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class ReviewCase(Base):
    __tablename__ = "review_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    current_stage: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    escalation_level: Mapped[str] = mapped_column(Text, nullable=False)
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HumanReviewDecision(Base):
    __tablename__ = "human_review_decisions"
    __table_args__ = (Index("ix_human_review_decisions_case_created_at", "review_case_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    decision_code: Mapped[str] = mapped_column(Text, nullable=False)
    decision_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decider_name: Mapped[str] = mapped_column(Text, nullable=False)
    decider_role: Mapped[str] = mapped_column(Text, nullable=False)
    from_stage: Mapped[str] = mapped_column(Text, nullable=False)
    to_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewCaseAction(Base):
    __tablename__ = "review_case_actions"
    __table_args__ = (
        Index("ix_review_case_actions_case_status_created_at", "review_case_id", "action_status", "created_at"),
        Index("ix_review_case_actions_decision_created_at", "decision_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_review_decisions.id"), nullable=True)
    action_code: Mapped[str] = mapped_column(Text, nullable=False)
    action_status: Mapped[str] = mapped_column(Text, nullable=False)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    target_agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_by_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewSplitItem(Base):
    __tablename__ = "review_split_items"
    __table_args__ = (
        Index("uq_review_split_items_case_child", "review_case_id", "child_key", unique=True),
        Index("ix_review_split_items_case_status", "review_case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    child_key: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_symbol_id: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_symbol_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_file_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'awaiting_decision'"))
    latest_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_review_decisions.id"), nullable=True)
    latest_action_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_case_actions.id"), nullable=True)
    downstream_agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    downstream_queue_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewSymbolProperty(Base):
    __tablename__ = "review_symbol_properties"
    __table_args__ = (
        Index("uq_review_symbol_properties_case_key", "review_case_id", "symbol_record_key", unique=True),
        Index("ix_review_symbol_properties_split_item", "review_split_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("review_cases.id"), nullable=False)
    review_split_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("review_split_items.id"), nullable=True)
    symbol_record_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    discipline: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'agent_initial'"))
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewSymbolPropertyOption(Base):
    __tablename__ = "review_symbol_property_options"
    __table_args__ = (
        CheckConstraint("field_name in ('category', 'discipline')", name="review_symbol_property_options_field_name"),
        Index("uq_review_symbol_property_options_field_key", "field_name", "normalized_key", unique=True),
        Index("ix_review_symbol_property_options_field_value", "field_name", "display_value"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    display_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_key: Mapped[str] = mapped_column(Text, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class PublicationJob(Base):
    __tablename__ = "publication_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("publication_packs.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    artifact_manifest_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ControlException(Base):
    __tablename__ = "control_exceptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
