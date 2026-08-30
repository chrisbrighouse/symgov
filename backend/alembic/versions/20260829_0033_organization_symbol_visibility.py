"""add organization symbol visibility foundation

Revision ID: 20260829_0033
Revises: 20260826_0032
Create Date: 2026-08-29 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260829_0033"
down_revision: Union[str, None] = "20260826_0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column(
        "governed_symbols",
        sa.Column("owner_organization_id", UUID, nullable=True),
    )
    op.add_column(
        "governed_symbols",
        sa.Column(
            "visibility",
            sa.Text(),
            server_default=sa.text("'public'"),
            nullable=False,
        ),
    )
    op.add_column(
        "governed_symbols",
        sa.Column(
            "organization_wide",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_governed_symbols_owner_organization_id",
        "governed_symbols",
        "organizations",
        ["owner_organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_governed_symbols_visibility",
        "governed_symbols",
        "visibility in ('organization_private', 'public')",
    )
    op.create_check_constraint(
        "ck_governed_symbols_organization_wide_scope",
        "governed_symbols",
        "not organization_wide or "
        "(owner_organization_id is not null and visibility = 'public')",
    )
    op.create_index(
        "ix_governed_symbols_owner_visibility_organization_wide",
        "governed_symbols",
        ["owner_organization_id", "visibility", "organization_wide"],
    )

    op.create_table(
        "organization_symbol_review_submissions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "governed_symbol_id",
            UUID,
            sa.ForeignKey("governed_symbols.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "symbol_revision_id",
            UUID,
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "submitted_by_user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submitted_at", TS, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("closed_at", TS, nullable=True),
        sa.CheckConstraint(
            "(status = 'active' and closed_at is null) or "
            "(status = 'closed' and closed_at is not null)",
            name="ck_organization_symbol_review_submissions_status",
        ),
        sa.CheckConstraint(
            "rationale is null or "
            "(btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="ck_organization_symbol_review_submissions_rationale",
        ),
    )
    op.create_index(
        "uq_organization_symbol_review_submissions_active_revision",
        "organization_symbol_review_submissions",
        ["symbol_revision_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_org_symbol_review_submissions_tenant_symbol_revision",
        "organization_symbol_review_submissions",
        ["organization_id", "governed_symbol_id", "symbol_revision_id"],
    )

    op.create_table(
        "organization_symbol_review_decisions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "submission_id",
            UUID,
            sa.ForeignKey(
                "organization_symbol_review_submissions.id", ondelete="RESTRICT"
            ),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "organization_id",
            UUID,
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "governed_symbol_id",
            UUID,
            sa.ForeignKey("governed_symbols.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "symbol_revision_id",
            UUID,
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decided_by_user_id",
            UUID,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("decided_at", TS, nullable=False),
        sa.CheckConstraint(
            "decision in ('approved', 'rejected', 'changes_requested')",
            name="ck_organization_symbol_review_decisions_decision",
        ),
        sa.CheckConstraint(
            "rationale is null or "
            "(btrim(rationale) <> '' and char_length(rationale) <= 2000)",
            name="ck_organization_symbol_review_decisions_rationale",
        ),
    )
    op.create_index(
        "ix_org_symbol_review_decisions_tenant_symbol_revision",
        "organization_symbol_review_decisions",
        ["organization_id", "governed_symbol_id", "symbol_revision_id"],
    )

    op.execute(
        """
        CREATE FUNCTION validate_organization_symbol_review_submission_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            current_submission organization_symbol_review_submissions%ROWTYPE;
        BEGIN
            SELECT * INTO current_submission
            FROM organization_symbol_review_submissions
            WHERE id = NEW.id;
            IF NOT EXISTS (
                SELECT 1
                FROM governed_symbols gs
                JOIN symbol_revisions sr
                  ON sr.id = current_submission.symbol_revision_id
                 AND sr.symbol_id = gs.id
                WHERE gs.id = current_submission.governed_symbol_id
                  AND gs.owner_organization_id = current_submission.organization_id
            ) THEN
                RAISE EXCEPTION 'organization review submission binding is invalid'
                    USING ERRCODE = '23514';
            END IF;
            IF current_submission.status = 'closed' AND NOT EXISTS (
                SELECT 1
                FROM organization_symbol_review_decisions decision
                WHERE decision.submission_id = current_submission.id
                  AND decision.organization_id = current_submission.organization_id
                  AND decision.governed_symbol_id = current_submission.governed_symbol_id
                  AND decision.symbol_revision_id = current_submission.symbol_revision_id
            ) THEN
                RAISE EXCEPTION 'closed organization review submission requires an exact decision'
                    USING ERRCODE = '23514';
            END IF;
            IF current_submission.status = 'active' AND EXISTS (
                SELECT 1
                FROM organization_symbol_review_decisions decision
                WHERE decision.submission_id = current_submission.id
            ) THEN
                RAISE EXCEPTION 'decided organization review submission must be closed'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_organization_symbol_review_submission_binding
        AFTER INSERT OR UPDATE ON organization_symbol_review_submissions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_organization_symbol_review_submission_binding()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_organization_symbol_review_decision_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM organization_symbol_review_submissions submission
                WHERE submission.id = NEW.submission_id
                  AND submission.organization_id = NEW.organization_id
                  AND submission.governed_symbol_id = NEW.governed_symbol_id
                  AND submission.symbol_revision_id = NEW.symbol_revision_id
            ) THEN
                RAISE EXCEPTION 'organization review decision binding is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_organization_symbol_review_decision_binding
        AFTER INSERT ON organization_symbol_review_decisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_organization_symbol_review_decision_binding()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_organization_symbol_review_parent_binding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'governed_symbols' AND EXISTS (
                SELECT 1
                FROM organization_symbol_review_submissions submission
                JOIN symbol_revisions revision
                  ON revision.id = submission.symbol_revision_id
                WHERE submission.governed_symbol_id = NEW.id
                  AND (
                      submission.organization_id IS DISTINCT FROM NEW.owner_organization_id
                      OR revision.symbol_id IS DISTINCT FROM NEW.id
                  )
            ) THEN
                RAISE EXCEPTION 'governed symbol change would rebind organization review history'
                    USING ERRCODE = '23514';
            ELSIF TG_TABLE_NAME = 'symbol_revisions' AND EXISTS (
                SELECT 1
                FROM organization_symbol_review_submissions submission
                JOIN governed_symbols symbol
                  ON symbol.id = submission.governed_symbol_id
                WHERE submission.symbol_revision_id = NEW.id
                  AND (
                      submission.governed_symbol_id IS DISTINCT FROM NEW.symbol_id
                      OR submission.organization_id IS DISTINCT FROM symbol.owner_organization_id
                  )
            ) THEN
                RAISE EXCEPTION 'symbol revision change would rebind organization review history'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_governed_symbols_organization_review_binding
        AFTER UPDATE OF owner_organization_id ON governed_symbols
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_organization_symbol_review_parent_binding()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_symbol_revisions_organization_review_binding
        AFTER UPDATE OF symbol_id ON symbol_revisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_organization_symbol_review_parent_binding()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_governed_symbol_organization_wide_eligibility()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            current_symbol governed_symbols%ROWTYPE;
        BEGIN
            SELECT * INTO current_symbol
            FROM governed_symbols
            WHERE id = NEW.id;
            IF current_symbol.organization_wide AND NOT EXISTS (
                SELECT 1
                FROM symbol_revisions sr
                JOIN organization_symbol_review_decisions decision
                  ON decision.organization_id = current_symbol.owner_organization_id
                 AND decision.governed_symbol_id = current_symbol.id
                 AND decision.symbol_revision_id = sr.id
                 AND decision.decision = 'approved'
                JOIN organization_symbol_review_submissions submission
                  ON submission.id = decision.submission_id
                 AND submission.status = 'closed'
                WHERE sr.id = current_symbol.current_revision_id
                  AND sr.symbol_id = current_symbol.id
            ) THEN
                RAISE EXCEPTION 'organization-wide symbol requires current approved organization revision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_governed_symbols_organization_wide_eligibility
        AFTER INSERT OR UPDATE OF owner_organization_id, visibility,
            organization_wide, current_revision_id ON governed_symbols
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_governed_symbol_organization_wide_eligibility()
        """
    )

    op.execute(
        """
        CREATE FUNCTION protect_organization_symbol_review_submission_history()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'organization review submission history is append-preserving'
                    USING ERRCODE = '55000';
            END IF;
            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
               OR OLD.governed_symbol_id IS DISTINCT FROM NEW.governed_symbol_id
               OR OLD.symbol_revision_id IS DISTINCT FROM NEW.symbol_revision_id
               OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id
               OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
               OR OLD.rationale IS DISTINCT FROM NEW.rationale
               OR NOT (
                   OLD.status = 'active'
                   AND OLD.closed_at IS NULL
                   AND NEW.status = 'closed'
                   AND NEW.closed_at IS NOT NULL
               ) THEN
                RAISE EXCEPTION 'organization review submission identity and history are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_symbol_review_submissions_immutable
        BEFORE UPDATE OR DELETE ON organization_symbol_review_submissions
        FOR EACH ROW EXECUTE FUNCTION protect_organization_symbol_review_submission_history()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_organization_symbol_review_decision_history()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'organization review decision history is immutable'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_symbol_review_decisions_immutable
        BEFORE UPDATE OR DELETE ON organization_symbol_review_decisions
        FOR EACH ROW EXECUTE FUNCTION protect_organization_symbol_review_decision_history()
        """
    )

    op.execute(
        """
        CREATE VIEW active_public_symbol_projections AS
        SELECT
            gs.id AS governed_symbol_id,
            gs.catalog_symbol_id,
            sr.id AS symbol_revision_id,
            page.id AS published_page_id,
            entry.id AS pack_entry_id,
            pack.id AS publication_pack_id,
            gs.owner_organization_id,
            gs.visibility,
            gs.organization_wide
        FROM governed_symbols gs
        JOIN symbol_revisions sr
          ON sr.id = gs.current_revision_id
         AND sr.symbol_id = gs.id
        JOIN published_pages page
          ON page.current_symbol_revision_id = sr.id
        JOIN pack_entries entry
          ON entry.symbol_revision_id = sr.id
         AND entry.published_page_id = page.id
         AND entry.pack_id = page.pack_id
        JOIN publication_packs pack
          ON pack.id = page.pack_id
        WHERE gs.visibility = 'public'
          AND sr.lifecycle_state = 'published'
          AND pack.audience = 'public'
          AND pack.status = 'published'
        """
    )

    op.execute(
        "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app"
    )
    op.execute(
        "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app"
    )
    op.execute(
        "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE ON organization_symbol_review_submissions FROM symgov_app"
    )
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON organization_symbol_review_decisions FROM symgov_app"
    )
    op.execute("GRANT SELECT ON active_public_symbol_projections TO symgov_app")


def downgrade() -> None:
    op.execute(
        "LOCK TABLE organization_symbol_review_decisions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        "LOCK TABLE organization_symbol_review_submissions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("LOCK TABLE governed_symbols IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE symbol_revisions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM organization_symbol_review_decisions)
               OR EXISTS (SELECT 1 FROM organization_symbol_review_submissions)
               OR EXISTS (
                   SELECT 1
                   FROM governed_symbols
                   WHERE owner_organization_id IS NOT NULL
                      OR visibility <> 'public'
                      OR organization_wide
               ) THEN
                RAISE EXCEPTION 'cannot downgrade organization symbol visibility while Stage 5 data exists'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )

    op.execute("REVOKE SELECT ON active_public_symbol_projections FROM symgov_app")
    op.execute("DROP VIEW active_public_symbol_projections")
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE ON organization_symbol_review_submissions FROM symgov_app"
    )
    op.execute(
        "REVOKE SELECT, INSERT ON organization_symbol_review_decisions FROM symgov_app"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_governed_symbols_organization_wide_eligibility ON governed_symbols"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_symbol_revisions_organization_review_binding "
        "ON symbol_revisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_governed_symbols_organization_review_binding "
        "ON governed_symbols"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_organization_symbol_review_decisions_immutable "
        "ON organization_symbol_review_decisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_organization_symbol_review_decision_binding "
        "ON organization_symbol_review_decisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_organization_symbol_review_submissions_immutable "
        "ON organization_symbol_review_submissions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_organization_symbol_review_submission_binding "
        "ON organization_symbol_review_submissions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_governed_symbol_organization_wide_eligibility()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_organization_symbol_review_parent_binding()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS protect_organization_symbol_review_decision_history()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_organization_symbol_review_decision_binding()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS protect_organization_symbol_review_submission_history()"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_organization_symbol_review_submission_binding()"
    )
    op.drop_index(
        "ix_org_symbol_review_decisions_tenant_symbol_revision",
        table_name="organization_symbol_review_decisions",
    )
    op.drop_table("organization_symbol_review_decisions")
    op.drop_index(
        "ix_org_symbol_review_submissions_tenant_symbol_revision",
        table_name="organization_symbol_review_submissions",
    )
    op.drop_index(
        "uq_organization_symbol_review_submissions_active_revision",
        table_name="organization_symbol_review_submissions",
    )
    op.drop_table("organization_symbol_review_submissions")
    op.drop_index(
        "ix_governed_symbols_owner_visibility_organization_wide",
        table_name="governed_symbols",
    )
    op.drop_constraint(
        "ck_governed_symbols_organization_wide_scope",
        "governed_symbols",
        type_="check",
    )
    op.drop_constraint(
        "ck_governed_symbols_visibility", "governed_symbols", type_="check"
    )
    op.drop_constraint(
        "fk_governed_symbols_owner_organization_id",
        "governed_symbols",
        type_="foreignkey",
    )
    op.drop_column("governed_symbols", "organization_wide")
    op.drop_column("governed_symbols", "visibility")
    op.drop_column("governed_symbols", "owner_organization_id")
