"""add llm_usage_events ledger table after profile subscription outbox

Revision ID: 20260730_0025
Revises: 20260721_0024
Create Date: 2026-07-30 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260730_0025"
down_revision = "20260721_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("observation_id", sa.Text(), nullable=False),
        sa.Column("use_case", sa.Text(), nullable=False),
        sa.Column("service_name", sa.Text(), nullable=False),
        sa.Column("agent_slug", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("requested_model", sa.Text(), nullable=False),
        sa.Column("resolved_model", sa.Text(), nullable=False),
        sa.Column("request_kind", sa.Text(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("cost_currency", sa.Text(), nullable=False),
        sa.Column("cost_basis", sa.Text(), nullable=False),
        sa.Column("provider_reported_cost_usd", sa.Numeric(20, 9), nullable=True),
        sa.Column("calculated_cost_usd", sa.Numeric(20, 9), nullable=True),
        sa.Column("pricing_version", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("image_input_units", sa.BigInteger(), nullable=True),
        sa.Column("image_output_units", sa.BigInteger(), nullable=True),
        sa.Column("other_usage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("queue_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("intake_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_package_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("symbol_display_id", sa.Text(), nullable=True),
        sa.Column("feature", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("release", sa.Text(), nullable=True),
        sa.Column("initiator_kind", sa.Text(), nullable=False),
        sa.Column("initiator_pseudonym", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint("environment in ('development', 'test', 'staging', 'production')", name="llm_usage_events_environment"),
        sa.CheckConstraint("use_case in ('workspace_chat', 'admin_llm_test', 'symbol_property_vision', 'vlad_graphic_edit')", name="llm_usage_events_use_case"),
        sa.CheckConstraint("service_name in ('symgov-api', 'libby', 'vlad')", name="llm_usage_events_service_name"),
        sa.CheckConstraint("agent_slug is null or agent_slug in ('libby', 'vlad', 'ed')", name="llm_usage_events_agent_slug"),
        sa.CheckConstraint("provider in ('openrouter', 'google', 'ollama')", name="llm_usage_events_provider"),
        sa.CheckConstraint("request_kind in ('text', 'vision', 'image_generation')", name="llm_usage_events_request_kind"),
        sa.CheckConstraint("status in ('succeeded', 'failed', 'timed_out', 'cancelled')", name="llm_usage_events_status"),
        sa.CheckConstraint("cost_currency = 'USD'", name="llm_usage_events_cost_currency"),
        sa.CheckConstraint("cost_basis in ('provider_reported', 'price_snapshot', 'local_policy', 'estimated', 'unknown')", name="llm_usage_events_cost_basis"),
        sa.CheckConstraint("initiator_kind in ('user', 'api_key', 'admin', 'scheduled_worker', 'system')", name="llm_usage_events_initiator_kind"),
        sa.CheckConstraint("attempt_number >= 1 and attempt_number <= 10000", name="llm_usage_events_attempt_number"),
        sa.CheckConstraint("latency_ms is null or (latency_ms >= 0 and latency_ms <= 604800000)", name="llm_usage_events_latency_ms"),
        sa.CheckConstraint("provider_reported_cost_usd is null or (provider_reported_cost_usd >= 0 and provider_reported_cost_usd <= 1000000)", name="llm_usage_events_provider_cost"),
        sa.CheckConstraint("calculated_cost_usd is null or (calculated_cost_usd >= 0 and calculated_cost_usd <= 1000000)", name="llm_usage_events_calculated_cost"),
        sa.CheckConstraint(
            "(cost_basis = 'provider_reported' and provider_reported_cost_usd is not null and calculated_cost_usd is null and pricing_version is null) or "
            "(cost_basis in ('price_snapshot', 'local_policy', 'estimated') and provider_reported_cost_usd is null and calculated_cost_usd is not null and pricing_version is not null) or "
            "(cost_basis = 'unknown' and provider_reported_cost_usd is null and calculated_cost_usd is null and pricing_version is null)",
            name="llm_usage_events_cost_provenance",
        ),
        sa.CheckConstraint("(status = 'succeeded' and error_class is null and error_code is null) or (status <> 'succeeded' and (error_class is not null or error_code is not null))", name="llm_usage_events_status_errors"),
        sa.CheckConstraint("input_tokens is null or (input_tokens >= 0 and input_tokens <= 1000000000000)", name="llm_usage_events_input_tokens"),
        sa.CheckConstraint("output_tokens is null or (output_tokens >= 0 and output_tokens <= 1000000000000)", name="llm_usage_events_output_tokens"),
        sa.CheckConstraint("cached_input_tokens is null or (cached_input_tokens >= 0 and cached_input_tokens <= 1000000000000)", name="llm_usage_events_cached_input_tokens"),
        sa.CheckConstraint("cache_write_input_tokens is null or (cache_write_input_tokens >= 0 and cache_write_input_tokens <= 1000000000000)", name="llm_usage_events_cache_write_input_tokens"),
        sa.CheckConstraint("reasoning_tokens is null or (reasoning_tokens >= 0 and reasoning_tokens <= 1000000000000)", name="llm_usage_events_reasoning_tokens"),
        sa.CheckConstraint("image_input_units is null or (image_input_units >= 0 and image_input_units <= 1000000000000)", name="llm_usage_events_image_input_units"),
        sa.CheckConstraint("image_output_units is null or (image_output_units >= 0 and image_output_units <= 1000000000000)", name="llm_usage_events_image_output_units"),
        sa.CheckConstraint("jsonb_typeof(other_usage_json) = 'object'", name="llm_usage_events_other_usage_object"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="llm_usage_events_metadata_object"),
        sa.UniqueConstraint("trace_id", "observation_id", name="llm_usage_events_trace_observation"),
    )

    op.create_index("ix_llm_usage_events_occurred_at_utc", "llm_usage_events", ["occurred_at_utc"])
    op.create_index("ix_llm_usage_events_provider_occurred", "llm_usage_events", ["provider", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_provider_model_occurred", "llm_usage_events", ["provider", "resolved_model", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_use_case_occurred", "llm_usage_events", ["use_case", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_agent_slug_occurred", "llm_usage_events", ["agent_slug", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_feature_occurred", "llm_usage_events", ["feature", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_trace_id", "llm_usage_events", ["trace_id"])
    op.create_index("ix_llm_usage_events_trace_attempt", "llm_usage_events", ["trace_id", "attempt_number"])
    op.create_index("ix_llm_usage_events_initiator_occurred", "llm_usage_events", ["initiator_pseudonym", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_symbol_display_occurred", "llm_usage_events", ["symbol_display_id", "occurred_at_utc"])
    op.create_index("ix_llm_usage_events_queue_item_id", "llm_usage_events", ["queue_item_id"])
    op.create_index("ix_llm_usage_events_agent_run_id", "llm_usage_events", ["agent_run_id"])
    op.create_index("ix_llm_usage_events_review_case_id", "llm_usage_events", ["review_case_id"])
    op.create_index("ix_llm_usage_events_intake_record_id", "llm_usage_events", ["intake_record_id"])
    op.create_index("ix_llm_usage_events_source_package_id", "llm_usage_events", ["source_package_id"])
    op.create_index("ix_llm_usage_events_symbol_id", "llm_usage_events", ["symbol_id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_llm_usage_events_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'llm_usage_events table is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_prevent_llm_usage_events_mutation
        BEFORE UPDATE OR DELETE ON llm_usage_events
        FOR EACH ROW EXECUTE FUNCTION prevent_llm_usage_events_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_prevent_llm_usage_events_mutation ON llm_usage_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_llm_usage_events_mutation()")
    op.drop_table("llm_usage_events")
