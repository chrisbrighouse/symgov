"""add publication gate evaluations and dimension exceptions

Revision ID: 20260911_0057
Revises: 20260910_0056
Create Date: 2026-09-11 00:00:00.000000

SM-P0-08 of the Semantic Model & Classification Change Specification: the
minimum semantic/source/rights publication gate of section 9.2. Section 17
scopes it -- "apply to new authoritative ingestion profiles; grandfather
current public data" -- and section 12.1's phase M6 says the same thing as a
migration phase.

**What the gate is applied to, and why nothing today is caught by it.**
A symbol revision is in scope when it reaches a `source_packages` row whose
`package_type` is `authoritative_library` -- SM-P0-05's
`source_package_acquisition.AUTHORITATIVE_PACKAGE_TYPE`, which
`register_source_package` already takes as its default. Every source package
in production is created by `runtime.ensure_source_package_for_intake` with
`package_type='submission_sheet'`; `register_source_package` is called from
tests only. So the grandfathering section 17 asks for is a property of the
data rather than a cutoff date, no backfill flag is needed, and a connector
that registers an authoritative library is gated from its first symbol
without having to opt in.

**This is not `automation_policy.evaluate_publication_automation_gate`.**
That function is live, is also called a publication gate, and is a different
gate on a different path: it decides whether a symbol may skip human review
and go straight to Rupert, and it reads `provenance_assessments`, whose
rights vocabulary shares not one value with `rights_records`' (the finding
20260910_0056 records). The two do not compose and neither calls the other.
`publication_gate.py` says so in prose and
`tests/test_publication_gate.py` pins the vocabularies apart.

Two tables, purely additive: no column is added to, altered in or dropped
from any existing table, so every fixture revision constant pinned below head
still opens a `Session` against models this migration does not touch.

`publication_gate_evaluations` is the durable record of one evaluation. The
decision is not only returned to the caller: section 14.4 keeps governance
decisions "with the governed data" and explicitly refuses to rely on
short-lived operational telemetry, so a refusal has to survive the request
that produced it. Rows accumulate rather than being replaced -- re-evaluating
a revision records a new row -- because why a symbol was refused in March is
part of the history section 14.4 retains, and because section 13.3's coverage
reporting is a query over exactly this table.

`outcome` has three values, not two. `not_in_scope` is the grandfathered
case, and it is a recorded outcome rather than an absence so that section
12.1's "existing published symbols grandfathered with traceability gaps
reported" is answerable: the row still carries all six dimension results and
the derived traceability level, and says the symbol published because it was
out of scope rather than because it passed.

`publication_gate_exceptions` is section 9.2's "explicit approved 'semantic
identity pending' exception for non-engineering/annotation symbols". It is a
governed row and not a flag or a policy constant because section 9.2's word
is "approved": a waiver of a publication requirement is a decision, and a
decision needs the who / when / why that section 7.12 requires of a rights
approval. `approved_by_user_id` is therefore `ondelete="RESTRICT"`, the same
choice and the same reason as `rights_records.decided_by_user_id`.

`dimension` is constrained to section 9.2's six names -- a vocabulary, which
is what this model puts in the database. *Which* of the six may be waived is
service policy in `publication_gate.WAIVABLE_DIMENSIONS` (today:
`semantic_identity` alone, the only exception section 9.2 offers), pinned by
`tests/test_publication_gate.py`. This is deliberately the same split
20260910_0056 settled on for `disposition_is_permitted`: the vocabulary is
storage, the policy is service, and loosening a policy must not require a
migration. A row that waives `rights` is therefore storable and unwritable,
and section 16.2's "no public authoritative-source symbol is newly published
with unresolved rights" is enforced by
`propose_publication_gate_exception` and by tests, not by a check constraint.

Three-valued logic: every check constraint here compares values that cannot
be NULL. `scope_matches_outcome` compares two booleans (`in_scope` is NOT
NULL; `outcome <> 'not_in_scope'` is NOT NULL because `outcome` is). The
refusal-reason constraints are guarded on `outcome`, which is NOT NULL, and
`refusal_reasons_json` is NOT NULL with a server default. `decision_actor`
and `approved_reason` mirror 20260910_0056's, which were written for the same
trap.

JSONB bounds use builtins only (`jsonb_typeof`, `jsonb_array_length`), so
`Base.metadata.create_all()` keeps working.

Identifier lengths: every foreign key and index is named explicitly. The
longest name created here is
`fk_publication_gate_evaluations_evaluated_by_user_id` at 52 characters,
against PostgreSQL's limit of 63. Check constraints are given **bare** names
and left to `NAMING_CONVENTION` to prefix -- an already-prefixed name is
double-prefixed and then silently hash-truncated, the defect 20260909_0050
and 20260909_0053 spent two migrations repairing. The longest effective check
name here is `ck_publication_gate_evaluations_dimension_results_complete` at
57.

Reversibility: `downgrade()` drops both tables and their indexes and touches
nothing else, because `upgrade()` creates nothing else. No pre-existing
constraint or index is renamed or replaced, so no earlier migration's
`downgrade()` depends on anything here. The chain still cannot be rolled back
below 20260720_0023, which raises deliberately.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260911_0057"
down_revision: Union[str, None] = "20260910_0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Section 9.2's six dimensions, verbatim and in the order the specification
# tabulates them. The snake_case spelling is this model's; the words are the
# specification's.
_GATE_DIMENSIONS = (
    "semantic_identity",
    "source",
    "graphical_authority",
    "rights",
    "integrity",
    "classification",
)

# What one evaluation concluded. `not_in_scope` is the section 17
# grandfathering case, recorded rather than omitted -- see the docstring.
_GATE_OUTCOMES = ("permitted", "refused", "not_in_scope")

# Section 13.2's derived traceability level. Stored because the gate computes
# every dimension it needs anyway, and a refusal without the level it reached
# loses the evidence. The *reporting* built on it is SM-P1-06.
_TRACEABILITY_LEVELS = ("T0", "T1", "T2", "T3", "T4", "T5")

# The governance lifecycle, the shape SM-P0-02 through -06 all use. A waiver
# uses `approved` rather than `verified` for 20260910_0056's reason: excusing
# a publication requirement is an approval, not a verification of fact.
_DECISION_STATUSES = ("proposed", "approved", "rejected", "retired")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    _create_publication_gate_exceptions()
    _create_publication_gate_evaluations()


def _create_publication_gate_exceptions() -> None:
    op.create_table(
        "publication_gate_exceptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # RESTRICT: a waiver must not disappear because its subject was
        # deleted out from under it. Section 14.4 keeps the decision history
        # with the governed data.
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "symbol_revisions.id",
                ondelete="RESTRICT",
                name="fk_publication_gate_exceptions_symbol_revision_id",
            ),
            nullable=False,
        ),
        # Which of section 9.2's six this waiver excuses. The column carries
        # the vocabulary; which values may actually be waived is service
        # policy -- see the docstring.
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("decision_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        # Section 9.2's "explicit approved": the who / when / why, the same
        # triple section 7.12 requires of a rights approval.
        sa.Column(
            "approved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="RESTRICT",
                name="fk_publication_gate_exceptions_approved_by_user_id",
            ),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_reason", sa.Text(), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_publication_gate_exceptions_proposed_by_user_id",
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_publication_gate_exceptions"),
        sa.CheckConstraint(
            f"dimension in ({_quoted(_GATE_DIMENSIONS)})",
            name="dimension",
        ),
        sa.CheckConstraint(
            f"decision_status in ({_quoted(_DECISION_STATUSES)})",
            name="decision_status",
        ),
        # `retired` is excluded for 20260910_0056's reason: supersession
        # retires a predecessor automatically when its successor is approved,
        # and the actor of record is the successor's approver.
        # `decision_status` is NOT NULL, so neither branch can be NULL.
        sa.CheckConstraint(
            "decision_status in ('proposed', 'retired') "
            "or (approved_at is not null and approved_by_user_id is not null)",
            name="decision_actor",
        ),
        # Section 9.2's exception is for "non-engineering/annotation symbols",
        # which is a judgement about this symbol. A waiver that does not say
        # why is not an explicit exception.
        sa.CheckConstraint(
            "decision_status <> 'approved' or approval_reason is not null",
            name="approved_reason",
        ),
        sa.CheckConstraint(
            "approval_reason is null or (btrim(approval_reason) <> '' "
            "and char_length(approval_reason) <= 2000)",
            name="approval_reason_bounds",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
    )

    # One *approved* waiver per revision and dimension. Proposals stay
    # unconstrained so competing candidates can sit side by side for review,
    # and approving a successor retires its predecessor rather than being
    # refused -- the supersession shape SM-P0-01 through -06 all use.
    op.create_index(
        "uq_publication_gate_exceptions_approved",
        "publication_gate_exceptions",
        ["symbol_revision_id", "dimension"],
        unique=True,
        postgresql_where=sa.text("decision_status = 'approved'"),
    )
    # The lookup the gate itself performs, and the index the RESTRICT foreign
    # key needs for a revision delete to be checked cheaply.
    op.create_index(
        "ix_publication_gate_exceptions_symbol_revision_id",
        "publication_gate_exceptions",
        ["symbol_revision_id", "decision_status"],
    )


def _create_publication_gate_evaluations() -> None:
    op.create_table(
        "publication_gate_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "symbol_revisions.id",
                ondelete="RESTRICT",
                name="fk_publication_gate_evaluations_symbol_revision_id",
            ),
            nullable=False,
        ),
        # The authoritative package that put this revision in scope. NULL
        # exactly when the revision was out of scope, which
        # `scope_requires_package` below enforces.
        sa.Column(
            "source_package_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "source_packages.id",
                ondelete="RESTRICT",
                name="fk_publication_gate_evaluations_source_package_id",
            ),
            nullable=True,
        ),
        sa.Column("in_scope", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("traceability_level", sa.Text(), nullable=False),
        # One entry per section 9.2 dimension, always all six -- a partial
        # evaluation is not a gate decision, and `dimension_results_complete`
        # makes that a storage guarantee rather than a convention.
        sa.Column("dimension_results_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "refusal_reasons_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Section 7.13: a persisted traceability snapshot "should store the
        # dimensions and policy version used". The same applies to a gate
        # decision -- a refusal under a policy nobody can name is not
        # auditable.
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        # SET NULL, not RESTRICT: an evaluation is a machine reading of the
        # governed rows, not a governance decision by this actor. The
        # decisions it reads carry their own RESTRICT approvers.
        sa.Column(
            "evaluated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_publication_gate_evaluations_evaluated_by_user_id",
            ),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_publication_gate_evaluations"),
        sa.CheckConstraint(
            f"outcome in ({_quoted(_GATE_OUTCOMES)})",
            name="outcome",
        ),
        sa.CheckConstraint(
            f"traceability_level in ({_quoted(_TRACEABILITY_LEVELS)})",
            name="traceability_level",
        ),
        # Boolean on both sides of the equality: `in_scope` is NOT NULL and
        # `outcome` is NOT NULL, so this can never evaluate to NULL.
        sa.CheckConstraint(
            "in_scope = (outcome <> 'not_in_scope')",
            name="scope_matches_outcome",
        ),
        # Being in scope means a package put it there.
        sa.CheckConstraint(
            "in_scope = false or source_package_id is not null",
            name="scope_requires_package",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dimension_results_json) = 'array'",
            name="dimension_results_array",
        ),
        # Section 9.2 has six dimensions and the gate reports all of them
        # whatever the outcome, including for a grandfathered symbol, so that
        # section 12.1's "traceability gaps reported" has something to read.
        sa.CheckConstraint(
            "jsonb_array_length(dimension_results_json) = 6",
            name="dimension_results_complete",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(refusal_reasons_json) = 'array'",
            name="refusal_reasons_array",
        ),
        # A refusal that names no reason is not reviewable, and a permit that
        # names one is a contradiction. Both branches are guarded on
        # `outcome`, which is NOT NULL. `not_in_scope` is constrained by
        # neither: a grandfathered symbol publishes while still carrying the
        # reasons it would have failed on, which is the report section 12.1
        # asks for.
        sa.CheckConstraint(
            "outcome <> 'refused' or jsonb_array_length(refusal_reasons_json) > 0",
            name="refusal_names_a_reason",
        ),
        sa.CheckConstraint(
            "outcome <> 'permitted' or jsonb_array_length(refusal_reasons_json) = 0",
            name="permit_names_no_reason",
        ),
        sa.CheckConstraint(
            "btrim(policy_version) <> '' and char_length(policy_version) <= 128",
            name="policy_version",
        ),
    )

    # Section 13.3's coverage reporting reads this table by revision, newest
    # first. Also the index the RESTRICT foreign key needs.
    op.create_index(
        "ix_publication_gate_evaluations_symbol_revision_id",
        "publication_gate_evaluations",
        ["symbol_revision_id", "evaluated_at"],
    )
    # "Percentage of public symbols with ..." queries filter by outcome over
    # a date range; partial on the in-scope rows, which are the only ones a
    # gate-coverage report is about.
    op.create_index(
        "ix_publication_gate_evaluations_outcome",
        "publication_gate_evaluations",
        ["outcome", "evaluated_at"],
        postgresql_where=sa.text("in_scope"),
    )
    op.create_index(
        "ix_publication_gate_evaluations_source_package_id",
        "publication_gate_evaluations",
        ["source_package_id"],
        postgresql_where=sa.text("source_package_id is not null"),
    )


def downgrade() -> None:
    """Drop exactly what upgrade() created, and nothing else.

    No pre-existing constraint or index is renamed or replaced by this
    migration, so unlike 20260910_0054 there is no earlier `downgrade()` whose
    named object has to be restored. Both tables are new, so dropping them
    cannot orphan a column an earlier migration created.
    """
    op.drop_index(
        "ix_publication_gate_evaluations_source_package_id",
        table_name="publication_gate_evaluations",
    )
    op.drop_index("ix_publication_gate_evaluations_outcome", table_name="publication_gate_evaluations")
    op.drop_index(
        "ix_publication_gate_evaluations_symbol_revision_id",
        table_name="publication_gate_evaluations",
    )
    op.drop_table("publication_gate_evaluations")

    op.drop_index(
        "ix_publication_gate_exceptions_symbol_revision_id",
        table_name="publication_gate_exceptions",
    )
    op.drop_index("uq_publication_gate_exceptions_approved", table_name="publication_gate_exceptions")
    op.drop_table("publication_gate_exceptions")
