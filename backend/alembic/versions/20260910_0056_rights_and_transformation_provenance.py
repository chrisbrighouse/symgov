"""add durable rights records and asset transformation lineage

Revision ID: 20260910_0056
Revises: 20260910_0055
Create Date: 2026-09-10 00:00:00.000000

SM-P0-06 of the Semantic Model & Classification Change Specification: rights
and transformation provenance. Section 15.1 asks to "persist durable rights
disposition and transformation lineage or extend existing durable rights
model", and section 7.12 adds that "if an equivalent durable rights entity
already exists elsewhere in the codebase, extend/reuse it rather than
duplicate it".

**No such entity exists, so this migration creates one.** The candidate was
`provenance_assessments`, and it fails on three counts, each independently
disqualifying:

* Both of its foreign keys -- `queue_item_id` and `intake_record_id` -- are
  NOT NULL and intake-scoped. There is no way to attach a rights decision to a
  governed symbol revision, a source package or a standard edition, which is
  exactly what authoritative ingestion needs. It records a review-time
  assessment of one submission.
* Neither of its vocabularies is section 7.12's. Its deployed
  `rights_disposition` enumeration is `cleared | unknown_warning | restricted
  | conflict | failed`, which shares not one value with section 7.12's
  `display | distribute | transform | compare_only | metadata_only | reject`.
* It is written by the live intake pipeline (`runtime.py`,
  `tracy_operations.py`, `automation_policy.py`, `publication_handoff.py`,
  `workspace.py`, `routes/workspace.py`). Repurposing it would mean changing
  an existing write path, which section 12.2 and SM-P0-09 reserve.

`hannah_photo_candidates.rights_status` is candidate-scoped and equally
unsuitable. Nothing in either table changes here, in either direction.

Purely additive: two new tables, no column added to, altered in or dropped
from any existing table. `source_packages.licence_reference` (SM-P0-05) stays
exactly as it is and is the forward *reference*; `rights_records` points back
at the package rather than the package pointing at the record, so no
pre-existing table is touched at all.

Two entities, matching how SM-P0-03 and SM-P0-05 split things:

`rights_records` carries section 7.12's rights status, disposition, licence
reference and governed decision. It is a *subject*-anchored assertion with
exactly one of three subjects set -- a source package, a standard edition or
a symbol revision:

* A source package is the primary anchor. It is the acquisition envelope
  (section 7.11), and `licence_reference` already sits there.
* A standard edition needs its own: whether SymGov may derive a symbol from
  one edition of a standard is a rights question about the standard, not
  about any package, and 20260910_0055 seeded 238 editions with no package
  behind them.
* A symbol revision needs its own because section 9.2's publication gate is
  per-symbol, and a redrawn asset's disposition can legitimately differ from
  its source's.

A single table with three nullable subjects rather than three tables, because
unlike SM-P0-04's two classification-assignment tables -- which carry
different role vocabularies -- the record shape here is identical for all
three subjects, and three copies of one governed decision is three places for
the vocabulary to drift. `subject_exactly_one` is spelled as a sum of `case`
expressions so it is an integer comparison that can never evaluate to NULL.

`asset_transformations` carries section 7.12's "source asset -> transformation
tool/version -> derived asset" as one row per *step*, ordered by
`step_index` within a symbol revision, so the specification's chain is
representable and not just its endpoints. One table, not two: a lineage
header would carry no field of its own -- the chain is fully described by
`(symbol_revision_id, step_index)` -- and would exist only to hold an id.
Chain *continuity* (each step starting from the previous step's derived
digest) cannot be expressed in a check constraint, which is a per-row
predicate, so it is enforced in `rights_provenance.record_asset_transformation`
and covered by tests; the database enforces everything that is per-row.

The two tables are deliberately not linked by a foreign key. Whether a
transformation was permitted depends on the source subject's approved
disposition including `transform`, and that judgement is section 9.2's
publication gate -- SM-P0-08, not this package. Joining them is a reporting
concern, and `rights_provenance.symbol_revision_rights_provenance` does it
read-only.

Deviations from the specification's literal field list, recorded deliberately
rather than slipped in:

* `determination_method` is the **sixth** `method` vocabulary in this model
  and is deliberately not unified with the other five. Section 8.4 requires
  an AI assertion to be stored as a proposal carrying its method, and without
  this column an agent-proposed rights record would be indistinguishable from
  a reviewer's. Its values are `manual | licence_document | ai_assisted`;
  `licence_document` appears in no other vocabulary.
* `decision_reason` exists because section 7.12 asks for the "why" of an
  approval, and no other table in this model requires one. It is required for
  `approved` and optional otherwise.
* `step_index` exists because section 7.12 asks for the chain. Without an
  order, two transformation rows for one revision are an unordered set.

One rule this migration deliberately does **not** carry. A permissive
disposition (`display`, `distribute`, `transform`, `compare_only`) may only be
*approved* when the rights status is `open`, `licensed` or `restricted`;
approving distribution of an asset whose rights are `unknown`, `prohibited` or
`expired` is the failure section 9.2's rights dimension and section 16.2's "no
public authoritative-source symbol is newly published with unresolved rights"
exist to prevent. It was written as a check constraint and then moved to
service policy on review: rights *gating* is SM-P0-08's, and a storage-level
rule would put a second gate in a second place, where a later policy change
would need a migration to loosen it. It now lives in
`rights_provenance.disposition_is_permitted`, and
`tests/test_rights_provenance.py` is its only enforcement -- the same shape as
`standard_sources.STANDARD_STATUSES`, which section 7.10 likewise leaves out
of the database. `metadata_only` and `reject` remain available on every
status either way, so an orphan work can be recorded honestly.

Two things this migration does *not* do. It copies no licence text: section
7.12 is explicit that this is "a reference to evidence, not copied
copyrighted terms", and `licence_reference` carries SM-P0-05's 512-character
bound for that reason. And it adds no second disposition vocabulary to
`source_packages`.

`decided_by_user_id` is the one foreign key in the semantic model with
`ondelete="RESTRICT"` rather than `SET NULL`. Section 14.4 requires the
governance decision history to be "retained with the governed data", and an
approved rights record that has lost its approver is exactly the record that
must not exist -- `decision_actor` would refuse the NULL anyway, so RESTRICT
reports the real reason instead of a check violation on an unrelated row.
`proposed_by_user_id` and `recorded_by_user_id` stay `SET NULL`: neither is a
governance decision.

Identifier lengths: every foreign key and index is named explicitly and
short. The convention's own name for `asset_transformations.source_package_-`
`entry_id` would be `fk_asset_transformations_source_package_entry_id_source_`
`package_entries` at 72 characters and would be rejected outright. Check
constraints are given *bare* names and left to `NAMING_CONVENTION` in
`models/base.py` to prefix -- an already-prefixed name gets double-prefixed
and then silently hash-truncated, which is the defect 20260909_0050 and
20260909_0053 spent two migrations repairing. The longest effective name here
is `ck_asset_transformations_transformation_changed_asset` at 53.

JSONB bounds are spelled with builtins only (`jsonb_typeof`). A depth or size
bound written with `stage4_jsonb_max_depth` or `stage4_string_array_bounds`
would break `Base.metadata.create_all()`, which is what
`INTENTIONAL_ORM_EXPRESSION_DIVERGENCE` exists to record and what
`tests/test_f0_4_review_without_unpublication.py` calls.

Reversibility: `downgrade()` drops both tables and their indexes and touches
nothing else, because `upgrade()` creates nothing else. No pre-existing
constraint or index is renamed or replaced, so no earlier migration's
`downgrade()` depends on anything here -- unlike 20260910_0054, which had to
restore 20260409_0001's index. The chain still cannot be rolled back below
20260720_0023, which raises deliberately.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260910_0056"
down_revision: Union[str, None] = "20260910_0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Section 7.12, verbatim and in the order the specification tabulates them.
# Section 13.1's "Rights" traceability dimension names the same six.
_RIGHTS_STATUSES = ("unknown", "open", "licensed", "restricted", "prohibited", "expired")

# Section 7.12, verbatim. Shares no value with the deployed
# `provenance_assessments.rights_disposition` enumeration
# (`cleared | unknown_warning | restricted | conflict | failed`), which is one
# of the three reasons that table could not become the durable record.
_RIGHTS_DISPOSITIONS = (
    "display",
    "distribute",
    "transform",
    "compare_only",
    "metadata_only",
    "reject",
)

# The statuses that assert someone granted terms, and therefore cannot be
# approved without a reference to them.
_LICENCE_BACKED_STATUSES = ("licensed", "restricted")

# The governance lifecycle. `approved` rather than the `verified` the other
# five governed tables use: section 7.12's own word is "approved", and section
# 13.1's Governance dimension reads "organisation approved | public approved".
# A rights disposition is a legal approval, not a verification of fact.
_DECISION_STATUSES = ("proposed", "approved", "rejected", "retired")

# The sixth `method` vocabulary in this model, distinct from all five before
# it on purpose. See the docstring.
_DETERMINATION_METHODS = ("manual", "licence_document", "ai_assisted")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    _create_rights_records()
    _create_asset_transformations()


def _create_rights_records() -> None:
    op.create_table(
        "rights_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # Exactly one of the three subjects is set; `subject_exactly_one`
        # below enforces it. RESTRICT in every direction: a rights decision
        # must not disappear because its subject was deleted out from under
        # it, and section 14.4 keeps the decision history with the governed
        # data.
        sa.Column(
            "source_package_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_packages.id", ondelete="RESTRICT", name="fk_rights_records_source_package_id"),
            nullable=True,
        ),
        sa.Column(
            "standard_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("standard_versions.id", ondelete="RESTRICT", name="fk_rights_records_standard_version_id"),
            nullable=True,
        ),
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_rights_records_symbol_revision_id"),
            nullable=True,
        ),
        sa.Column("rights_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("disposition", sa.Text(), nullable=False),
        # Section 7.12: "a reference to terms/contract/rights record; not the
        # licence text itself". The 512-character bound is SM-P0-05's, on
        # `source_packages.licence_reference`, and is what keeps it a
        # reference.
        sa.Column("licence_reference", sa.Text(), nullable=True),
        sa.Column("determination_method", sa.Text(), nullable=False),
        sa.Column("decision_status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        # Section 7.12's who / when / why.
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT", name="fk_rights_records_decided_by_user_id"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_rights_records_proposed_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rights_records"),
        # A sum of `case` expressions rather than a chain of `and`/`or` over
        # nullable columns: the comparison is between two integers, so it is
        # true or false and can never evaluate to NULL. PostgreSQL accepts a
        # check constraint that evaluates to NULL, which is the trap that bit
        # SM-P0-03's checksum pairing and SM-P0-04's parent check.
        sa.CheckConstraint(
            "(case when source_package_id is null then 0 else 1 end "
            "+ case when standard_version_id is null then 0 else 1 end "
            "+ case when symbol_revision_id is null then 0 else 1 end) = 1",
            name="subject_exactly_one",
        ),
        sa.CheckConstraint(
            f"rights_status in ({_quoted(_RIGHTS_STATUSES)})",
            name="rights_status",
        ),
        sa.CheckConstraint(
            f"disposition in ({_quoted(_RIGHTS_DISPOSITIONS)})",
            name="disposition",
        ),
        sa.CheckConstraint(
            f"determination_method in ({_quoted(_DETERMINATION_METHODS)})",
            name="determination_method",
        ),
        sa.CheckConstraint(
            f"decision_status in ({_quoted(_DECISION_STATUSES)})",
            name="decision_status",
        ),
        sa.CheckConstraint(
            "licence_reference is null or (btrim(licence_reference) <> '' "
            "and char_length(licence_reference) <= 512)",
            name="licence_reference",
        ),
        sa.CheckConstraint(
            "decision_reason is null or (btrim(decision_reason) <> '' "
            "and char_length(decision_reason) <= 2000)",
            name="decision_reason",
        ),
        # Section 7.12's who and when. `retired` is excluded because
        # supersession retires a predecessor automatically when its successor
        # is approved, exactly as in SM-P0-02 through -05; the actor of record
        # is the successor's approver. `decision_status` is NOT NULL, so
        # neither branch can be NULL.
        sa.CheckConstraint(
            "decision_status in ('proposed', 'retired') "
            "or (decided_at is not null and decided_by_user_id is not null)",
            name="decision_actor",
        ),
        # Section 7.12's why. No other table in this model requires a reason;
        # a rights approval does, because it is the one decision whose
        # justification is not reconstructible from the row.
        sa.CheckConstraint(
            "decision_status <> 'approved' or decision_reason is not null",
            name="approved_reason",
        ),
        # Section 8.4: an AI assertion is a proposal. Unlike section 7.10's
        # verification, there is no controlled-system exception here -- no
        # deterministic reading of a licence is a rights decision.
        sa.CheckConstraint(
            "decision_status <> 'approved' or determination_method <> 'ai_assisted'",
            name="approved_not_ai_determined",
        ),
        # Asserting terms were granted, with no reference to them, is
        # unverifiable. Gated on `approved` rather than on the status alone so
        # a reviewer can still *propose* "this looks licensed" before the
        # contract has been found.
        sa.CheckConstraint(
            "decision_status <> 'approved' "
            f"or rights_status not in ({_quoted(_LICENCE_BACKED_STATUSES)}) "
            "or licence_reference is not null",
            name="approved_licence_reference",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
    )

    # One *approved* rights record per subject. Proposals stay unconstrained
    # so competing candidates can sit side by side for review, and approving
    # a successor retires its predecessor rather than being refused -- the
    # supersession shape SM-P0-01 through -05 all use. Each index is partial
    # on its own subject being present as well as on the status: a unique
    # index over a nullable column would otherwise admit every NULL row,
    # which is true but reads as though it were being relied on.
    op.create_index(
        "uq_rights_records_approved_source_package",
        "rights_records",
        ["source_package_id"],
        unique=True,
        postgresql_where=sa.text("decision_status = 'approved' and source_package_id is not null"),
    )
    op.create_index(
        "uq_rights_records_approved_standard_version",
        "rights_records",
        ["standard_version_id"],
        unique=True,
        postgresql_where=sa.text("decision_status = 'approved' and standard_version_id is not null"),
    )
    op.create_index(
        "uq_rights_records_approved_symbol_revision",
        "rights_records",
        ["symbol_revision_id"],
        unique=True,
        postgresql_where=sa.text("decision_status = 'approved' and symbol_revision_id is not null"),
    )
    # Section 14.3's shape for assignment tables -- index by target. Partial
    # in each case, because two thirds of the rows have a NULL in any given
    # subject column. These are also the indexes the RESTRICT foreign keys
    # need for a delete of a subject to be checked cheaply.
    op.create_index(
        "ix_rights_records_source_package_id",
        "rights_records",
        ["source_package_id", "decision_status"],
        postgresql_where=sa.text("source_package_id is not null"),
    )
    op.create_index(
        "ix_rights_records_standard_version_id",
        "rights_records",
        ["standard_version_id", "decision_status"],
        postgresql_where=sa.text("standard_version_id is not null"),
    )
    op.create_index(
        "ix_rights_records_symbol_revision_id",
        "rights_records",
        ["symbol_revision_id", "decision_status"],
        postgresql_where=sa.text("symbol_revision_id is not null"),
    )


def _create_asset_transformations() -> None:
    op.create_table(
        "asset_transformations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        # The derived asset belongs to a symbol revision, which is what makes
        # section 16.1's "traced to ... hashes" query answerable from one
        # index. NOT NULL: a transformation with no derived asset owner is
        # lineage for nothing.
        sa.Column(
            "symbol_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("symbol_revisions.id", ondelete="RESTRICT", name="fk_asset_transformations_symbol_revision_id"),
            nullable=False,
        ),
        # Position in the chain, from 1. Section 7.12 asks for "source asset
        # -> transformation tool/version -> derived asset", which is a chain
        # and not a set; without an order, two rows for one revision cannot
        # be read as successive steps.
        sa.Column("step_index", sa.Integer(), nullable=False),
        # The acquired asset this step started from, where it is inside a
        # source package. `source_package_entries.original_asset_sha256` and
        # `source_path` (SM-P0-05) are joined to rather than duplicated.
        sa.Column(
            "source_package_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "source_package_entries.id",
                ondelete="RESTRICT",
                name="fk_asset_transformations_source_package_entry_id",
            ),
            nullable=True,
        ),
        # Section 7.12: "SHA-256 for source and derived assets where
        # available". The source digest is genuinely sometimes unavailable --
        # a licensed source that may not be stored -- so it is nullable, and
        # `source_asset_identified` requires the entry instead in that case.
        sa.Column("source_asset_sha256", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("tool_version", sa.Text(), nullable=False),
        # NOT NULL. Section 9.2's integrity minimum is "at least the final
        # stored asset hash", and SymGov produced this asset, so its digest is
        # always available.
        sa.Column("derived_asset_sha256", sa.Text(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "recorded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_asset_transformations_recorded_by_user_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_asset_transformations"),
        sa.CheckConstraint("step_index >= 1", name="step_index"),
        sa.CheckConstraint(
            "btrim(tool_name) <> '' and char_length(tool_name) <= 128",
            name="tool_name",
        ),
        sa.CheckConstraint(
            "btrim(tool_version) <> '' and char_length(tool_version) <= 64",
            name="tool_version",
        ),
        # Sections 7.10, 7.11 and 7.12 all name SHA-256 specifically, so a
        # fixed 64-character grammar and no algorithm column. Deliberately
        # not SM-P0-03's algorithm-paired `^[0-9a-f]{32,128}$`, which would
        # admit a 32-character MD5 digest.
        sa.CheckConstraint(
            "source_asset_sha256 is null or source_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="source_asset_sha256",
        ),
        sa.CheckConstraint(
            "derived_asset_sha256 ~ '^[0-9a-f]{64}$'",
            name="derived_asset_sha256",
        ),
        # A derived asset with no identified source is not lineage. Either
        # the source digest or the package entry it came from must be
        # present. Both branches test a column against NULL explicitly, so
        # the constraint is always true or false.
        sa.CheckConstraint(
            "source_asset_sha256 is not null or source_package_entry_id is not null",
            name="source_asset_identified",
        ),
        # A step whose output is byte-identical to its input transformed
        # nothing, and inserting one would make the chain unorderable by
        # digest.
        sa.CheckConstraint(
            "source_asset_sha256 is null or derived_asset_sha256 <> source_asset_sha256",
            name="transformation_changed_asset",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_json) = 'object'",
            name="evidence_json_object",
        ),
    )

    # One step per position per revision. This is what makes the chain a
    # chain; continuity between steps is a cross-row property and is enforced
    # in `rights_provenance.record_asset_transformation`.
    op.create_index(
        "uq_asset_transformations_revision_step",
        "asset_transformations",
        ["symbol_revision_id", "step_index"],
        unique=True,
    )
    # Which symbol a stored asset digest belongs to -- section 13.3's
    # lineage reporting, and a signal section 10.2's duplicate detection can
    # use without a name comparison.
    op.create_index(
        "ix_asset_transformations_derived_asset_sha256",
        "asset_transformations",
        ["derived_asset_sha256"],
    )
    # And the reverse: everything derived from one acquired asset.
    op.create_index(
        "ix_asset_transformations_source_asset_sha256",
        "asset_transformations",
        ["source_asset_sha256"],
        postgresql_where=sa.text("source_asset_sha256 is not null"),
    )
    # The index the RESTRICT foreign key needs, and the join back to the
    # exact provider entry Appendix B.2 asks for.
    op.create_index(
        "ix_asset_transformations_source_package_entry_id",
        "asset_transformations",
        ["source_package_entry_id"],
        postgresql_where=sa.text("source_package_entry_id is not null"),
    )


def downgrade() -> None:
    """Drop exactly what upgrade() created, and nothing else.

    No pre-existing constraint or index is renamed or replaced by this
    migration, so unlike 20260910_0054 there is no earlier `downgrade()` whose
    named object has to be restored. Both tables are new, so dropping them
    cannot orphan a column an earlier migration created.
    """
    op.drop_index("ix_asset_transformations_source_package_entry_id", table_name="asset_transformations")
    op.drop_index("ix_asset_transformations_source_asset_sha256", table_name="asset_transformations")
    op.drop_index("ix_asset_transformations_derived_asset_sha256", table_name="asset_transformations")
    op.drop_index("uq_asset_transformations_revision_step", table_name="asset_transformations")
    op.drop_table("asset_transformations")

    op.drop_index("ix_rights_records_symbol_revision_id", table_name="rights_records")
    op.drop_index("ix_rights_records_standard_version_id", table_name="rights_records")
    op.drop_index("ix_rights_records_source_package_id", table_name="rights_records")
    op.drop_index("uq_rights_records_approved_symbol_revision", table_name="rights_records")
    op.drop_index("uq_rights_records_approved_standard_version", table_name="rights_records")
    op.drop_index("uq_rights_records_approved_source_package", table_name="rights_records")
    op.drop_table("rights_records")
