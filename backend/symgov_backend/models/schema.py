from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", text("lower(email)"), unique=True),
        Index("uq_users_display_name_lower", text("lower(display_name)"), unique=True),
        Index("ix_users_deleted_display_name", "deleted_at", "display_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    pin_hash: Mapped[str] = mapped_column(Text, nullable=False)
    pin_set_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    must_change_pin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        CheckConstraint("role in ('admin', 'integrator', 'submitter', 'reviewer')", name="ck_user_roles_role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (Index("uq_user_sessions_token_hash", "token_hash", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"
    __table_args__ = (
        CheckConstraint("tier in ('free', 'plus')", name="ck_user_subscriptions_tier"),
        CheckConstraint(
            "(tier = 'free' and expires_on is null and is_protected = false) or "
            "(tier = 'plus' and ((is_protected = true and expires_on is null) or expires_on is not null))",
            name="ck_user_subscriptions_tier_expiry",
        ),
        CheckConstraint("expires_on is null or expires_on > started_on", name="ck_user_subscriptions_dates"),
        CheckConstraint("anchor_day between 1 and 31", name="ck_user_subscriptions_anchor_day"),
        Index("ix_user_subscriptions_tier_expiry", "tier", "expires_on"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tier: Mapped[str] = mapped_column(Text, nullable=False)
    started_on: Mapped[object] = mapped_column(Date, nullable=False)
    expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    is_protected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class SubscriptionEvent(Base):
    __tablename__ = "subscription_events"
    __table_args__ = (
        CheckConstraint(
            "action in ('created', 'upgraded', 'adjusted', 'cancelled', 'expired', 'user_removed', 'owner_repaired')",
            name="ck_subscription_events_action",
        ),
        CheckConstraint("origin in ('admin', 'self_service', 'system', 'expiry')", name="ck_subscription_events_origin"),
        Index("ix_subscription_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'system'"))
    previous_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_tier: Mapped[str] = mapped_column(Text, nullable=False)
    previous_expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    new_expires_on: Mapped[object | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailOutbox(Base):
    __tablename__ = "email_outbox"
    __table_args__ = (
        CheckConstraint("status in ('pending', 'sent')", name="ck_email_outbox_status"),
        CheckConstraint("recipient_kind in ('customer', 'admin')", name="ck_email_outbox_recipient_kind"),
        Index("uq_email_outbox_event_recipient", "subscription_event_id", "recipient_kind", unique=True),
        Index("ix_email_outbox_pending", "status", "next_attempt_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscription_events.id", ondelete="CASCADE"), nullable=False
    )
    recipient_kind: Mapped[str] = mapped_column(Text, nullable=False)
    to_email: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CatalogFavourite(Base):
    __tablename__ = "catalog_favourites"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("governed_symbols.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class CatalogApiKey(Base):
    __tablename__ = "catalog_api_keys"
    __table_args__ = (
        CheckConstraint("status in ('active', 'disabled', 'revoked')", name="status"),
        Index("uq_catalog_api_keys_key_hash", "key_hash", unique=True),
        Index("ix_catalog_api_keys_key_prefix", "key_prefix"),
        Index("ix_catalog_api_keys_customer_integration", "customer_name", "integration_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_name: Mapped[str] = mapped_column(Text, nullable=False)
    integration_name: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    contact_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_origins_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CatalogApiUsageEvent(Base):
    __tablename__ = "catalog_api_usage_events"
    __table_args__ = (
        Index("ix_catalog_api_usage_events_api_key_created", "api_key_id", "created_at"),
        Index("ix_catalog_api_usage_events_customer_created", "customer_name_snapshot", "integration_name_snapshot", "created_at"),
        Index("ix_catalog_api_usage_events_route_created", "route_name", "status_code", "created_at"),
        Index("ix_catalog_api_usage_events_symbol_created", "symbol_ref", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("catalog_api_keys.id", ondelete="CASCADE"), nullable=False)
    customer_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    integration_name_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    scope_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    route_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ed_query_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class LLMUsageEvent(Base):
    """Authoritative, append-only record of one sanitized LLM attempt."""

    __tablename__ = "llm_usage_events"
    __table_args__ = (
        CheckConstraint("environment in ('development', 'test', 'staging', 'production')", name="llm_usage_events_environment"),
        CheckConstraint("use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit')", name="llm_usage_events_use_case"),
        CheckConstraint("service_name in ('symgov-api', 'libby', 'vlad')", name="llm_usage_events_service_name"),
        CheckConstraint("agent_slug is null or agent_slug in ('libby', 'vlad', 'ed')", name="llm_usage_events_agent_slug"),
        CheckConstraint("provider in ('openrouter', 'google', 'ollama')", name="llm_usage_events_provider"),
        CheckConstraint("request_kind in ('text', 'vision', 'image_generation')", name="llm_usage_events_request_kind"),
        CheckConstraint("status in ('succeeded', 'failed', 'timed_out', 'cancelled')", name="llm_usage_events_status"),
        CheckConstraint("cost_currency = 'USD'", name="llm_usage_events_cost_currency"),
        CheckConstraint("cost_basis in ('provider_reported', 'price_snapshot', 'local_policy', 'estimated', 'unknown')", name="llm_usage_events_cost_basis"),
        CheckConstraint("initiator_kind in ('user', 'api_key', 'admin', 'scheduled_worker', 'system')", name="llm_usage_events_initiator_kind"),
        CheckConstraint("attempt_number >= 1 and attempt_number <= 10000", name="llm_usage_events_attempt_number"),
        CheckConstraint("latency_ms is null or (latency_ms >= 0 and latency_ms <= 604800000)", name="llm_usage_events_latency_ms"),
        CheckConstraint("provider_reported_cost_usd is null or (provider_reported_cost_usd >= 0 and provider_reported_cost_usd <= 1000000)", name="llm_usage_events_provider_cost"),
        CheckConstraint("calculated_cost_usd is null or (calculated_cost_usd >= 0 and calculated_cost_usd <= 1000000)", name="llm_usage_events_calculated_cost"),
        CheckConstraint(
            "(cost_basis = 'provider_reported' and provider_reported_cost_usd is not null and calculated_cost_usd is null and pricing_version is null) or "
            "(cost_basis in ('price_snapshot', 'local_policy', 'estimated') and provider_reported_cost_usd is null and calculated_cost_usd is not null and pricing_version is not null) or "
            "(cost_basis = 'unknown' and provider_reported_cost_usd is null and calculated_cost_usd is null and pricing_version is null)",
            name="llm_usage_events_cost_provenance",
        ),
        CheckConstraint("(status = 'succeeded' and error_class is null and error_code is null) or (status <> 'succeeded' and (error_class is not null or error_code is not null))", name="llm_usage_events_status_errors"),
        *(
            CheckConstraint(f"{field} is null or ({field} >= 0 and {field} <= 1000000000000)", name=f"llm_usage_events_{field}")
            for field in (
                "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "reasoning_tokens", "image_input_units", "image_output_units",
            )
        ),
        CheckConstraint("jsonb_typeof(other_usage_json) = 'object'", name="llm_usage_events_other_usage_object"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="llm_usage_events_metadata_object"),
        UniqueConstraint("trace_id", "observation_id", name="llm_usage_events_trace_observation"),
        Index("ix_llm_usage_events_occurred_at_utc", "occurred_at_utc"),
        Index("ix_llm_usage_events_provider_occurred", "provider", "occurred_at_utc"),
        Index("ix_llm_usage_events_provider_model_occurred", "provider", "resolved_model", "occurred_at_utc"),
        Index("ix_llm_usage_events_use_case_occurred", "use_case", "occurred_at_utc"),
        Index("ix_llm_usage_events_agent_slug_occurred", "agent_slug", "occurred_at_utc"),
        Index("ix_llm_usage_events_feature_occurred", "feature", "occurred_at_utc"),
        Index("ix_llm_usage_events_trace_id", "trace_id"),
        Index("ix_llm_usage_events_trace_attempt", "trace_id", "attempt_number"),
        Index("ix_llm_usage_events_initiator_occurred", "initiator_pseudonym", "occurred_at_utc"),
        Index("ix_llm_usage_events_symbol_display_occurred", "symbol_display_id", "occurred_at_utc"),
        Index("ix_llm_usage_events_queue_item_id", "queue_item_id"),
        Index("ix_llm_usage_events_agent_run_id", "agent_run_id"),
        Index("ix_llm_usage_events_review_case_id", "review_case_id"),
        Index("ix_llm_usage_events_intake_record_id", "intake_record_id"),
        Index("ix_llm_usage_events_source_package_id", "source_package_id"),
        Index("ix_llm_usage_events_symbol_id", "symbol_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    occurred_at_utc: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    observation_id: Mapped[str] = mapped_column(Text, nullable=False)
    use_case: Mapped[str] = mapped_column(Text, nullable=False)
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    agent_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    requested_model: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_model: Mapped[str] = mapped_column(Text, nullable=False)
    request_kind: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_currency: Mapped[str] = mapped_column(Text, nullable=False)
    cost_basis: Mapped[str] = mapped_column(Text, nullable=False)
    provider_reported_cost_usd: Mapped[object | None] = mapped_column(Numeric(20, 9), nullable=True)
    calculated_cost_usd: Mapped[object | None] = mapped_column(Numeric(20, 9), nullable=True)
    pricing_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_write_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_input_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    image_output_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    other_usage_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    intake_record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_package_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    symbol_display_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    release: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiator_kind: Mapped[str] = mapped_column(Text, nullable=False)
    initiator_pseudonym: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    recorded_at_utc: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


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
            "(submitted_by is not null)::int + (external_submitter_id is not null)::int + (catalog_api_key_id is not null)::int = 1",
            name="exactly_one_submitter",
        ),
        Index("ix_clarification_records_symbol_page_created_at", "symbol_id", "published_page_id", "created_at"),
        Index("ix_clarification_records_external_submitter_created_at", "external_submitter_id", "created_at"),
        Index("ix_clarification_records_catalog_api_key_created_at", "catalog_api_key_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    published_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    external_submitter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("external_identities.id"), nullable=True)
    catalog_api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("catalog_api_keys.id"), nullable=True)
    context_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
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


class AgentFeedbackEvent(Base):
    __tablename__ = "agent_feedback_events"
    __table_args__ = (
        Index("ix_agent_feedback_events_agent_created", "agent_slug", "created_at"),
        Index("ix_agent_feedback_events_source", "source_entity_type", "source_entity_id", "created_at"),
        Index("ix_agent_feedback_events_type_created", "feedback_type", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_slug: Mapped[str] = mapped_column(Text, nullable=False)
    feedback_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    original_value_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    corrected_value_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    applied_to_rules_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_to_prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class ScottSourceDiscoverySite(Base):
    __tablename__ = "scott_source_discovery_sites"
    __table_args__ = (
        Index("uq_scott_source_discovery_sites_domain", text("lower(domain)"), unique=True),
        Index("ix_scott_source_discovery_sites_status_score_seen", "status", "relevance_score", "last_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    process: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    include_next_run: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    requires_auth: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    auth_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'no_auth'"))
    auth_secret_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol_formats_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_session_queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)


class HannahSymbolCurationState(Base):
    __tablename__ = "hannah_symbol_curation_states"
    __table_args__ = (
        Index("uq_hannah_symbol_curation_states_symbol", "symbol_id", unique=True),
        Index("ix_hannah_symbol_curation_states_status_attempt", "status", "last_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_attempt_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class HannahPhotoCandidate(Base):
    __tablename__ = "hannah_photo_candidates"
    __table_args__ = (
        Index("ix_hannah_photo_candidates_symbol_status_score", "symbol_id", "status", "relevance_score"),
        Index("ix_hannah_photo_candidates_last_seen", "last_seen_at"),
        Index("uq_hannah_photo_candidates_image_symbol", "symbol_id", "image_url", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=False)
    symbol_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("symbol_revisions.id"), nullable=True)
    published_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=True)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_status: Mapped[str] = mapped_column(Text, nullable=False)
    license_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"), nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class WhitneyMarketIntelligenceReport(Base):
    __tablename__ = "whitney_market_intelligence_reports"
    __table_args__ = (Index("ix_whitney_reports_queue_completed", "queue_item_id", "completed_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    signals_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    recommendations_json: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


class WhitneyDemandSignal(Base):
    __tablename__ = "whitney_demand_signals"
    __table_args__ = (
        Index("ix_whitney_demand_signals_type_score_seen", "signal_type", "demand_score", "last_seen_at"),
        Index("ix_whitney_demand_signals_segment_seen", "market_segment", "last_seen_at"),
        Index("uq_whitney_demand_signals_source", "source_type", "source_ref", "signal_type", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_queue_items.id"), nullable=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whitney_market_intelligence_reports.id"),
        nullable=True,
    )
    symbol_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("governed_symbols.id"), nullable=True)
    published_page_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("published_pages.id"), nullable=True)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    market_segment: Mapped[str | None] = mapped_column(Text, nullable=True)
    discipline: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    demand_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


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


class HannahCurationSourceSite(Base):
    __tablename__ = "hannah_curation_source_sites"
    __table_args__ = (
        Index("uq_hannah_curation_source_sites_domain", text("lower(domain)"), unique=True),
        Index("ix_hannah_curation_source_sites_status_score", "status", "usefulness_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    usefulness_score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default=text("1.00"))
    reliability_score: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default=text("1.00"))
    feedback_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    last_search_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)


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
    rights_disposition: Mapped[str] = mapped_column(Text, nullable=False)
    processing_outcome: Mapped[str] = mapped_column(Text, nullable=False)
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
