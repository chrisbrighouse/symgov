"""add agent_configurations and agent_findings (Stage 10 WP10.1)

Revision ID: 20260905_0044
Revises: 20260904_0043
Create Date: 2026-09-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260905_0044"
down_revision = "20260904_0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("logical_agent_name", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("model_alias", sa.Text(), nullable=True),
        sa.Column("allowed_capabilities_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.CheckConstraint(
            "logical_agent_name in ('organization_steward', 'platform_governance')",
            name="ck_agent_configurations_logical_agent_name",
        ),
        sa.CheckConstraint("scope_type in ('platform', 'organization')", name="ck_agent_configurations_scope_type"),
        sa.CheckConstraint(
            "(scope_type = 'organization' and scope_id is not null) or (scope_type = 'platform' and scope_id is null)",
            name="ck_agent_configurations_scope_id_matches_type",
        ),
        sa.CheckConstraint(
            "model_alias is null or model_alias ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="ck_agent_configurations_model_alias_format",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_capabilities_json) = 'array'",
            name="ck_agent_configurations_allowed_capabilities_array",
        ),
        sa.CheckConstraint(
            "octet_length(convert_to(allowed_capabilities_json::text, 'UTF8')) <= 8192",
            name="ck_agent_configurations_allowed_capabilities_size",
        ),
    )
    op.create_index(
        "uq_agent_configurations_platform_scope",
        "agent_configurations",
        ["logical_agent_name"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'platform'"),
    )
    op.create_index(
        "uq_agent_configurations_org_scope",
        "agent_configurations",
        ["logical_agent_name", "scope_id"],
        unique=True,
        postgresql_where=sa.text("scope_type = 'organization'"),
    )
    # No DELETE grant: configuration rows are disabled in place
    # (`enabled = false`), never hard-deleted, mirroring `organizations`'
    # own soft-state convention rather than `platform_role_assignments`'
    # revoke-by-timestamp shape (there is no equivalent "revoke" concept
    # here -- `enabled` already carries that meaning).
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_configurations TO symgov_app")

    op.create_table(
        "agent_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_config_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_configurations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("finding_type", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("superseded_by_finding_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assignee_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issue_reference", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "finding_type in ("
            "'reviewer_coverage_gap', 'review_backlog_stale', "
            "'project_health_issue', 'symbol_set_health_issue', 'unresolved_reference', "
            "'platform_admin_continuity_risk', 'duplicate_organization_suspected'"
            ")",
            name="ck_agent_findings_finding_type",
        ),
        sa.CheckConstraint("severity in ('low', 'medium', 'high', 'critical')", name="ck_agent_findings_severity"),
        sa.CheckConstraint(
            "status in ('open', 'acknowledged', 'dismissed', 'resolved', 'superseded')",
            name="ck_agent_findings_status",
        ),
        sa.CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_agent_findings_fingerprint_format"),
        sa.CheckConstraint("btrim(summary) <> '' and char_length(summary) <= 2000", name="ck_agent_findings_summary_bounds"),
        sa.CheckConstraint("jsonb_typeof(evidence_json) = 'object'", name="ck_agent_findings_evidence_object"),
        sa.CheckConstraint(
            "octet_length(convert_to(evidence_json::text, 'UTF8')) <= 16384",
            name="ck_agent_findings_evidence_size",
        ),
        sa.CheckConstraint("(acknowledged_at is null) = (acknowledged_by_user_id is null)", name="ck_agent_findings_acknowledged_pair"),
        sa.CheckConstraint("(dismissed_at is null) = (dismissed_by_user_id is null)", name="ck_agent_findings_dismissed_pair"),
        sa.CheckConstraint("(resolved_at is null) = (resolved_by_user_id is null)", name="ck_agent_findings_resolved_pair"),
        sa.CheckConstraint(
            "(status = 'open' and dismissed_at is null and resolved_at is null and superseded_by_finding_id is null) or "
            "(status = 'acknowledged' and acknowledged_at is not null and dismissed_at is null and resolved_at is null and superseded_by_finding_id is null) or "
            "(status = 'dismissed' and dismissed_at is not null) or "
            "(status = 'resolved' and resolved_at is not null) or "
            "(status = 'superseded' and superseded_by_finding_id is not null)",
            name="ck_agent_findings_status_consistency",
        ),
    )
    # Deliberately not a foreign key -- mirroring ContributionEvent.
    # reversed_event_id's own precedent: this table has no immutability
    # trigger to collide with, but self-referential FKs on a row that may
    # itself later be the *target* of another supersession would need
    # careful ON DELETE handling this stage does not yet require, since no
    # retention/purge exists for this table at all yet (see class docstring).
    op.create_foreign_key(
        "fk_agent_findings_superseded_by_finding_id",
        "agent_findings",
        "agent_findings",
        ["superseded_by_finding_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_agent_findings_active_fingerprint",
        "agent_findings",
        ["fingerprint"],
        unique=True,
        postgresql_where=sa.text("status in ('open', 'acknowledged')"),
    )
    op.create_index("ix_agent_findings_agent_config_status", "agent_findings", ["agent_config_id", "status"])
    op.create_index("ix_agent_findings_entity", "agent_findings", ["entity_type", "entity_id"])
    op.create_index("ix_agent_findings_last_seen_at", "agent_findings", ["last_seen_at"])
    # No DELETE grant: no retention/purge policy exists yet for this table
    # (deliberately deferred, see class docstring) -- UPDATE covers the
    # full acknowledge/dismiss/resolve/supersede lifecycle.
    op.execute("GRANT SELECT, INSERT, UPDATE ON agent_findings TO symgov_app")


def downgrade() -> None:
    op.drop_table("agent_findings")
    op.drop_index("uq_agent_configurations_org_scope", table_name="agent_configurations")
    op.drop_index("uq_agent_configurations_platform_scope", table_name="agent_configurations")
    op.drop_table("agent_configurations")
