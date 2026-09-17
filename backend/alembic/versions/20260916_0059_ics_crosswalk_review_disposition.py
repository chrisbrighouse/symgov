"""Record who dispositioned an ICS domain crosswalk, and when.

`20260915_0058` created `ics_domain_crosswalks` with a `review_status` that
the runbook expects a human to move to `approved` or `rejected`, but it gave
the table nowhere to record *who* decided or *when*. A disposition with no
named reviewer is not a governance record, so this revision adds the reviewer,
the decision time and an optional reviewer rationale, and constrains them to
agree with `review_status` in storage rather than only in service code.

`reason` stays the *import's* rationale for proposing the pair and remains an
imported fact that a re-import compares. `review_note` is the reviewer's own
rationale for the disposition and is never compared on re-import.

Revision ID: 20260916_0059
Revises: 20260915_0058
Create Date: 2026-09-16 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260916_0059"
down_revision: Union[str, None] = "20260915_0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# An undecided row carries no reviewer, no decision time and no note; a
# decided row carries a reviewer and a decision time. Stated here so that a
# raw SQL `UPDATE ... SET review_status='approved'` -- the only way a
# disposition could be recorded before this revision -- fails closed instead
# of producing an unattributable decision.
_DISPOSITION_ATTRIBUTED = (
    "(review_status in ('initial_broader', 'needs_review')"
    " and reviewed_by_user_id is null and reviewed_at is null and review_note is null)"
    " or (review_status in ('approved', 'rejected')"
    " and reviewed_by_user_id is not null and reviewed_at is not null)"
)


def upgrade() -> None:
    op.add_column(
        "ics_domain_crosswalks",
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ics_domain_crosswalks",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ics_domain_crosswalks",
        sa.Column("review_note", sa.Text(), nullable=True),
    )
    # The reviewer outlives neither the decision nor the row: a deleted user
    # leaves the disposition standing but unattributed, which is why the
    # attribution check below tolerates a null reviewer only for undecided
    # rows and SET NULL is paired with an explicit historical caveat in the
    # runbook rather than with RESTRICT on a people table.
    op.create_foreign_key(
        "fk_ics_domain_crosswalks_reviewed_by_user_id_users",
        "ics_domain_crosswalks",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "review_note",
        "ics_domain_crosswalks",
        "review_note is null or (btrim(review_note) <> '' and char_length(review_note) <= 4000)",
    )
    op.create_check_constraint(
        "disposition_attributed",
        "ics_domain_crosswalks",
        _DISPOSITION_ATTRIBUTED,
    )
    op.create_index(
        "ix_ics_domain_crosswalks_import_review_status",
        "ics_domain_crosswalks",
        ["import_id", "review_status"],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE ics_domain_crosswalks IN EXCLUSIVE MODE")
    decided = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM ics_domain_crosswalks "
            "WHERE review_status in ('approved', 'rejected')"
        )
    ).scalar()
    if decided:
        raise RuntimeError("Cannot discard recorded crosswalk review attribution")

    op.drop_index(
        "ix_ics_domain_crosswalks_import_review_status",
        table_name="ics_domain_crosswalks",
    )
    op.drop_constraint(
        op.f("ck_ics_domain_crosswalks_disposition_attributed"),
        "ics_domain_crosswalks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ics_domain_crosswalks_review_note"),
        "ics_domain_crosswalks",
        type_="check",
    )
    op.drop_constraint(
        "fk_ics_domain_crosswalks_reviewed_by_user_id_users",
        "ics_domain_crosswalks",
        type_="foreignkey",
    )
    op.drop_column("ics_domain_crosswalks", "review_note")
    op.drop_column("ics_domain_crosswalks", "reviewed_at")
    op.drop_column("ics_domain_crosswalks", "reviewed_by_user_id")
