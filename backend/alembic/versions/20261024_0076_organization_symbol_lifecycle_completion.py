"""complete the organization-private symbol lifecycle

Revision ID: 20261024_0076
Revises: 20261021_0075
Create Date: 2026-10-24 00:00:00.000000

WP2 of docs/plans/2026-10-24-unified-symbol-submission-review-implementation-plan.md:

- `symbol_revisions.lifecycle_state` gains `rejected`, a terminal state for an
  organization revision whose review was rejected. Changes requested still
  returns the revision to `draft`.
- `organization_symbol_review_submissions.closure_reason` records why a
  submission closed: `decided` (a reviewer decided it) or `withdrawn` (the
  submitter withdrew it, with no decision). Existing closed rows are all
  decided, because the 0033 binding trigger never let a submission close
  without one. The immutability trigger fills in `decided` when a caller
  closes a submission without naming a reason, so code from before this
  migration keeps working on this schema.
- `governed_symbols.pending_revision_id` holds the next revision of a
  published private symbol while it is reviewed, so the published revision
  (`current_revision_id`, which every reader uses) stays live until the new
  one is approved.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261024_0076"
down_revision = "20261021_0075"
branch_labels = None
depends_on = None


LIFECYCLE_WITH_REJECTED = (
    "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn', 'rejected')"
)
LIFECYCLE_WITHOUT_REJECTED = (
    "lifecycle_state in ('draft', 'review', 'approved', 'published', 'deprecated', 'withdrawn')"
)
CLOSURE_REASON_CHECK = (
    "(status = 'active' and closure_reason is null)"
    " or (status = 'closed' and closure_reason in ('decided', 'withdrawn'))"
)
PENDING_REVISION_CHECK = (
    "pending_revision_id is null"
    " or (visibility = 'organization_private' and pending_revision_id is distinct from current_revision_id)"
)

SUBMISSION_HISTORY_WITH_CLOSURE_REASON = """
CREATE OR REPLACE FUNCTION protect_organization_symbol_review_submission_history()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'organization review submission history is append-preserving'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
       OR OLD.governed_symbol_id IS DISTINCT FROM NEW.governed_symbol_id
       OR OLD.symbol_revision_id IS DISTINCT FROM NEW.symbol_revision_id
       OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id
       OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at
       OR OLD.rationale IS DISTINCT FROM NEW.rationale THEN
        RAISE EXCEPTION 'organization review submission identity and history are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'active' AND OLD.closed_at IS NULL
       AND NEW.status = 'closed' AND NEW.closed_at IS NOT NULL THEN
        -- Closing. Code from before 20261024_0076 names no reason, and every
        -- close it makes is a decision.
        NEW.closure_reason := COALESCE(NEW.closure_reason, 'decided');
        RETURN NEW;
    END IF;
    IF OLD.status = 'closed' AND NEW.status = 'closed'
       AND OLD.closed_at IS NOT DISTINCT FROM NEW.closed_at
       AND OLD.closure_reason IS NULL AND NEW.closure_reason = 'decided' THEN
        -- The one-time backfill of rows closed before 20261024_0076.
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'organization review submission identity and history are immutable'
        USING ERRCODE = '55000';
END;
$$
"""

SUBMISSION_HISTORY_BEFORE = """
CREATE OR REPLACE FUNCTION protect_organization_symbol_review_submission_history()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
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

SUBMISSION_BINDING_WITH_WITHDRAWAL = """
CREATE OR REPLACE FUNCTION validate_organization_symbol_review_submission_binding()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    current_submission public.organization_symbol_review_submissions%ROWTYPE;
BEGIN
    SELECT * INTO current_submission
    FROM public.organization_symbol_review_submissions
    WHERE id = NEW.id;
    IF NOT EXISTS (
        SELECT 1
        FROM public.governed_symbols gs
        JOIN public.symbol_revisions sr
          ON sr.id = current_submission.symbol_revision_id
         AND sr.symbol_id = gs.id
        WHERE gs.id = current_submission.governed_symbol_id
          AND gs.owner_organization_id = current_submission.organization_id
    ) THEN
        RAISE EXCEPTION 'organization review submission binding is invalid'
            USING ERRCODE = '23514';
    END IF;
    IF current_submission.status = 'closed'
       AND current_submission.closure_reason = 'decided'
       AND NOT EXISTS (
        SELECT 1
        FROM public.organization_symbol_review_decisions decision
        WHERE decision.submission_id = current_submission.id
          AND decision.organization_id = current_submission.organization_id
          AND decision.governed_symbol_id = current_submission.governed_symbol_id
          AND decision.symbol_revision_id = current_submission.symbol_revision_id
    ) THEN
        RAISE EXCEPTION 'closed organization review submission requires an exact decision'
            USING ERRCODE = '23514';
    END IF;
    IF (current_submission.status = 'active' OR current_submission.closure_reason = 'withdrawn') AND EXISTS (
        SELECT 1
        FROM public.organization_symbol_review_decisions decision
        WHERE decision.submission_id = current_submission.id
    ) THEN
        RAISE EXCEPTION 'decided organization review submission must be closed as decided'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$
"""

SUBMISSION_BINDING_BEFORE = """
CREATE OR REPLACE FUNCTION validate_organization_symbol_review_submission_binding()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    current_submission public.organization_symbol_review_submissions%ROWTYPE;
BEGIN
    SELECT * INTO current_submission
    FROM public.organization_symbol_review_submissions
    WHERE id = NEW.id;
    IF NOT EXISTS (
        SELECT 1
        FROM public.governed_symbols gs
        JOIN public.symbol_revisions sr
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
        FROM public.organization_symbol_review_decisions decision
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
        FROM public.organization_symbol_review_decisions decision
        WHERE decision.submission_id = current_submission.id
    ) THEN
        RAISE EXCEPTION 'decided organization review submission must be closed'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$
"""

PENDING_REVISION_BINDING = """
CREATE FUNCTION validate_governed_symbol_pending_revision()
RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE
    current_symbol public.governed_symbols%ROWTYPE;
BEGIN
    SELECT * INTO current_symbol FROM public.governed_symbols WHERE id = NEW.id;
    IF current_symbol.pending_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM public.symbol_revisions sr
        WHERE sr.id = current_symbol.pending_revision_id
          AND sr.symbol_id = current_symbol.id
    ) THEN
        RAISE EXCEPTION 'pending revision must belong to the same governed symbol'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$
"""


def upgrade() -> None:
    # --- symbol_revisions.lifecycle_state gains 'rejected' ---
    op.drop_constraint("lifecycle_state", "symbol_revisions", type_="check")
    op.create_check_constraint(
        "lifecycle_state", "symbol_revisions", LIFECYCLE_WITH_REJECTED, postgresql_not_valid=True
    )
    op.execute("ALTER TABLE symbol_revisions VALIDATE CONSTRAINT ck_symbol_revisions_lifecycle_state")

    # --- organization_symbol_review_submissions.closure_reason ---
    op.add_column("organization_symbol_review_submissions", sa.Column("closure_reason", sa.Text(), nullable=True))
    op.execute(SUBMISSION_HISTORY_WITH_CLOSURE_REASON)
    op.execute(
        "UPDATE organization_symbol_review_submissions SET closure_reason = 'decided' WHERE status = 'closed'"
    )
    op.create_check_constraint("closure_reason", "organization_symbol_review_submissions", CLOSURE_REASON_CHECK)
    op.execute(SUBMISSION_BINDING_WITH_WITHDRAWAL)
    op.execute("GRANT UPDATE (closure_reason) ON organization_symbol_review_submissions TO symgov_app")

    # --- governed_symbols.pending_revision_id ---
    op.add_column(
        "governed_symbols",
        sa.Column(
            "pending_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", name="fk_governed_symbols_pending_revision_id_symbol_revisions"),
            nullable=True,
        ),
    )
    op.create_check_constraint("pending_revision_scope", "governed_symbols", PENDING_REVISION_CHECK)
    op.execute(PENDING_REVISION_BINDING)
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_governed_symbols_pending_revision
        AFTER INSERT OR UPDATE OF pending_revision_id ON governed_symbols
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_governed_symbol_pending_revision()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM governed_symbols WHERE pending_revision_id IS NOT NULL)
               OR EXISTS (SELECT 1 FROM organization_symbol_review_submissions WHERE closure_reason = 'withdrawn')
               OR EXISTS (SELECT 1 FROM symbol_revisions WHERE lifecycle_state = 'rejected') THEN
                RAISE EXCEPTION 'cannot downgrade 20261024_0076 while pending revisions, withdrawals or rejected revisions exist'
                    USING ERRCODE = '23514';
            END IF;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_governed_symbols_pending_revision ON governed_symbols")
    op.execute("DROP FUNCTION IF EXISTS validate_governed_symbol_pending_revision()")
    op.drop_constraint("pending_revision_scope", "governed_symbols", type_="check")
    op.drop_column("governed_symbols", "pending_revision_id")

    op.execute("REVOKE UPDATE (closure_reason) ON organization_symbol_review_submissions FROM symgov_app")
    op.execute(SUBMISSION_BINDING_BEFORE)
    op.drop_constraint("closure_reason", "organization_symbol_review_submissions", type_="check")
    op.execute(SUBMISSION_HISTORY_BEFORE)
    op.drop_column("organization_symbol_review_submissions", "closure_reason")

    op.drop_constraint("lifecycle_state", "symbol_revisions", type_="check")
    op.create_check_constraint("lifecycle_state", "symbol_revisions", LIFECYCLE_WITHOUT_REJECTED)
