"""add promotion_requests / promotion_request_decisions (Stage 7 WP7.2)

Revision ID: 20260902_0036
Revises: 20260902_0035
Create Date: 2026-09-02 01:00:00.000000

Per the programme plan §13 and the Stage 7 plan's §4 decisions (Q1-Q3):

- `promotion_requests` is the dedicated organization-side submission record
  for "promote this organization-approved revision to public" -- per I-10,
  it does not overload public `SymbolRevision.lifecycle_state` or public
  `ReviewCase`. It snapshots the organization-approved revision at
  submission time (FR-PUB-002/§13 task 2). A DB-level unique partial index
  enforces one active (non-terminal) request per governed symbol (Q3),
  mirroring `OrganizationSymbolReviewSubmission`'s own
  `uq_organization_symbol_review_submissions_active_revision` pattern.
  `review_case_id` is nullable and populated only once a later work package
  (WP7.3, per Q1) opens the adapted `ReviewCase` that drives the request to
  `execute_publication_handoff`; WP7.2 never sets it.
- `promotion_request_decisions` is an append-only decision/transition log
  (one row per state transition, not a 1:1 submission/decision pair like
  Stage 5's organization review, since a promotion request can move through
  several transitions over its lifetime: submitted -> triage -> in_review ->
  changes_requested -> in_review -> accepted/rejected, or any open state ->
  withdrawn). WP7.2 only ever writes a `withdrawn` transition (organization
  Admin withdrawal of a still-pending request, §13 task 6); later work
  packages add the reviewer-facing transitions on the same table.

Both tables follow the append-preserving/immutability-trigger and
least-privilege GRANT/REVOKE conventions `20260829_0033` established for
`organization_symbol_review_submissions`/`_decisions`.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260902_0036"
down_revision: Union[str, None] = "20260902_0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promotion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("governed_symbol_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_symbols.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("symbol_revision_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("proposed_metadata_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("sharing_acknowledgment", sa.Boolean(), nullable=False),
        sa.Column("submitted_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("review_cases.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "status",
        "promotion_requests",
        "status in ('submitted', 'triage', 'in_review', 'changes_requested', 'accepted', 'rejected', 'withdrawn')",
    )
    op.create_check_constraint(
        "closed_state",
        "promotion_requests",
        "(status in ('submitted', 'triage', 'in_review', 'changes_requested') and closed_at is null) "
        "or (status in ('accepted', 'rejected', 'withdrawn') and closed_at is not null)",
    )
    op.create_check_constraint(
        "reason",
        "promotion_requests",
        "btrim(reason) <> '' and char_length(reason) <= 2000",
    )
    op.create_check_constraint(
        "sharing_acknowledgment",
        "promotion_requests",
        "sharing_acknowledgment = true",
    )
    op.create_index(
        "uq_promotion_requests_active_symbol",
        "promotion_requests",
        ["governed_symbol_id"],
        unique=True,
        postgresql_where=sa.text("status in ('submitted', 'triage', 'in_review', 'changes_requested')"),
    )
    op.create_index(
        "ix_promotion_requests_organization_symbol",
        "promotion_requests",
        ["organization_id", "governed_symbol_id"],
    )

    op.create_table(
        "promotion_request_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("promotion_request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promotion_requests.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision_code", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("decider_name", sa.Text(), nullable=False),
        sa.Column("decider_role", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_check_constraint(
        "decision_code",
        "promotion_request_decisions",
        "decision_code in ('triage', 'in_review', 'changes_requested', 'accepted', 'rejected', 'withdrawn')",
    )
    op.create_check_constraint(
        "note",
        "promotion_request_decisions",
        "note is null or (btrim(note) <> '' and char_length(note) <= 2000)",
    )
    op.create_index(
        "ix_promotion_request_decisions_request_created_at",
        "promotion_request_decisions",
        ["promotion_request_id", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION protect_promotion_request_identity_and_history()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'promotion request identity and history are append-preserving'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.governed_symbol_id IS DISTINCT FROM NEW.governed_symbol_id
               OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
               OR OLD.symbol_revision_id IS DISTINCT FROM NEW.symbol_revision_id
               OR OLD.proposed_metadata_json IS DISTINCT FROM NEW.proposed_metadata_json
               OR OLD.reason IS DISTINCT FROM NEW.reason
               OR OLD.sharing_acknowledgment IS DISTINCT FROM NEW.sharing_acknowledgment
               OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id
               OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
               OR OLD.trace_id IS DISTINCT FROM NEW.trace_id
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR (OLD.review_case_id IS NOT NULL AND OLD.review_case_id IS DISTINCT FROM NEW.review_case_id)
            THEN
                RAISE EXCEPTION 'promotion request identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_promotion_requests_immutable_identity
        BEFORE UPDATE OR DELETE ON promotion_requests
        FOR EACH ROW EXECUTE FUNCTION protect_promotion_request_identity_and_history()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_promotion_requests_immutable_identity_truncate
        BEFORE TRUNCATE ON promotion_requests
        FOR EACH STATEMENT EXECUTE FUNCTION protect_promotion_request_identity_and_history()
        """
    )

    op.execute(
        """
        CREATE FUNCTION protect_promotion_request_decision_history()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'promotion request decision history is immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_promotion_request_decisions_immutable
        BEFORE UPDATE OR DELETE ON promotion_request_decisions
        FOR EACH ROW EXECUTE FUNCTION protect_promotion_request_decision_history()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_promotion_request_decisions_immutable_truncate
        BEFORE TRUNCATE ON promotion_request_decisions
        FOR EACH STATEMENT EXECUTE FUNCTION protect_promotion_request_decision_history()
        """
    )

    op.execute("GRANT SELECT, INSERT ON promotion_requests TO symgov_app")
    op.execute("GRANT UPDATE (status, closed_at, review_case_id, updated_at) ON promotion_requests TO symgov_app")
    op.execute("REVOKE DELETE, TRUNCATE ON promotion_requests FROM symgov_app")
    op.execute("GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app")
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON promotion_request_decisions FROM symgov_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON promotion_request_decisions FROM symgov_app")
    op.execute("REVOKE SELECT, INSERT, UPDATE ON promotion_requests FROM symgov_app")

    # Dropping the tables also drops the triggers defined on them; the
    # trigger functions are dropped separately afterward since they are not
    # owned by any table.
    op.drop_table("promotion_request_decisions")
    op.drop_table("promotion_requests")

    op.execute("DROP FUNCTION protect_promotion_request_decision_history()")
    op.execute("DROP FUNCTION protect_promotion_request_identity_and_history()")
